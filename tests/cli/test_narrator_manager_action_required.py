from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from tests.conftest import build_test_vibe_config
from vibe.cli.narrator_manager import NarratorManager, NarratorState
from vibe.cli.turn_summary import TurnSummaryResult
from vibe.core.audio_player.audio_player_port import AudioFormat
from vibe.core.config import NarrationTone
from vibe.core.tts.tts_client_port import TTSResult


class RecordingAudioPlayer:
    def __init__(self) -> None:
        self._playing = False
        self.play_calls: list[tuple[bytes, AudioFormat]] = []
        self.stop_calls = 0
        self._on_finished: Callable[[], object] | None = None

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(
        self,
        audio_data: bytes,
        audio_format: AudioFormat,
        *,
        on_finished: Callable[[], object] | None = None,
    ) -> None:
        self._playing = True
        self.play_calls.append((audio_data, audio_format))
        self._on_finished = on_finished

    def stop(self) -> None:
        if not self._playing:
            return
        self.stop_calls += 1
        self._playing = False


class RecordingTTSClient:
    def __init__(self, result: TTSResult | None = None) -> None:
        self.calls: list[str] = []
        self._result = result or TTSResult(audio_data=b"spoken-audio")

    async def speak(self, text: str) -> TTSResult:
        self.calls.append(text)
        return self._result

    async def close(self) -> None:
        pass


def _enabled_config():
    return build_test_vibe_config(narrator_enabled=True)


def _disabled_config():
    return build_test_vibe_config(narrator_enabled=False)


class TestSpeakActionRequired:
    @pytest.mark.asyncio
    async def test_speaks_exact_text_via_tts_and_audio(self) -> None:
        audio_player = RecordingAudioPlayer()
        narrator_manager = NarratorManager(
            config_getter=_enabled_config, audio_player=audio_player
        )
        narrator_manager._tts_client = RecordingTTSClient()

        await narrator_manager.speak_action_required(
            "I need your input before I continue."
        )

        assert narrator_manager._tts_client.calls == [
            "I need your input before I continue."
        ]
        assert audio_player.play_calls == [(b"spoken-audio", AudioFormat.WAV)]
        assert narrator_manager.state == NarratorState.SPEAKING

    @pytest.mark.asyncio
    async def test_interrupts_current_summary_speak_task_and_audio(self) -> None:
        audio_player = RecordingAudioPlayer()
        narrator_manager = NarratorManager(
            config_getter=_enabled_config, audio_player=audio_player
        )
        narrator_manager._tts_client = RecordingTTSClient()
        cancelled = False

        def cancel_summary() -> bool:
            nonlocal cancelled
            cancelled = True
            return True

        narrator_manager._cancel_summary = cancel_summary
        previous_speak_task = asyncio.create_task(asyncio.sleep(10))
        narrator_manager._speak_task = previous_speak_task
        audio_player._playing = True

        await narrator_manager.speak_action_required("Approval needed.")
        await asyncio.sleep(0)

        assert cancelled is True
        assert previous_speak_task.cancelled() is True
        assert narrator_manager._tts_client.calls == ["Approval needed."]
        assert audio_player.stop_calls == 1
        assert audio_player.play_calls == [(b"spoken-audio", AudioFormat.WAV)]

    @pytest.mark.asyncio
    async def test_is_safe_when_narrator_is_disabled(self) -> None:
        audio_player = RecordingAudioPlayer()
        narrator_manager = NarratorManager(
            config_getter=_disabled_config, audio_player=audio_player
        )

        await narrator_manager.speak_action_required("Approval needed.")

        assert audio_player.play_calls == []
        assert narrator_manager.state == NarratorState.IDLE

    @pytest.mark.asyncio
    async def test_turn_summary_speaking_still_works(self) -> None:
        audio_player = RecordingAudioPlayer()
        narrator_manager = NarratorManager(
            config_getter=_enabled_config, audio_player=audio_player
        )
        narrator_manager._tts_client = RecordingTTSClient()

        narrator_manager._on_turn_summary(
            TurnSummaryResult(
                generation=narrator_manager.turn_summary.generation,
                summary="Summary of the conversation",
            )
        )
        await asyncio.sleep(0)

        assert narrator_manager._tts_client.calls == ["Summary of the conversation"]
        assert audio_player.play_calls == [(b"spoken-audio", AudioFormat.WAV)]
        assert narrator_manager.state == NarratorState.SPEAKING

    @pytest.mark.asyncio
    async def test_sync_rebuilds_turn_summary_with_updated_narration_tone(self) -> None:
        config = build_test_vibe_config(
            narrator_enabled=True, narration_tone=NarrationTone.NEUTRAL
        )

        audio_player = RecordingAudioPlayer()
        narrator_manager = NarratorManager(
            config_getter=lambda: config, audio_player=audio_player
        )

        neutral_messages = narrator_manager.turn_summary._build_summary_messages(
            "turn context"
        )
        assert neutral_messages[0].content is not None
        assert (
            "Sound direct, concise, task-oriented, and natural."
            in neutral_messages[0].content
        )

        config.narration_tone = NarrationTone.GLAZING
        narrator_manager.sync()

        glazing_messages = narrator_manager.turn_summary._build_summary_messages(
            "turn context"
        )
        assert glazing_messages[0].content is not None
        assert (
            "Use a playful, flattering tone that treats the user like the one in charge."
            in glazing_messages[0].content
        )
