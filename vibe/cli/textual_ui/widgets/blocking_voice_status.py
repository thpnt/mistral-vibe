from __future__ import annotations

from enum import StrEnum, auto

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic


class BlockingVoiceStatus(StrEnum):
    IDLE = auto()
    WAITING_FOR_VOICE_ANSWER = auto()
    RECORDING = auto()
    TRANSCRIBING = auto()
    INTERPRETING = auto()
    ACCEPTED = auto()
    UNRESOLVED = auto()


def blocking_voice_status_message(state: BlockingVoiceStatus) -> str:
    return {
        BlockingVoiceStatus.IDLE: "",
        BlockingVoiceStatus.WAITING_FOR_VOICE_ANSWER: (
            "Waiting for voice answer - press Ctrl+R"
        ),
        BlockingVoiceStatus.RECORDING: "Recording... press Ctrl+R to stop",
        BlockingVoiceStatus.TRANSCRIBING: "Transcribing...",
        BlockingVoiceStatus.INTERPRETING: "Understanding your answer...",
        BlockingVoiceStatus.ACCEPTED: "Answer accepted",
        BlockingVoiceStatus.UNRESOLVED: (
            "Couldn't understand - try again or choose on screen"
        ),
    }[state]


def blocking_voice_status_variant(state: BlockingVoiceStatus) -> str:
    match state:
        case BlockingVoiceStatus.ACCEPTED:
            return "success"
        case BlockingVoiceStatus.UNRESOLVED:
            return "error"
        case BlockingVoiceStatus.IDLE:
            return "idle"
        case _:
            return "active"


class BlockingVoiceStatusWidget(NoMarkupStatic):
    def __init__(
        self, state: BlockingVoiceStatus = BlockingVoiceStatus.IDLE, **kwargs: object
    ) -> None:
        super().__init__("", classes="blocking-voice-status", **kwargs)
        self._state = BlockingVoiceStatus.IDLE
        self.set_status(state)

    @property
    def state(self) -> BlockingVoiceStatus:
        return self._state

    @property
    def message(self) -> str:
        return blocking_voice_status_message(self._state)

    def set_status(self, state: BlockingVoiceStatus) -> None:
        self._state = state
        self.update(self.message)
        self.display = state is not BlockingVoiceStatus.IDLE
        self.remove_class("blocking-voice-status-active")
        self.remove_class("blocking-voice-status-success")
        self.remove_class("blocking-voice-status-error")
        variant = blocking_voice_status_variant(state)
        if variant != "idle":
            self.add_class(f"blocking-voice-status-{variant}")
