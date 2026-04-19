from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from tests.conftest import build_test_vibe_app
from tests.stubs.fake_voice_manager import FakeVoiceManager
from vibe.cli.narrator_manager import NarratorState
from vibe.cli.textual_ui.action_required_narration import (
    format_active_question_narration,
    format_approval_narration,
)
from vibe.cli.textual_ui.blocking_voice_answer_interpreter import (
    BlockingVoiceActionType,
    BlockingVoiceAnswerInterpretation,
    BlockingVoiceAnswerInterpreterPort,
    unresolved_blocking_voice_answer,
)
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.cli.textual_ui.widgets.blocking_voice_status import BlockingVoiceStatusWidget
from vibe.cli.textual_ui.widgets.chat_input import ChatInputBody, ChatInputContainer
from vibe.cli.voice_manager.voice_manager_port import TranscribeState
from vibe.core.tools.builtins.ask_user_question import (
    Answer,
    AskUserQuestionArgs,
    AskUserQuestionResult,
    Choice,
    Question,
)
from vibe.core.tools.builtins.bash import BashArgs
from vibe.core.types import ApprovalResponse, BaseEvent


async def _wait_for_pending_future(getter, *, timeout: float = 1.0):
    loop = asyncio.get_running_loop()
    start = loop.time()
    while (future := getter()) is None:
        if (loop.time() - start) > timeout:
            raise AssertionError("Timed out waiting for pending future")
        await asyncio.sleep(0)
    return future


def _question_args(
    *, hide_other: bool = False, multi_select: bool = False
) -> AskUserQuestionArgs:
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="Which database?",
                header="DB",
                options=[Choice(label="PostgreSQL"), Choice(label="MongoDB")],
                hide_other=hide_other,
                multi_select=multi_select,
            )
        ]
    )


def _multi_select_question_args(*, hide_other: bool = False) -> AskUserQuestionArgs:
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="Which tone should I use?",
                header="Tone",
                options=[
                    Choice(label="Professional"),
                    Choice(label="Warm"),
                    Choice(label="Default"),
                ],
                hide_other=hide_other,
                multi_select=True,
            )
        ]
    )


def _two_question_args() -> AskUserQuestionArgs:
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="Which database?",
                header="DB",
                options=[Choice(label="PostgreSQL"), Choice(label="MongoDB")],
            ),
            Question(
                question="Which framework?",
                header="Framework",
                options=[Choice(label="FastAPI"), Choice(label="Django")],
            ),
        ]
    )


def _approval_prompt_narration() -> str:
    return format_approval_narration("bash", BashArgs(command="echo hi"), None)


async def _start_approval_prompt(
    app,
) -> asyncio.Task[tuple[ApprovalResponse, str | None]]:
    task = asyncio.create_task(
        app._approval_callback("bash", BashArgs(command="echo hi"), "tool-1", None)
    )
    await _wait_for_pending_future(lambda: app._pending_approval)
    return task


async def _start_question_prompt(
    app, args: AskUserQuestionArgs
) -> asyncio.Task[AskUserQuestionResult]:
    task = asyncio.create_task(app._user_input_callback(args))
    await _wait_for_pending_future(lambda: app._pending_question)
    return task


def _emit_voice_answer(voice_manager: FakeVoiceManager, text: str) -> None:
    voice_manager.set_transcribe_state(TranscribeState.RECORDING)
    voice_manager.emit_transcribe_text(text)
    voice_manager.set_transcribe_state(TranscribeState.IDLE)


def _blocking_voice_status_message(app) -> str | None:
    try:
        widget = app.query_one(BlockingVoiceStatusWidget)
    except Exception:
        return None
    if not widget.display:
        return None
    return widget.message


def _approval_option_texts(app) -> list[str]:
    approval_app = app.query_one(ApprovalApp)
    return [widget.content for widget in approval_app.option_widgets]


class RecordingNarratorManager:
    def __init__(self) -> None:
        self._state = NarratorState.IDLE
        self.speak_calls: list[str] = []
        self.cancel_calls = 0

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
        self.cancel_calls += 1

    def sync(self) -> None:
        pass

    def add_listener(self, listener) -> None:
        del listener

    def remove_listener(self, listener) -> None:
        del listener

    async def close(self) -> None:
        pass


class FakeBlockingVoiceAnswerInterpreter(BlockingVoiceAnswerInterpreterPort):
    def __init__(
        self,
        responses: Sequence[BlockingVoiceAnswerInterpretation | Exception]
        | None = None,
    ) -> None:
        self._responses = list(responses or [unresolved_blocking_voice_answer()])
        self.calls: list[tuple[str, object]] = []

    async def interpret(
        self, *, transcript: str, context: object
    ) -> BlockingVoiceAnswerInterpretation:
        self.calls.append((transcript, context))
        if not self._responses:
            return unresolved_blocking_voice_answer()
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        return None


class ControlledBlockingVoiceAnswerInterpreter(BlockingVoiceAnswerInterpreterPort):
    def __init__(self, response: BlockingVoiceAnswerInterpretation) -> None:
        self._response = response
        self.calls: list[tuple[str, object]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def interpret(
        self, *, transcript: str, context: object
    ) -> BlockingVoiceAnswerInterpretation:
        self.calls.append((transcript, context))
        self.started.set()
        await self.release.wait()
        return self._response

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_blocking_approval_transcript_is_routed_without_chat_input_leak() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.APPROVAL_ONCE
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager, blocking_voice_answer_interpreter=interpreter
    )

    async with app.run_test() as pilot:
        task = await _start_approval_prompt(app)
        await pilot.pause()

        chat_input = app.query_one(ChatInputContainer)
        assert chat_input.input_widget is not None

        _emit_voice_answer(voice_manager, "yes go ahead")
        await pilot.pause()

        assert chat_input.input_widget.get_full_text() == ""
        assert await task == (ApprovalResponse.YES, None)


@pytest.mark.asyncio
async def test_blocking_question_transcript_is_routed_without_chat_input_leak() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.SELECT_OPTIONS,
            selected_option_labels=["MongoDB"],
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager, blocking_voice_answer_interpreter=interpreter
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, _question_args())
        await pilot.pause()

        chat_input = app.query_one(ChatInputContainer)
        assert chat_input.input_widget is not None

        _emit_voice_answer(voice_manager, "the mongodb one")
        await pilot.pause()

        assert chat_input.input_widget.get_full_text() == ""
        assert await task == AskUserQuestionResult(
            answers=[
                Answer(question="Which database?", answer="MongoDB", is_other=False)
            ],
            cancelled=False,
        )


@pytest.mark.asyncio
async def test_transcript_without_blocking_state_still_populates_chat_input() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    app = build_test_vibe_app(
        voice_manager=voice_manager, narrator_manager=narrator_manager
    )

    async with app.run_test() as pilot:
        voice_manager.emit_transcribe_text("hello from voice")
        await pilot.pause()

        chat_input_body = app.query_one(ChatInputBody)
        assert chat_input_body.input_widget is not None
        assert chat_input_body.input_widget.get_full_text() == "hello from voice"
        assert narrator_manager.speak_calls == []


@pytest.mark.asyncio
async def test_approval_prompt_shows_waiting_voice_status() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        task = await _start_approval_prompt(app)
        pending = await _wait_for_pending_future(lambda: app._pending_approval)
        await pilot.pause()

        assert _approval_option_texts(app) == [
            "› 1. Yes",
            "  2. Yes and always allow bash for this session",
            "  3. No and tell the agent what to do instead",
        ]
        assert _blocking_voice_status_message(app) == (
            "Waiting for voice answer - press Ctrl+R"
        )

        pending.set_result((ApprovalResponse.YES, None))
        assert await task == (ApprovalResponse.YES, None)


@pytest.mark.asyncio
async def test_blocking_voice_status_tracks_recording_transcribing_and_interpreting() -> (
    None
):
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    interpreter = ControlledBlockingVoiceAnswerInterpreter(
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.SELECT_OPTIONS,
            selected_option_labels=["MongoDB"],
        )
    )
    app = build_test_vibe_app(
        voice_manager=voice_manager, blocking_voice_answer_interpreter=interpreter
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, _question_args())
        await pilot.pause()

        voice_manager.set_transcribe_state(TranscribeState.RECORDING)
        await pilot.pause()
        assert _blocking_voice_status_message(app) == (
            "Recording... press Ctrl+R to stop"
        )

        voice_manager.emit_transcribe_text("mongodb")
        voice_manager.set_transcribe_state(TranscribeState.FLUSHING)
        await pilot.pause()
        assert _blocking_voice_status_message(app) == "Transcribing..."

        voice_manager.set_transcribe_state(TranscribeState.IDLE)
        await interpreter.started.wait()
        await pilot.pause()
        assert _blocking_voice_status_message(app) == "Understanding your answer..."

        interpreter.release.set()
        await pilot.pause()

        assert await task == AskUserQuestionResult(
            answers=[
                Answer(question="Which database?", answer="MongoDB", is_other=False)
            ],
            cancelled=False,
        )


@pytest.mark.asyncio
async def test_approval_voice_status_shows_accepted_before_submit_finishes() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.APPROVAL_ONCE
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager, blocking_voice_answer_interpreter=interpreter
    )
    captured_status: str | None = None

    async with app.run_test() as pilot:
        task = await _start_approval_prompt(app)
        await pilot.pause()

        approval_app = app.query_one(ApprovalApp)
        original_submit = approval_app.submit_voice_action

        def submit_and_capture(action) -> bool:
            nonlocal captured_status
            captured_status = _blocking_voice_status_message(app)
            return original_submit(action)

        approval_app.submit_voice_action = submit_and_capture

        _emit_voice_answer(voice_manager, "yes")
        await pilot.pause()

        assert await task == (ApprovalResponse.YES, None)
        assert captured_status == "Answer accepted"


@pytest.mark.asyncio
async def test_question_voice_status_shows_unresolved_when_answer_cannot_be_used() -> (
    None
):
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        unresolved_blocking_voice_answer()
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager, blocking_voice_answer_interpreter=interpreter
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, _question_args())
        pending = await _wait_for_pending_future(lambda: app._pending_question)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "not the default one")
        await pilot.pause()

        assert _blocking_voice_status_message(app) == (
            "Couldn't understand - try again or choose on screen"
        )

        pending.set_result(AskUserQuestionResult(answers=[], cancelled=True))
        assert await task == AskUserQuestionResult(answers=[], cancelled=True)


@pytest.mark.asyncio
async def test_multi_question_flow_returns_to_waiting_status_for_next_question() -> (
    None
):
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.SELECT_OPTIONS,
            selected_option_labels=["MongoDB"],
        )
    ])
    question_args = _two_question_args()
    app = build_test_vibe_app(
        voice_manager=voice_manager, blocking_voice_answer_interpreter=interpreter
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, question_args)
        pending = await _wait_for_pending_future(lambda: app._pending_question)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "mongodb")
        await pilot.pause()

        assert _blocking_voice_status_message(app) == (
            "Waiting for voice answer - press Ctrl+R"
        )

        pending.set_result(AskUserQuestionResult(answers=[], cancelled=True))
        assert await task == AskUserQuestionResult(answers=[], cancelled=True)


@pytest.mark.asyncio
async def test_cancelling_question_flow_removes_blocking_voice_status_widget() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, _question_args())
        await pilot.pause()

        assert _blocking_voice_status_message(app) == (
            "Waiting for voice answer - press Ctrl+R"
        )

        await pilot.press("escape")
        await pilot.pause()

        assert await task == AskUserQuestionResult(answers=[], cancelled=True)
        assert _blocking_voice_status_message(app) is None


@pytest.mark.asyncio
async def test_approval_voice_answer_always_allow_resolves_once_with_existing_contract() -> (
    None
):
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.APPROVAL_ALWAYS
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager, blocking_voice_answer_interpreter=interpreter
    )

    async with app.run_test() as pilot:
        task = await _start_approval_prompt(app)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "always allow that")
        await pilot.pause()

        assert await task == (ApprovalResponse.YES, None)


@pytest.mark.asyncio
async def test_approval_voice_answer_allow_once_speaks_confirmation() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.APPROVAL_ONCE
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_approval_prompt(app)
        await pilot.pause()

        assert narrator_manager.speak_calls == [_approval_prompt_narration()]

        _emit_voice_answer(voice_manager, "yes")
        await pilot.pause()

        assert await task == (ApprovalResponse.YES, None)
        assert narrator_manager.speak_calls[-1] == "Got it, I'll allow this command."


@pytest.mark.asyncio
async def test_approval_voice_answer_always_allow_speaks_confirmation() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.APPROVAL_ALWAYS
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_approval_prompt(app)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "always allow")
        await pilot.pause()

        assert await task == (ApprovalResponse.YES, None)
        assert narrator_manager.speak_calls[-1] == (
            "Understood, I'll always allow that for this session."
        )


@pytest.mark.asyncio
async def test_approval_voice_answer_reject_speaks_confirmation() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(action_type=BlockingVoiceActionType.REJECT)
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_approval_prompt(app)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "no")
        await pilot.pause()

        result = await task

        assert result[0] == ApprovalResponse.NO
        assert result[1] is not None
        assert narrator_manager.speak_calls[-1] == "Okay, I won't allow it."


@pytest.mark.asyncio
async def test_approval_voice_answer_invalid_payload_leaves_prompt_pending() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.APPROVAL_ONCE,
            selected_option_labels=["MongoDB"],
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_approval_prompt(app)
        pending = await _wait_for_pending_future(lambda: app._pending_approval)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "yes")
        await pilot.pause()

        assert not pending.done()
        assert narrator_manager.speak_calls == [_approval_prompt_narration()]

        pending.set_result((ApprovalResponse.YES, None))
        assert await task == (ApprovalResponse.YES, None)


@pytest.mark.asyncio
async def test_approval_voice_answer_unavailable_interpreter_keeps_prompt_pending() -> (
    None
):
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=None,
    )
    app._blocking_voice_answer_interpreter = None

    async with app.run_test() as pilot:
        task = await _start_approval_prompt(app)
        pending = await _wait_for_pending_future(lambda: app._pending_approval)
        await pilot.pause()

        chat_input = app.query_one(ChatInputContainer)
        assert chat_input.input_widget is not None

        _emit_voice_answer(voice_manager, "yes")
        await pilot.pause()

        assert chat_input.input_widget.get_full_text() == ""
        assert not pending.done()
        assert narrator_manager.speak_calls == [_approval_prompt_narration()]

        pending.set_result((ApprovalResponse.YES, None))
        assert await task == (ApprovalResponse.YES, None)


@pytest.mark.asyncio
async def test_question_voice_answer_single_select_speaks_confirmation() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    question_args = _question_args()
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.SELECT_OPTIONS,
            selected_option_labels=["MongoDB"],
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, question_args)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "mongodb")
        await pilot.pause()

        assert await task == AskUserQuestionResult(
            answers=[
                Answer(question="Which database?", answer="MongoDB", is_other=False)
            ],
            cancelled=False,
        )
        assert narrator_manager.speak_calls == [
            format_active_question_narration(question_args.questions[0]),
            "Okay, I'll use MongoDB.",
        ]


@pytest.mark.asyncio
async def test_question_voice_answer_other_text_resolves_when_allowed() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    question_args = _question_args()
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.OTHER_TEXT, other_text="SQLite"
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, question_args)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "something else, sqlite")
        await pilot.pause()

        assert await task == AskUserQuestionResult(
            answers=[
                Answer(question="Which database?", answer="SQLite", is_other=True)
            ],
            cancelled=False,
        )
        assert narrator_manager.speak_calls == [
            format_active_question_narration(question_args.questions[0]),
            "Understood, I'll use your custom answer.",
        ]


@pytest.mark.asyncio
async def test_question_voice_answer_other_text_hidden_stays_pending() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    question_args = _question_args(hide_other=True)
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.OTHER_TEXT, other_text="SQLite"
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, question_args)
        pending = await _wait_for_pending_future(lambda: app._pending_question)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "sqlite")
        await pilot.pause()

        assert not pending.done()
        assert narrator_manager.speak_calls == [
            format_active_question_narration(question_args.questions[0])
        ]

        pending.set_result(AskUserQuestionResult(answers=[], cancelled=True))
        assert await task == AskUserQuestionResult(answers=[], cancelled=True)


@pytest.mark.asyncio
async def test_question_voice_answer_multi_select_with_two_labels_resolves() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    question_args = _multi_select_question_args()
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.SELECT_OPTIONS,
            selected_option_labels=["Professional", "Warm"],
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, question_args)
        await pilot.pause()

        chat_input = app.query_one(ChatInputContainer)
        assert chat_input.input_widget is not None

        _emit_voice_answer(voice_manager, "both professional and warm")
        await pilot.pause()

        assert chat_input.input_widget.get_full_text() == ""
        assert await task == AskUserQuestionResult(
            answers=[
                Answer(
                    question="Which tone should I use?",
                    answer="Professional, Warm",
                    is_other=False,
                )
            ],
            cancelled=False,
        )
        assert narrator_manager.speak_calls == [
            format_active_question_narration(question_args.questions[0]),
            "Okay, I'll use Professional and Warm.",
        ]


@pytest.mark.asyncio
async def test_question_voice_answer_multi_select_stays_pending_when_not_allowed() -> (
    None
):
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.SELECT_OPTIONS,
            selected_option_labels=["PostgreSQL", "MongoDB"],
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager, blocking_voice_answer_interpreter=interpreter
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, _question_args())
        pending = await _wait_for_pending_future(lambda: app._pending_question)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "both")
        await pilot.pause()

        assert not pending.done()

        pending.set_result(AskUserQuestionResult(answers=[], cancelled=True))
        assert await task == AskUserQuestionResult(answers=[], cancelled=True)


@pytest.mark.asyncio
async def test_question_voice_answer_invalid_label_stays_pending() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    question_args = _question_args()
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.SELECT_OPTIONS,
            selected_option_labels=["SQLite"],
        )
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, question_args)
        pending = await _wait_for_pending_future(lambda: app._pending_question)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "sqlite")
        await pilot.pause()

        assert not pending.done()
        assert narrator_manager.speak_calls == [
            format_active_question_narration(question_args.questions[0])
        ]

        pending.set_result(AskUserQuestionResult(answers=[], cancelled=True))
        assert await task == AskUserQuestionResult(answers=[], cancelled=True)


@pytest.mark.asyncio
async def test_question_voice_answer_unclear_stays_pending() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    question_args = _question_args()
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        unresolved_blocking_voice_answer()
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, question_args)
        pending = await _wait_for_pending_future(lambda: app._pending_question)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "not the default one")
        await pilot.pause()

        assert not pending.done()
        assert narrator_manager.speak_calls == [
            format_active_question_narration(question_args.questions[0])
        ]

        pending.set_result(AskUserQuestionResult(answers=[], cancelled=True))
        assert await task == AskUserQuestionResult(answers=[], cancelled=True)


@pytest.mark.asyncio
async def test_question_voice_answer_interpreter_failure_stays_silent() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    question_args = _question_args()
    interpreter = FakeBlockingVoiceAnswerInterpreter([RuntimeError("boom")])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, question_args)
        pending = await _wait_for_pending_future(lambda: app._pending_question)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "mongodb")
        await pilot.pause()

        assert not pending.done()
        assert narrator_manager.speak_calls == [
            format_active_question_narration(question_args.questions[0])
        ]

        pending.set_result(AskUserQuestionResult(answers=[], cancelled=True))
        assert await task == AskUserQuestionResult(answers=[], cancelled=True)


@pytest.mark.asyncio
async def test_multi_question_voice_confirmation_only_speaks_on_final_submit() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    question_args = _two_question_args()
    interpreter = FakeBlockingVoiceAnswerInterpreter([
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.SELECT_OPTIONS,
            selected_option_labels=["MongoDB"],
        ),
        BlockingVoiceAnswerInterpretation(
            action_type=BlockingVoiceActionType.SELECT_OPTIONS,
            selected_option_labels=["FastAPI"],
        ),
    ])
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, question_args)
        await pilot.pause()

        _emit_voice_answer(voice_manager, "mongodb")
        await pilot.pause()

        assert narrator_manager.speak_calls == [
            format_active_question_narration(question_args.questions[0]),
            format_active_question_narration(question_args.questions[1]),
        ]

        _emit_voice_answer(voice_manager, "fastapi")
        await pilot.pause()

        assert await task == AskUserQuestionResult(
            answers=[
                Answer(question="Which database?", answer="MongoDB", is_other=False),
                Answer(question="Which framework?", answer="FastAPI", is_other=False),
            ],
            cancelled=False,
        )
        assert narrator_manager.speak_calls[-1] == "Okay, I'll use FastAPI."


@pytest.mark.asyncio
async def test_ctrl_r_starts_and_stops_recording_while_approval_app_is_active() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    interpreter = FakeBlockingVoiceAnswerInterpreter()
    app = build_test_vibe_app(
        voice_manager=voice_manager,
        narrator_manager=narrator_manager,
        blocking_voice_answer_interpreter=interpreter,
    )

    async with app.run_test() as pilot:
        task = await _start_approval_prompt(app)
        pending = await _wait_for_pending_future(lambda: app._pending_approval)
        await pilot.pause()

        await pilot.press("ctrl+r")
        await pilot.pause()
        assert voice_manager.transcribe_state == TranscribeState.RECORDING
        assert narrator_manager.cancel_calls == 1

        await pilot.press("ctrl+r")
        await pilot.pause()
        assert voice_manager.transcribe_state == TranscribeState.IDLE

        pending.set_result((ApprovalResponse.YES, None))
        assert await task == (ApprovalResponse.YES, None)


@pytest.mark.asyncio
async def test_ctrl_r_starts_and_stops_recording_while_question_app_is_active() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    interpreter = FakeBlockingVoiceAnswerInterpreter()
    app = build_test_vibe_app(
        voice_manager=voice_manager, blocking_voice_answer_interpreter=interpreter
    )

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, _question_args())
        pending = await _wait_for_pending_future(lambda: app._pending_question)
        await pilot.pause()

        await pilot.press("ctrl+r")
        await pilot.pause()
        assert voice_manager.transcribe_state == TranscribeState.RECORDING

        await pilot.press("ctrl+r")
        await pilot.pause()
        assert voice_manager.transcribe_state == TranscribeState.IDLE

        pending.set_result(AskUserQuestionResult(answers=[], cancelled=True))
        assert await task == AskUserQuestionResult(answers=[], cancelled=True)


@pytest.mark.asyncio
async def test_ctrl_r_still_works_for_chat_input_voice_mode() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    narrator_manager = RecordingNarratorManager()
    app = build_test_vibe_app(
        voice_manager=voice_manager, narrator_manager=narrator_manager
    )

    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert voice_manager.transcribe_state == TranscribeState.RECORDING
        assert narrator_manager.cancel_calls == 1


@pytest.mark.asyncio
async def test_keyboard_question_flow_still_works_without_confirmation() -> None:
    narrator_manager = RecordingNarratorManager()
    question_args = _question_args()
    app = build_test_vibe_app(narrator_manager=narrator_manager)

    async with app.run_test() as pilot:
        task = await _start_question_prompt(app, question_args)
        await pilot.pause()

        await pilot.press("down", "enter")
        await pilot.pause()

        assert await task == AskUserQuestionResult(
            answers=[
                Answer(question="Which database?", answer="MongoDB", is_other=False)
            ],
            cancelled=False,
        )
        assert narrator_manager.speak_calls == [
            format_active_question_narration(question_args.questions[0])
        ]


@pytest.mark.asyncio
async def test_keyboard_approval_flow_still_works_without_confirmation() -> None:
    narrator_manager = RecordingNarratorManager()
    app = build_test_vibe_app(narrator_manager=narrator_manager)

    async with app.run_test() as pilot:
        task = await _start_approval_prompt(app)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert await task == (ApprovalResponse.YES, None)
        assert narrator_manager.speak_calls == [_approval_prompt_narration()]
