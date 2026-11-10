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

        # Populate the player with a generated demo clip when no sample is present.
        demo = root / ".cache" / "test" / "demo_player.wav"
        reference = root / "audio" / "reference_ru.wav"
        if reference.is_file():
            player_path = reference
        else:
            import numpy as np
            import soundfile as sf

            demo.parent.mkdir(parents=True, exist_ok=True)
            rate = 16000
            timeline = np.linspace(0, 2.0, rate * 2, endpoint=False)
            tone = 0.2 * np.sin(2 * np.pi * 440 * timeline)
            sf.write(str(demo), tone, rate, subtype="PCM_16")
            player_path = demo

        app.player.load(player_path)
        wave = waveform_peaks(player_path)
        app.query_one("#waveform").set_audio(wave.peaks, wave.duration)
        app.seek_ratio(0.5)
        await pilot.pause(0.2)
        print("seek 50%   :", app.player.position_ms, "ms")
        assert wave.duration / 2 * 1000 - 500 < app.player.position_ms < wave.duration / 2 * 1000 + 700
        app.player.pause()
        app._tick_player()
        app.query_one("#player-panel").display = True
        app.query_one("#track-title", Static).update(
            f"result.wav · 16.0 kHz · {wave.duration:.2f} c · 64 КБ"
        )
        app.query_one("#prompt-line", Static).update(
            "Промпт: Привет! Это мой голос, созданный нейросетью."
        )


        app.query_one("#overall").update(progress=64)
        app.query_one("#stage-bar").update(progress=42)
        app.query_one("#overall-label", Static).update("ПРОГРЕСС · 64%")
        app.query_one("#stage-label", Static).update("генерация речи · 42% · оценка")

        # Sanitize the demo screenshot: no local user paths in the repository.
        demo_name = "voice.wav"
        app.query_one("#ref-path", Input).value = f"audio/{demo_name}"
        app.query_one("#ref-path", Input).cursor_position = len(f"audio/{demo_name}")
        app.query_one("#ref-status", Static).update("✓ voice.wav → WAV 16 kHz mono · 00:04.88")
        app.query_one("#ref-status", Static).set_class(True, "ok")
        log = app.query_one("#log", RichLog)
        log.clear()
        app._log("SYS", "VoxCLode · VoxCPM2 voice studio")
        app._log("INFO", "Эталон: audio/voice.wav · 48 kHz · 4.88 c")
        app._log("CMD", "ffmpeg … → .cache/converted/voice_16k.wav")
        app._log("OK", "Эталон готов: voice.wav → WAV 16 kHz mono · 4.88 c · 153 КБ")
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
