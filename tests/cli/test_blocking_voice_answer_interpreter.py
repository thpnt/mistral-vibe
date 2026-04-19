from __future__ import annotations

from pydantic import ValidationError
import pytest

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.textual_ui.blocking_voice_answer_interpreter import (
    BlockingVoiceActionType,
    BlockingVoiceAnswerInterpretation,
    BlockingVoiceAnswerInterpreter,
    build_approval_voice_context,
    build_question_voice_context,
    unresolved_blocking_voice_answer,
)
from vibe.core.config import ModelConfig
from vibe.core.tools.builtins.ask_user_question import Choice, Question

_TEST_MODEL = ModelConfig(name="test-model", provider="test", alias="test-model")


def _question() -> Question:
    return Question(
        question="Which tone should I use?",
        options=[
            Choice(label="Professional", description="Direct and polished"),
            Choice(label="Warm", description="Friendly and upbeat"),
        ],
        multi_select=True,
    )


class TestBlockingVoiceAnswerInterpretationSchema:
    def test_valid_response_parses(self) -> None:
        parsed = BlockingVoiceAnswerInterpretation.model_validate_json(
            """
            {
              "action_type": "select_options",
              "selected_option_labels": ["Warm"],
              "other_text": null
            }
            """
        )

        assert parsed == BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.SELECT_OPTIONS,
            selected_option_labels=["Warm"],
        )

    def test_extra_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BlockingVoiceAnswerInterpretation.model_validate_json(
                """
                {
                  "action_type": "unclear",
                  "selected_option_labels": [],
                  "other_text": null,
                  "confidence": 0.3
                }
                """
            )

    def test_invalid_action_type_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BlockingVoiceAnswerInterpretation.model_validate_json(
                """
                {
                  "action_type": "approve",
                  "selected_option_labels": [],
                  "other_text": null
                }
                """
            )


class TestBlockingVoiceAnswerInterpreter:
    @pytest.mark.asyncio
    async def test_interpret_returns_parsed_result(self) -> None:
        backend = FakeBackend(
            mock_llm_chunk(
                content="""
                {
                  "action_type": "approval_once",
                  "selected_option_labels": [],
                  "other_text": null
                }
                """
            )
        )
        interpreter = BlockingVoiceAnswerInterpreter(backend=backend, model=_TEST_MODEL)

        result = await interpreter.interpret(
            transcript="yes go ahead", context=build_approval_voice_context("bash")
        )

        assert result == BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.APPROVAL_ONCE
        )
        assert backend.requests_response_formats[0] is not None

    @pytest.mark.asyncio
    async def test_interpret_builds_context_prompt(self) -> None:
        backend = FakeBackend(
            mock_llm_chunk(
                content="""
                {
                  "action_type": "select_options",
                  "selected_option_labels": ["Warm"],
                  "other_text": null
                }
                """
            )
        )
        interpreter = BlockingVoiceAnswerInterpreter(backend=backend, model=_TEST_MODEL)

        await interpreter.interpret(
            transcript="the warm one", context=build_question_voice_context(_question())
        )

        messages = backend.requests_messages[0]
        assert len(messages) == 1
        assert messages[0].content is not None
        assert "Which tone should I use?" in messages[0].content
        assert "Professional" in messages[0].content
        assert "the warm one" in messages[0].content

    @pytest.mark.asyncio
    async def test_interpret_returns_unclear_on_backend_failure(self) -> None:
        interpreter = BlockingVoiceAnswerInterpreter(
            backend=FakeBackend(exception_to_raise=RuntimeError("backend down")),
            model=_TEST_MODEL,
        )

        result = await interpreter.interpret(
            transcript="yes", context=build_approval_voice_context("bash")
        )

        assert result == unresolved_blocking_voice_answer()

    @pytest.mark.asyncio
    async def test_interpret_returns_unclear_on_invalid_json(self) -> None:
        interpreter = BlockingVoiceAnswerInterpreter(
            backend=FakeBackend(mock_llm_chunk(content="not json")), model=_TEST_MODEL
        )

        result = await interpreter.interpret(
            transcript="yes", context=build_approval_voice_context("bash")
        )

        assert result == unresolved_blocking_voice_answer()

    @pytest.mark.asyncio
    async def test_interpret_returns_unclear_on_invalid_schema(self) -> None:
        interpreter = BlockingVoiceAnswerInterpreter(
            backend=FakeBackend(
                mock_llm_chunk(
                    content="""
                    {
                      "action_type": "select_options",
                      "selected_option_labels": ["Warm"],
                      "other_text": null,
                      "extra": true
                    }
                    """
                )
            ),
            model=_TEST_MODEL,
        )

        result = await interpreter.interpret(
            transcript="warm", context=build_question_voice_context(_question())
        )

        assert result == unresolved_blocking_voice_answer()
