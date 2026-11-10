"""Headless smoke test for the VoxCLode Textual UI.

Run: .venv\\Scripts\\python.exe scripts\\check_voxclode.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

os.environ.setdefault("VOXCLODE_NO_CUDA_PROBE", "1")

from textual.widgets import Input, RadioSet, RichLog, Static, TextArea  # noqa: E402

from voxclode.app import ToggleButton, VoxClodeApp  # noqa: E402
from voxclode.audio import waveform_peaks  # noqa: E402


async def main() -> int:
    root = Path(__file__).resolve().parent.parent
    app = VoxClodeApp()

    async with app.run_test(size=(132, 46)) as pilot:
        await pilot.pause()
        await pilot.pause(0.4)

        print("mode index :", app.query_one("#mode", RadioSet).pressed_index)
        print("reference  :", app.query_one("#ref-path", Input).value)
        print("prompt len :", len(app.query_one("#prompt", TextArea).text))
        print("generate   :", app.query_one("#generate").label)
        print("stage      :", app.query_one("#stage-label", Static).content)

        await pilot.click("#mode-design")
        await pilot.pause()
        print("design mode:", app.query_one("#mode", RadioSet).pressed_index)
        print("gen label  :", app.query_one("#generate").label)
        await pilot.click("#mode-clone")
        await pilot.pause()

        toggle = app.query_one("#denoise", ToggleButton)
        toggle.toggle()
        print("toggle     :", toggle.value, toggle.label)
        assert toggle.value is True
        toggle.toggle()
        assert toggle.value is False

        app.query_one("#prompt", TextArea).text = ""
        app._start_generation()
        await pilot.pause()
        print("empty text :", "blocked" if not app._busy else "NOT BLOCKED")
        assert app._busy is False
        app.query_one("#prompt", TextArea).text = "Привет! Это мой голос, созданный нейросетью."

        # Populate the player with the reference clip to verify waveform rendering.
        app.player.load(root / "audio" / "reference_ru.wav")
        wave = waveform_peaks(root / "audio" / "reference_ru.wav")
        app.query_one("#waveform").set_audio(wave.peaks, wave.duration)
        app.seek_ratio(0.5)
        await pilot.pause(0.2)
        print("seek 50%   :", app.player.position_ms, "ms")
        assert 3800 < app.player.position_ms < 5200
        app.player.pause()
        app.query_one("#player-panel").display = True
        app.query_one("#track-title", Static).update(
            f"result.wav · 16.0 kHz · {wave.duration:.2f} c · 271 КБ"
        )
        app.query_one("#prompt-line", Static).update(
            "Промпт: Привет! Это мой голос, созданный нейросетью."
        )
        app.query_one("#play-time", Static).update("00:00.00 / 00:08.68")

        app.query_one("#overall").update(progress=64)
        app.query_one("#stage-bar").update(progress=42)
        app.query_one("#overall-label", Static).update("ПРОГРЕСС · 64%")
        app.query_one("#stage-label", Static).update("генерация речи · 42% · оценка")

        # Sanitize the demo screenshot: no local user paths in the repository.
        app.query_one("#ref-path", Input).value = "audio/anna_06.ogg"
        app.query_one("#ref-path", Input).cursor_position = len("audio/anna_06.ogg")
        app.query_one("#ref-status", Static).update("✓ anna_06.ogg → WAV 16 kHz mono · 00:04.88")
        app.query_one("#ref-status", Static).set_class(True, "ok")
        log = app.query_one("#log", RichLog)
        log.clear()
        app._log("SYS", "VoxCLode · VoxCPM2 voice studio")
        app._log("INFO", "Эталон: audio/anna_06.ogg · 48 kHz · 4.88 c")
        app._log("CMD", "ffmpeg … → .cache/converted/anna_06_16k.wav")
        app._log("OK", "Эталон готов: anna_06.ogg → WAV 16 kHz mono · 4.88 c · 153 КБ")
        app._log("INFO", "▶ Генерация речи")
        app._log("INFO", "Загрузка модели VoxCPM2…")
        await pilot.pause()

        screenshot = app.export_screenshot()
        out = root / "docs" / "voxclode-preview.svg"
        out.write_text(screenshot, encoding="utf-8")
        print("screenshot :", out)
        print("player open:", app.player.is_open, "duration_ms =", app.player.duration_ms)

        app.player.close()

    print("UI RESULT  : PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
