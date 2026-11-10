"""Audio helpers for VoxCLode: probing, format conversion, waveform peaks."""

from __future__ import annotations

import math
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

AUDIO_EXTENSIONS = {
    ".wav",
    ".wave",
    ".mp3",
    ".ogg",
    ".oga",
    ".opus",
    ".flac",
    ".m4a",
    ".mp4",
    ".aac",
    ".wma",
    ".aiff",
    ".aif",
    ".caf",
    ".webm",
    ".mkv",
    ".mov",
    ".3gp",
    ".amr",
    ".au",
}

_TARGET_RATE = 16000
_TARGET_CHANNELS = 1


class AudioError(RuntimeError):
    """Raised when an audio file cannot be read or converted."""


@dataclass(slots=True)
class AudioInfo:
    path: Path
    samplerate: int
    channels: int
    frames: int
    duration: float
    format: str
    subtype: str
    size: int

    @property
    def size_human(self) -> str:
        return format_bytes(self.size)

    @property
    def rate_human(self) -> str:
        khz = self.samplerate / 1000
        return f"{khz:.1f} kHz".replace(".0 ", " ")

    @property
    def duration_human(self) -> str:
        return format_duration(self.duration)


@dataclass(slots=True)
class Conversion:
    source: Path
    output: Path
    info: AudioInfo
    command: list[str] | None
    elapsed: float
    copied: bool = False


@dataclass(slots=True)
class Waveform:
    peaks: list[float]
    duration: float
    samplerate: int


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} Б"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} КБ"
    return f"{size / (1024 * 1024):.1f} МБ"


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:05.2f}"
    return f"{minutes:02d}:{sec:05.2f}"


def format_clock(milliseconds: float) -> str:
    seconds = max(0.0, milliseconds / 1000)
    minutes, sec = divmod(seconds, 60)
    return f"{int(minutes):02d}:{sec:05.2f}"


def find_ffmpeg() -> str | None:
    """Return an ffmpeg executable path: PATH first, then imageio-ffmpeg bundle."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        candidate = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(candidate).exists():
            return candidate
    except Exception:  # pragma: no cover - optional dependency
        pass
    return None


def probe(path: str | Path) -> AudioInfo:
    path = Path(path).expanduser()
    if not path.exists():
        raise AudioError(f"Файл не найден: {path}")
    if not path.is_file():
        raise AudioError(f"Это не файл: {path}")
    try:
        info = sf.info(str(path))
    except Exception as exc:  # noqa: BLE001
        raise AudioError(f"Не удалось прочитать аудио: {exc}") from exc
    return AudioInfo(
        path=path,
        samplerate=int(info.samplerate),
        channels=int(info.channels),
        frames=int(info.frames),
        duration=float(info.duration),
        format=str(info.format),
        subtype=str(info.subtype),
        size=path.stat().st_size,
    )


def convert_to_wav(
    source: str | Path,
    destination: str | Path,
    *,
    samplerate: int = _TARGET_RATE,
) -> Conversion:
    """Convert any ffmpeg-readable media to 16 kHz mono PCM WAV."""
    source = Path(source).expanduser()
    destination = Path(destination).expanduser()
    if not source.is_file():
        raise AudioError(f"Файл не найден: {source}")
    try:
        source_info: AudioInfo | None = probe(source)
    except AudioError:
        source_info = None
    destination.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()

    # A file that is already in the exact target format can be copied as-is.
    if (
        source_info is not None
        and source.suffix.lower() in {".wav", ".wave"}
        and source_info.samplerate == samplerate
        and source_info.channels == _TARGET_CHANNELS
        and source_info.subtype.upper() in {"PCM_16", "PCM_S16LE", "16-BIT SIGNED INTEGER"}
    ):
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        return Conversion(
            source=source,
            output=destination,
            info=probe(destination),
            command=None,
            elapsed=time.perf_counter() - started,
            copied=True,
        )

    ffmpeg = find_ffmpeg()
    if ffmpeg:
        command = [
            ffmpeg,
            "-nostdin",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-map_metadata",
            "-1",
            "-ac",
            str(_TARGET_CHANNELS),
            "-ar",
            str(samplerate),
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise AudioError("ffmpeg не успел сконвертировать файл за 300 секунд") from exc
        if result.returncode != 0 or not destination.exists():
            detail = (result.stderr or result.stdout or "").strip()
            raise AudioError(f"ffmpeg не смог конвертировать {source.name}: {detail}")
        return Conversion(
            source=source,
            output=destination,
            info=probe(destination),
            command=command,
            elapsed=time.perf_counter() - started,
        )

    # Fallback: libsndfile handles WAV/FLAC/OGG/MP3 without ffmpeg.
    try:
        data, rate = sf.read(str(source), dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        if rate != samplerate:
            try:
                import librosa

                mono = librosa.resample(mono, orig_sr=rate, target_sr=samplerate)
            except Exception:  # noqa: BLE001
                raise AudioError(
                    "ffmpeg не найден, а для ресемплинга недоступен librosa"
                )
        sf.write(str(destination), mono, samplerate, subtype="PCM_16")
    except AudioError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AudioError(f"Не удалось конвертировать {source.name}: {exc}") from exc

    return Conversion(
        source=source,
        output=destination,
        info=probe(destination),
        command=None,
        elapsed=time.perf_counter() - started,
    )


def waveform_peaks(path: str | Path, buckets: int = 240) -> Waveform:
    """Compute normalized peak envelope for a compact terminal waveform."""
    path = Path(path)
    try:
        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001
        raise AudioError(f"Не удалось построить волну: {exc}") from exc

    if data.size == 0:
        return Waveform(peaks=[0.0] * buckets, duration=0.0, samplerate=int(rate))

    mono = np.abs(data).mean(axis=1)
    bucket_count = max(8, min(int(buckets), len(mono)))
    edges = np.linspace(0, len(mono), bucket_count + 1, dtype=np.int64)
    peaks = [
        float(mono[edges[i] : max(edges[i] + 1, edges[i + 1])].max())
        for i in range(bucket_count)
    ]
    top = max(peaks) or 1.0
    normalized = [min(1.0, math.sqrt(value / top)) for value in peaks]
    return Waveform(
        peaks=normalized,
        duration=len(mono) / float(rate),
        samplerate=int(rate),
    )


def path_exists(path_text: str) -> bool:
    try:
        return Path(path_text.strip().strip('"').strip("'")).expanduser().is_file()
    except OSError:
        return False


def is_audio_path(path: str | Path) -> bool:
    try:
        return Path(path).suffix.lower() in AUDIO_EXTENSIONS
    except (OSError, ValueError):
        return False


def parse_path_text(text: str) -> Path | None:
    """Extract an existing file path from pasted/dragged text."""
    if not text:
        return None
    raw_lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
    for raw in raw_lines:
        candidate = raw.strip()
        if candidate.startswith("& "):
            candidate = candidate[2:].strip()
        if candidate.startswith("file://"):
            from urllib.parse import unquote, urlparse

            parsed = urlparse(candidate)
            candidate = unquote(parsed.path)
            if candidate.startswith("/") and len(candidate) > 2 and candidate[2] == ":":
                candidate = candidate[1:]
        candidate = candidate.strip().strip('"').strip("'").strip()
        expanded = Path(candidate).expanduser()
        if expanded.is_file():
            return expanded
    return None
