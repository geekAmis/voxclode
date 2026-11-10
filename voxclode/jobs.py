"""Generation subprocess plumbing and reference-audio preparation."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .audio import AudioError, AudioInfo, Conversion, convert_to_wav, probe, waveform_peaks

Mode = Literal["clone", "design"]

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
_CONVERT_LOCK = threading.Lock()


@dataclass(slots=True)
class GenerationRequest:
    mode: Mode
    text: str
    output: Path
    reference: Path | None = None
    control: str = ""
    device: str = "cpu"
    timesteps: int = 10
    denoise: bool = False
    normalize: bool = False


def voxcpm_executable(root: Path = PROJECT_ROOT) -> Path:
    if sys.platform == "win32":
        return root / ".venv" / "Scripts" / "voxcpm.exe"
    return root / ".venv" / "bin" / "voxcpm"


def output_path(root: Path, mode: Mode) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return root / "output" / f"{mode}_{stamp}.wav"


def build_command(request: GenerationRequest, root: Path = PROJECT_ROOT) -> list[str]:
    exe = voxcpm_executable(root)
    command = [
        str(exe),
        request.mode,
        "--text",
        request.text,
        "--output",
        str(request.output),
        "--device",
        request.device,
        "--inference-timesteps",
        str(request.timesteps),
        "--cache-dir",
        str(root / ".cache" / "huggingface" / "hub"),
    ]
    if request.mode == "clone":
        if request.reference is None:
            raise ValueError("Для клонирования нужен эталонный аудиофайл")
        command += ["--reference-audio", str(request.reference)]
    if request.control.strip():
        command += ["--control", request.control.strip()]
    if request.normalize:
        command.append("--normalize")
    if request.denoise:
        command.append("--denoise")
    else:
        command.append("--no-denoiser")
    return command


def prepare_reference(source: Path, root: Path = PROJECT_ROOT) -> Conversion:
    safe_stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in source.stem)[:48]
    destination = root / ".cache" / "converted" / f"{safe_stem}_16k.wav"
    with _CONVERT_LOCK:
        return convert_to_wav(source, destination)


def _emit(events: queue.Queue, **payload: object) -> None:
    events.put(payload)


def _watch_cancel(proc: subprocess.Popen[str], cancel: threading.Event) -> None:
    while proc.poll() is None:
        if cancel.is_set():
            try:
                proc.terminate()
            except OSError:
                pass
            return
        time.sleep(0.15)


def run_generation(
    request: GenerationRequest,
    events: queue.Queue,
    cancel: threading.Event,
    root: Path = PROJECT_ROOT,
) -> None:
    """Run the VoxCPM CLI and stream human-readable events into ``events``."""
    try:
        command = build_command(request, root)
    except Exception as exc:  # noqa: BLE001
        _emit(events, type="error", message=str(exc))
        return

    request.output.parent.mkdir(parents=True, exist_ok=True)
    _emit(
        events,
        type="command",
        command=command,
        display=subprocess.list2cmdline(command),
    )
    _emit(events, type="stage", stage="load", percent=0)

    env = os.environ.copy()
    env["HF_HOME"] = str(root / ".cache" / "huggingface")
    env["HF_HUB_CACHE"] = str(root / ".cache" / "huggingface" / "hub")
    env["MODELSCOPE_CACHE"] = str(root / ".cache" / "modelscope")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

    try:
        proc = subprocess.Popen(
            command,
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        _emit(
            events,
            type="error",
            message=(
                "Не найден voxcpm.exe. Установите окружение: "
                "scripts\\install.ps1 (или pip install -r requirements.txt)."
            ),
        )
        return
    except OSError as exc:
        _emit(events, type="error", message=f"Не удалось запустить voxcpm: {exc}")
        return

    watcher = threading.Thread(target=_watch_cancel, args=(proc, cancel), daemon=True)
    watcher.start()

    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.rstrip()
        if not line:
            continue
        lower = line.lower()
        if "loading voxcpm model" in lower:
            _emit(events, type="stage", stage="load", percent=4)
        elif "model loaded" in lower:
            _emit(events, type="stage", stage="generate", percent=0)
        elif "saved audio to:" in lower:
            _emit(events, type="stage", stage="save", percent=40)
        _emit(events, type="log", level=_classify(line), message=line)

    return_code = proc.wait()

    if cancel.is_set():
        _emit(events, type="cancelled", message="Генерация остановлена пользователем")
        return

    if return_code != 0:
        _emit(
            events,
            type="error",
            message=f"voxcpm завершился с кодом {return_code}",
        )
        return

    if not request.output.is_file():
        _emit(
            events,
            type="error",
            message="Команда завершилась, но выходной WAV не найден",
        )
        return

    try:
        info = probe(request.output)
        wave = waveform_peaks(request.output)
    except AudioError as exc:
        _emit(events, type="error", message=str(exc))
        return

    _emit(
        events,
        type="done",
        path=str(request.output),
        info=_info_payload(info),
        peaks=wave.peaks,
    )


def _classify(line: str) -> str:
    lower = line.lower()
    if "traceback" in lower or "error" in lower or "failed" in lower:
        return "ERR"
    if "warning" in lower or "deprecated" in lower:
        return "WARN"
    if "saved audio" in lower or "model loaded" in lower:
        return "OK"
    return "INFO"


def _info_payload(info: AudioInfo) -> dict[str, object]:
    return {
        "path": str(info.path),
        "samplerate": info.samplerate,
        "channels": info.channels,
        "duration": info.duration,
        "size": info.size,
        "format": info.format,
        "subtype": info.subtype,
    }
