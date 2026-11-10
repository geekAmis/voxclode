"""Core tests for VoxCLode (no model weights required).

Run: .venv\\Scripts\\python.exe scripts\\test_voxclode_core.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voxclode.audio import convert_to_wav, parse_path_text, waveform_peaks  # noqa: E402
from voxclode.clipboard import clipboard_audio  # noqa: E402
from voxclode.jobs import GenerationRequest, build_command  # noqa: E402
from voxclode.player import PlayerError, create_player  # noqa: E402


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} failed {detail}")
    print(f"  OK  {name}")


def main() -> int:
    print("VoxCLode core tests")
    print("  python:", sys.version.split()[0])

    sample = ROOT / "audio" / "anna_06.ogg"
    if sample.is_file():
        out = ROOT / ".cache" / "test" / "core_anna.wav"
        conversion = convert_to_wav(sample, out)
        check("ogg → wav", conversion.output.is_file())
        check("16 kHz mono", conversion.info.samplerate == 16000 and conversion.info.channels == 1)
        check("duration ~4.88 s", abs(conversion.info.duration - 4.879) < 0.1, str(conversion.info.duration))
        wave = waveform_peaks(conversion.output)
        check("waveform peaks", len(wave.peaks) > 10 and max(wave.peaks) > 0.5)
    else:
        print("  SKIP ogg conversion (audio/anna_06.ogg not present)")

    quoted = f'"{sample}"'
    if sample.is_file():
        check("parse quoted path", parse_path_text(quoted) == sample)
        check("parse file:// path", parse_path_text(sample.as_uri()) == sample)

    request = GenerationRequest(
        mode="clone",
        text="Привет!",
        output=ROOT / "output" / "test.wav",
        reference=sample,
        device="cpu",
        timesteps=4,
        denoise=False,
        normalize=True,
    )
    command = build_command(request, ROOT)
    check("clone command", command[1] == "clone" and "--reference-audio" in command)
    check("no-denoiser default", "--no-denoiser" in command)
    check("normalize flag", "--normalize" in command)
    check("cache inside project", str(ROOT / ".cache") in " ".join(command))

    design = GenerationRequest(
        mode="design",
        text="Добро пожаловать!",
        output=ROOT / "output" / "design.wav",
        control="Мужской голос, 35 лет",
        device="cpu",
        timesteps=4,
        denoise=True,
    )
    design_cmd = build_command(design, ROOT)
    check("design command", design_cmd[1] == "design" and "--control" in design_cmd)
    check("denoise flag", "--denoise" in design_cmd and "--no-denoiser" not in design_cmd)

    clip_path, note = clipboard_audio()
    check("clipboard readable", isinstance(clip_path, (Path, type(None))) and isinstance(note, (str, type(None))))

    if sample.is_file():
        player = create_player()
        try:
            player.load(ROOT / "audio" / "reference_ru.wav")
            check("player loads", player.is_open and player.duration_ms > 0)
            player.seek(1000)
            check("player seek", abs(player.position_ms - 1000) < 250, str(player.position_ms))
            player.play()
            player.pause()
            check("player pause", not player.is_playing)
        except PlayerError as exc:
            print(f"  SKIP player ({exc})")
        finally:
            player.close()

    print("CORE RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
