from __future__ import annotations

import asyncio
from enum import StrEnum
import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from mistralai.client.models import JSONSchema, ResponseFormat
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vibe import VIBE_ROOT
from vibe.cli.turn_summary import create_narrator_backend
from vibe.core.config import ModelConfig, VibeConfig
from vibe.core.llm.types import BackendLike
from vibe.core.logger import logger
from vibe.core.types import LLMChunk, LLMMessage, Role
from vibe.core.utils.io import read_safe

if TYPE_CHECKING:
    from vibe.core.tools.builtins.ask_user_question import Question

_PROMPT_PATH = (
    VIBE_ROOT / "core" / "prompts" / "voice" / "blocking_answer_interpreter.md"
)


class BlockingVoiceActionType(StrEnum):
    APPROVAL_ONCE = "approval_once"
    APPROVAL_ALWAYS = "approval_always"
    REJECT = "reject"
    SELECT_OPTIONS = "select_options"
    OTHER_TEXT = "other_text"
    UNCLEAR = "unclear"


class BlockingVoiceAnswerInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: BlockingVoiceActionType
    selected_option_labels: list[str] = Field(default_factory=list)
    other_text: str | None = None


class BlockingVoiceQuestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    description: str = ""


class BlockingVoiceApprovalContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_type: Literal["approval"] = "approval"
    tool_name: str
    available_actions: list[BlockingVoiceActionType]


class BlockingVoiceQuestionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_type: Literal["question"] = "question"
    question: str
    options: list[BlockingVoiceQuestionOption]
    multi_select: bool = False
    allow_other: bool = True
    available_actions: list[BlockingVoiceActionType]


BlockingVoiceContext = Annotated[
    BlockingVoiceApprovalContext | BlockingVoiceQuestionContext,
    Field(discriminator="context_type"),
]


class BlockingVoiceAnswerInterpreterPort(Protocol):
    async def interpret(
        self, *, transcript: str, context: BlockingVoiceContext
    ) -> BlockingVoiceAnswerInterpretation: ...

    async def close(self) -> None: ...


def create_blocking_voice_answer_interpreter(
    config: VibeConfig,
) -> BlockingVoiceAnswerInterpreter | None:
    if (result := create_narrator_backend(config)) is None:
        return None

    backend, model = result
    return BlockingVoiceAnswerInterpreter(backend=backend, model=model)


class BlockingVoiceAnswerInterpreter:
    def __init__(
        self,
        *,
        backend: BackendLike,
        model: ModelConfig,
        max_tokens: int = 256,
        prompt_path: Path = _PROMPT_PATH,
    ) -> None:
        self._backend = backend
        self._model = model
        self._max_tokens = max_tokens
        self._prompt_path = prompt_path

    async def interpret(
        self, *, transcript: str, context: BlockingVoiceContext
    ) -> BlockingVoiceAnswerInterpretation:
        try:
            messages = self._build_messages(transcript=transcript, context=context)
            result = await self._complete(messages)
            raw_content = (result.message.content or "").strip()
            if not raw_content:
                raise ValueError("Empty blocking voice interpretation")
            return BlockingVoiceAnswerInterpretation.model_validate_json(raw_content)
        except asyncio.CancelledError:
            raise
        except (ValidationError, ValueError, TypeError):
            logger.warning("Blocking voice interpretation parse failed", exc_info=True)
        except Exception:
            logger.warning("Blocking voice interpretation failed", exc_info=True)
        return unresolved_blocking_voice_answer()

    async def close(self) -> None:
        await self._backend.__aexit__(None, None, None)

    def _build_messages(
        self, *, transcript: str, context: BlockingVoiceContext
    ) -> list[LLMMessage]:
        prompt_text = read_safe(self._prompt_path).strip()
        context_text = json.dumps(
            context.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
        prompt_text = prompt_text.replace("{{BLOCKING_CONTEXT}}", context_text)
        prompt_text = prompt_text.replace("{{TRANSCRIPT}}", transcript.strip())
        return [LLMMessage(role=Role.system, content=prompt_text)]

    async def _complete(self, messages: list[LLMMessage]) -> LLMChunk:
        schema = BlockingVoiceAnswerInterpretation.model_json_schema()
        response_format = ResponseFormat(
            type="json_schema",
            json_schema=JSONSchema(
                name="blocking_voice_answer_interpretation", schema=schema, strict=True
            ),
        )
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


def unresolved_blocking_voice_answer() -> BlockingVoiceAnswerInterpretation:
    return BlockingVoiceAnswerInterpretation(
        action_type=BlockingVoiceActionType.UNCLEAR
    )


def build_approval_voice_context(tool_name: str) -> BlockingVoiceApprovalContext:
    return BlockingVoiceApprovalContext(
        tool_name=tool_name,
        available_actions=[
            BlockingVoiceActionType.APPROVAL_ONCE,
            BlockingVoiceActionType.APPROVAL_ALWAYS,
            BlockingVoiceActionType.REJECT,
            BlockingVoiceActionType.UNCLEAR,
        ],
    )


def build_question_voice_context(question: Question) -> BlockingVoiceQuestionContext:
    return BlockingVoiceQuestionContext(
        question=question.question,
        options=[
            BlockingVoiceQuestionOption(
                label=option.label, description=option.description
            )
            for option in question.options
        ],
        multi_select=question.multi_select,
        allow_other=not question.hide_other,
        available_actions=[
            BlockingVoiceActionType.SELECT_OPTIONS,
            *([BlockingVoiceActionType.OTHER_TEXT] if not question.hide_other else []),
            BlockingVoiceActionType.UNCLEAR,
        ],
    )
