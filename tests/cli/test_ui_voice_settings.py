from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.app import BottomApp
from vibe.cli.textual_ui.widgets.voice_app import VoiceApp


@pytest.mark.asyncio
async def test_voice_settings_open_voice_app() -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_voice_settings()
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Voice
        assert len(app.query(VoiceApp)) == 1


@pytest.mark.asyncio
async def test_voice_settings_escape_saves_tts_voice() -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_voice_settings()
        await pilot.pause(0.2)

        await pilot.press("down", "down", "enter")
        await pilot.pause(0.1)

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates") as mock_save:
            await pilot.press("escape")
            await pilot.pause(0.2)

        mock_save.assert_called_once_with({"tts_voice": "gb_jane_neutral"})


@pytest.mark.asyncio
async def test_voice_settings_escape_saves_narration_tone() -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_voice_settings()
        await pilot.pause(0.2)

        await pilot.press("down", "down", "down", "enter")
        await pilot.pause(0.1)

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates") as mock_save:
            await pilot.press("escape")
            await pilot.pause(0.2)

        mock_save.assert_called_once_with({"narration_tone": "professional"})


@pytest.mark.asyncio
async def test_voice_settings_can_cycle_to_glazing_tone() -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_voice_settings()
        await pilot.pause(0.2)

        await pilot.press("down", "down", "down", "enter", "enter", "enter", "enter")
        await pilot.pause(0.1)

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates") as mock_save:
            await pilot.press("escape")
            await pilot.pause(0.2)

        mock_save.assert_called_once_with({"narration_tone": "glazing"})
