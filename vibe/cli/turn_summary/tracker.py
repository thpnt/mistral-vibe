from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mistralai.client.models import JSONSchema, ResponseFormat
from pydantic import BaseModel

from vibe import VIBE_ROOT
from vibe.cli.turn_summary.port import (
    TurnSummaryData,
    TurnSummaryPort,
    TurnSummaryResult,
)
from vibe.core.config import ModelConfig, NarrationTone
from vibe.core.llm.types import BackendLike
from vibe.core.logger import logger
from vibe.core.types import AssistantEvent, BaseEvent, LLMChunk, LLMMessage, Role
from vibe.core.utils.io import read_safe

_PROMPTS_DIR = VIBE_ROOT / "core" / "prompts" / "voice"
_TONE_PROMPT_FILES: dict[str, Path] = {
    NarrationTone.NEUTRAL.value: (_PROMPTS_DIR / "default").with_suffix(".md"),
    NarrationTone.PROFESSIONAL.value: (_PROMPTS_DIR / "pro").with_suffix(".md"),
    NarrationTone.WARM.value: (_PROMPTS_DIR / "warm").with_suffix(".md"),
    NarrationTone.CONCISE.value: (_PROMPTS_DIR / "concise").with_suffix(".md"),
    NarrationTone.GLAZING.value: (_PROMPTS_DIR / "glazing").with_suffix(".md"),
}


class NarrationSpeech(BaseModel):
    speech_text: str


class TurnSummaryTracker(TurnSummaryPort):
    def __init__(
        self,
        backend: BackendLike,
        model: ModelConfig,
        on_summary: Callable[[TurnSummaryResult], None] | None = None,
        max_tokens: int = 512,
        tone: NarrationTone | str = NarrationTone.NEUTRAL,
    ) -> None:
        self._backend = backend
        self._model = model
        self._on_summary = on_summary
        self._max_tokens = max_tokens
        self._tone = str(tone)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._data: TurnSummaryData | None = None
        self._generation: int = 0

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def on_summary(self) -> Callable[[TurnSummaryResult], None] | None:
        return self._on_summary

    @on_summary.setter
    def on_summary(self, value: Callable[[TurnSummaryResult], None] | None) -> None:
        self._on_summary = value

    def start_turn(self, user_message: str) -> None:
        self._generation += 1
        self._data = TurnSummaryData(user_message=user_message)

    def track(self, event: BaseEvent) -> None:
        if self._data is None:
            return
        match event:
            case AssistantEvent(content=c) if c:
                self._data.assistant_fragments.append(c)

    def set_error(self, message: str) -> None:
        if self._data is not None:
            self._data.error = message

    def cancel_turn(self) -> None:
        self._data = None

    def end_turn(self) -> Callable[[], bool] | None:
        if self._data is None:
            return None
        gen = self._generation
        task = asyncio.create_task(self._generate_summary(self._data, gen))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._data = None
        return task.cancel

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _generate_summary(self, data: TurnSummaryData, gen: int) -> None:
        try:
            turn_context = self._build_turn_context(data)
            summary_messages = self._build_summary_messages(turn_context)
            summary = await self._generate_speech_summary(summary_messages)
            if self._on_summary is not None:
                self._on_summary(TurnSummaryResult(generation=gen, summary=summary))
        except Exception:
            logger.warning("Turn summary generation failed", exc_info=True)
            if self._on_summary is not None:
                self._on_summary(TurnSummaryResult(generation=gen, summary=None))

    def _build_turn_context(self, data: TurnSummaryData) -> str:
        sections: list[str] = [f"## User Request\n{data.user_message}"]

        full_text = "".join(data.assistant_fragments)
        if full_text:
            sections.append(f"## Assistant Response\n{full_text}")

        if data.error:
            sections.append(f"## Error\n{data.error}")

        return "\n\n".join(sections)

    def _build_summary_messages(self, turn_context: str) -> list[LLMMessage]:
        prompt_text = self._read_prompt_text()
        if "{{TURN_CONTEXT}}" in prompt_text:
            prompt_text = prompt_text.replace("{{TURN_CONTEXT}}", turn_context)
            return [LLMMessage(role=Role.system, content=prompt_text)]

        return [
            LLMMessage(role=Role.system, content=prompt_text),
            LLMMessage(role=Role.user, content=turn_context),
        ]

    def _read_prompt_text(self) -> str:
        prompt_path = _TONE_PROMPT_FILES.get(
            self._tone, _TONE_PROMPT_FILES[NarrationTone.NEUTRAL.value]
        )
        return read_safe(prompt_path).strip()

    async def _generate_speech_summary(self, messages: list[LLMMessage]) -> str:
        try:
            return await self._generate_structured_speech(messages)
        except Exception:
            logger.warning(
                "Structured turn summary generation failed; falling back to raw text",
                exc_info=True,
            )
        return await self._generate_raw_speech(messages)

    async def _generate_structured_speech(self, messages: list[LLMMessage]) -> str:
        schema = NarrationSpeech.model_json_schema()
        response_format = ResponseFormat(
            type="json_schema",
            json_schema=JSONSchema(name="narration_speech", schema=schema, strict=True),
        )
        result = await self._complete(messages, response_format=response_format)
        raw_content = result.message.content or ""
        return self._parse_speech_text(raw_content)

    async def _generate_raw_speech(self, messages: list[LLMMessage]) -> str:
        result = await self._complete(messages)
        raw_content = (result.message.content or "").strip()
        if not raw_content:
            raise ValueError("Empty raw narration")
        return raw_content

    async def _complete(
        self, messages: list[LLMMessage], *, response_format: Any | None = None
    ) -> LLMChunk:
        return await self._backend.complete(
            model=self._model,
            messages=messages,
            temperature=0.0,
            tools=None,
            tool_choice=None,
            max_tokens=self._max_tokens,
            extra_headers={},
            response_format=response_format,
            metadata={},
        )

    def _parse_speech_text(self, raw_content: str) -> str:
        parsed = NarrationSpeech.model_validate_json(raw_content)
        if not (speech_text := parsed.speech_text.strip()):
            raise ValueError("Empty narration speech_text")
        return speech_text
