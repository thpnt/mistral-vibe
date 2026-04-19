from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from pydantic import BaseModel
import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.narrator_manager import NarratorState
from vibe.cli.textual_ui.action_required_narration import (
    format_active_question_narration,
    format_approval_narration,
    format_approval_voice_confirmation,
    format_question_voice_confirmation,
)
from vibe.cli.textual_ui.blocking_voice_answers import (
    ApprovalVoiceAction,
    MultiSelectQuestionVoiceAnswer,
    QuestionVoiceAnswer,
)
from vibe.cli.textual_ui.notifications import NotificationContext
from vibe.cli.textual_ui.widgets.question_app import QuestionApp
from vibe.core.tools.builtins.ask_user_question import (
    Answer,
    AskUserQuestionArgs,
    AskUserQuestionResult,
    Choice,
    Question,
)
from vibe.core.types import ApprovalResponse, BaseEvent


class DummyArgs(BaseModel):
    pass


class RecordingNarratorManager:
    def __init__(self) -> None:
        self._state = NarratorState.IDLE
        self.speak_calls: list[str] = []

    @property
    def state(self) -> NarratorState:
        return self._state

    @property
    def is_playing(self) -> bool:
        return False

    def on_turn_start(self, user_message: str) -> None:
        del user_message

    def on_turn_event(self, event: BaseEvent) -> None:
        del event

    def on_turn_error(self, message: str) -> None:
        del message

    def on_turn_cancel(self) -> None:
        pass

    def on_turn_end(self) -> None:
        pass

    async def speak_action_required(self, text: str) -> None:
        self.speak_calls.append(text)

    def cancel(self) -> None:
        pass

    def sync(self) -> None:
        pass

    def add_listener(self, listener) -> None:
        del listener

    def remove_listener(self, listener) -> None:
        del listener

    async def close(self) -> None:
        pass


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[NotificationContext] = []

    def notify(self, context: NotificationContext) -> None:
        self.calls.append(context)

    def on_focus(self) -> None:
        pass

    def on_blur(self) -> None:
        pass

    def restore(self) -> None:
        pass


def _exit_plan_question_args() -> AskUserQuestionArgs:
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="Plan is complete. Switch to accept-edits mode and start implementing?",
                header="Plan ready",
                options=[
                    Choice(label="Yes, and auto approve edits"),
                    Choice(label="Yes, and request approval for edits"),
                    Choice(label="No"),
                ],
            )
        ]
    )


def _teleport_push_args() -> AskUserQuestionArgs:
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="You have 2 unpushed commits. Push to continue?",
                header="Push",
                options=[Choice(label="Push and continue"), Choice(label="Cancel")],
                hide_other=True,
            )
        ]
    )


class TestFormatters:
    def test_format_approval_narration_for_bash(self) -> None:
        assert format_approval_narration("bash", DummyArgs(), None) == (
            "I need your approval to run this command."
        )

    def test_format_approval_narration_for_file_edit(self) -> None:
        assert format_approval_narration("search_replace", DummyArgs(), None) == (
            "I need your approval to edit this file."
        )

    def test_format_approval_narration_does_not_list_choices(self) -> None:
        narration = format_approval_narration("bash", DummyArgs(), None)

        assert "allow it once" not in narration
        assert "always allow" not in narration
        assert "reject it" not in narration

    def test_format_active_question_narration_single_select(self) -> None:
        assert format_active_question_narration(
            _exit_plan_question_args().questions[0]
        ) == (
            "I need your input before I continue. "
            "Plan is complete. Switch to accept-edits mode and start implementing? "
            "Choose Yes, and auto approve edits, "
            "Yes, and request approval for edits, or No. "
            "You can also enter a different answer."
        )

    def test_format_active_question_narration_multi_select(self) -> None:
        question = Question(
            question="Which tone should I use?",
            header="Tone",
            options=[
                Choice(label="Professional"),
                Choice(label="Warm"),
                Choice(label="Default"),
            ],
            multi_select=True,
        )

        assert format_active_question_narration(question) == (
            "I need your input before I continue. "
            "Which tone should I use? "
            "Choose one or more options: Professional, Warm, or Default. "
            "You can also enter a different answer."
        )

    def test_format_active_question_narration_omits_other_when_hidden(self) -> None:
        question = Question(
            question="You have 2 unpushed commits. Push to continue?",
            header="Push",
            options=[Choice(label="Push and continue"), Choice(label="Cancel")],
            hide_other=True,
        )

        assert format_active_question_narration(question) == (
            "I need your input before I continue. "
            "You have 2 unpushed commits. Push to continue? "
            "Choose Push and continue or Cancel."
        )

    def test_format_approval_voice_confirmation_allow_once(self) -> None:
        assert (
            format_approval_voice_confirmation(ApprovalVoiceAction.APPROVE_ONCE)
            == "Got it, I'll allow this command."
        )

    def test_format_approval_voice_confirmation_allow_always(self) -> None:
        assert (
            format_approval_voice_confirmation(ApprovalVoiceAction.APPROVE_ALWAYS)
            == "Understood, I'll always allow that for this session."
        )

    def test_format_approval_voice_confirmation_reject(self) -> None:
        assert (
            format_approval_voice_confirmation(ApprovalVoiceAction.REJECT)
            == "Okay, I won't allow it."
        )

    def test_format_question_voice_confirmation_single_select(self) -> None:
        question = Question(
            question="Which database should I use?",
            options=[Choice(label="PostgreSQL"), Choice(label="MongoDB")],
        )

        assert (
            format_question_voice_confirmation(
                question, QuestionVoiceAnswer(answer="MongoDB", is_other=False)
            )
            == "Okay, I'll use MongoDB."
        )

    def test_format_question_voice_confirmation_multi_select(self) -> None:
        question = Question(
            question="Which tone should I use?",
            options=[
                Choice(label="Professional"),
                Choice(label="Warm"),
                Choice(label="Concise"),
            ],
            multi_select=True,
        )

        assert (
            format_question_voice_confirmation(
                question, MultiSelectQuestionVoiceAnswer(selected_indices=(0, 2))
            )
            == "Okay, I'll use Professional and Concise."
        )

    def test_format_question_voice_confirmation_other(self) -> None:
        question = Question(
            question="Which database should I use?",
            options=[Choice(label="PostgreSQL"), Choice(label="MongoDB")],
        )

        assert (
            format_question_voice_confirmation(
                question, QuestionVoiceAnswer(answer="SQLite", is_other=True)
            )
            == "Understood, I'll use your custom answer."
        )


async def _wait_for_pending_future(getter, *, timeout: float = 1.0):
    loop = asyncio.get_running_loop()
    start = loop.time()
    while (future := getter()) is None:
        if (loop.time() - start) > timeout:
            raise AssertionError("Timed out waiting for pending future")
        await asyncio.sleep(0)
    return future


class TestCallbacks:
    @pytest.mark.asyncio
    async def test_approval_callback_speaks_action_required_text(self) -> None:
        narrator_manager = RecordingNarratorManager()
        notifier = RecordingNotifier()
        app = build_test_vibe_app(
            narrator_manager=narrator_manager, terminal_notifier=notifier
        )
        app._switch_to_approval_app = AsyncMock()
        app._switch_to_input_app = AsyncMock()

        callback_task = asyncio.create_task(
            app._approval_callback("bash", DummyArgs(), "tool-call-1", None)
        )
        pending_approval = await _wait_for_pending_future(lambda: app._pending_approval)

        assert narrator_manager.speak_calls == [
            format_approval_narration("bash", DummyArgs(), None)
        ]
        assert notifier.calls == [NotificationContext.ACTION_REQUIRED]

        pending_approval.set_result((ApprovalResponse.YES, None))
        assert await callback_task == (ApprovalResponse.YES, None)
        app._switch_to_approval_app.assert_awaited_once()
        app._switch_to_input_app.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_input_callback_does_not_speak_without_question_app_transition(
        self,
    ) -> None:
        narrator_manager = RecordingNarratorManager()
        notifier = RecordingNotifier()
        app = build_test_vibe_app(
            narrator_manager=narrator_manager, terminal_notifier=notifier
        )
        app._switch_to_question_app = AsyncMock()
        app._switch_to_input_app = AsyncMock()
        question_args = _teleport_push_args()

        callback_task = asyncio.create_task(app._user_input_callback(question_args))
        pending_question = await _wait_for_pending_future(lambda: app._pending_question)

        assert narrator_manager.speak_calls == []
        assert notifier.calls == [NotificationContext.ACTION_REQUIRED]

        pending_question.set_result(
            AskUserQuestionResult(
                answers=[
                    Answer(
                        question=question_args.questions[0].question,
                        answer="Push and continue",
                    )
                ],
                cancelled=False,
            )
        )
        result = await callback_task
        assert result.answers[0].answer == "Push and continue"
        app._switch_to_question_app.assert_awaited_once_with(question_args)
        app._switch_to_input_app.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multi_question_flow_speaks_each_active_question(self) -> None:
        narrator_manager = RecordingNarratorManager()
        notifier = RecordingNotifier()
        app = build_test_vibe_app(
            narrator_manager=narrator_manager, terminal_notifier=notifier
        )
        question_args = AskUserQuestionArgs(
            questions=[
                Question(
                    question="Should I update the existing prompt or create a new one?",
                    header="Prompt",
                    options=[
                        Choice(label="Update existing"),
                        Choice(label="Create new"),
                    ],
                ),
                Question(
                    question="Which tone should I use?",
                    header="Tone",
                    options=[Choice(label="Warm"), Choice(label="Professional")],
                ),
            ]
        )

        async with app.run_test() as pilot:
            callback_task = asyncio.create_task(app._user_input_callback(question_args))
            await pilot.pause(0.2)

            assert narrator_manager.speak_calls == [
                format_active_question_narration(question_args.questions[0])
            ]
            assert notifier.calls == [NotificationContext.ACTION_REQUIRED]
            assert app.query_one(QuestionApp).current_question_idx == 0

            await pilot.press("enter")
            await pilot.pause(0.2)

            assert narrator_manager.speak_calls == [
                format_active_question_narration(question_args.questions[0]),
                format_active_question_narration(question_args.questions[1]),
            ]
            assert app.query_one(QuestionApp).current_question_idx == 1

            await pilot.press("enter")
            await pilot.pause(0.2)

            result = await callback_task

        assert [answer.answer for answer in result.answers] == [
            "Update existing",
            "Warm",
        ]
