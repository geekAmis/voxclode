"""Tiny cross-platform audio player.

Windows uses MCI (winmm), which gives pause / resume / seek without extra
native libraries. Linux and macOS use ``ffplay`` (part of FFmpeg) as a
best-effort fallback with restart-based pause and seek.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


class PlayerError(RuntimeError):
    pass


IS_WINDOWS = sys.platform == "win32"


if IS_WINDOWS:

    class MciPlayer:
        """Play WAV files through the Windows MCI API."""

        _ALIAS = "voxclode_player"

        def __init__(self) -> None:
            self._winmm = ctypes.WinDLL("winmm")
            self._mci = self._winmm.mciSendStringW
            self._mci.argtypes = [
                wintypes.LPCWSTR,
                wintypes.LPWSTR,
                wintypes.UINT,
                wintypes.HWND,
            ]
            self._mci.restype = wintypes.UINT
            self._error = self._winmm.mciGetErrorStringW
            self._error.argtypes = [wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
            self._error.restype = wintypes.BOOL
            self._buffer = ctypes.create_unicode_buffer(1024)
            self._path: Path | None = None
            self._duration_ms = 0
            self._open = False

        def _send(self, command: str) -> str:
            code = self._mci(command, self._buffer, len(self._buffer), None)
            if code:
                message = ctypes.create_unicode_buffer(512)
                self._error(code, message, len(message))
                raise PlayerError(f"MCI: {message.value or f'code {code}'} ({command})")
            return self._buffer.value

        def _query(self, command: str, default: str = "") -> str:
            try:
                return self._send(command)
            except PlayerError:
                return default

        @property
        def path(self) -> Path | None:
            return self._path

        @property
        def duration_ms(self) -> int:
            return self._duration_ms

        @property
        def is_open(self) -> bool:
            return self._open

        @property
        def mode(self) -> str:
            return self._query(f"status {self._ALIAS} mode", "closed")

        @property
        def is_playing(self) -> bool:
            return self._open and self.mode == "playing"

        @property
        def position_ms(self) -> int:
            if not self._open:
                return 0
            raw = self._query(f"status {self._ALIAS} position", "0")
            try:
                return max(0, int(float(raw)))
            except ValueError:
                return 0

        def load(self, path: str | Path) -> None:
            path = Path(path).expanduser().resolve()
            if not path.is_file():
                raise PlayerError(f"Файл не найден: {path}")
            self.close()
            self._send(f'open "{path}" type waveaudio alias {self._ALIAS}')
            try:
                self._send(f"set {self._ALIAS} time format milliseconds")
                length = self._query(f"status {self._ALIAS} length", "0")
            except PlayerError:
                self.close()
                raise
            self._path = path
            self._duration_ms = int(float(length or 0))
            self._open = True

        def play(self, from_ms: int | None = None) -> None:
            if not self._open:
                raise PlayerError("Аудио не загружено")
            mode = self.mode
            if mode == "playing":
                return
            if from_ms is not None:
                target = max(0, int(from_ms))
                self._send(f"seek {self._ALIAS} to {target}")
                self._send(f"play {self._ALIAS} from {target}")
            elif mode == "paused":
                self._send(f"resume {self._ALIAS}")
            else:
                position = self.position_ms
                if self._duration_ms and position >= self._duration_ms - 30:
                    position = 0
                self._send(f"play {self._ALIAS} from {position}")

        def pause(self) -> None:
            if self._open and self.mode == "playing":
                self._send(f"pause {self._ALIAS}")

        def toggle(self) -> None:
            if self.is_playing:
                self.pause()
            else:
                self.play()

        def stop(self) -> None:
            if not self._open:
                return
            self._send(f"stop {self._ALIAS}")
            self._send(f"seek {self._ALIAS} to 0")

        def seek(self, position_ms: int) -> None:
            if not self._open:
                return
            position = max(0, min(int(position_ms), self._duration_ms))
            was_playing = self.is_playing
            if was_playing:
                self._send(f"pause {self._ALIAS}")
            self._send(f"seek {self._ALIAS} to {position}")
            if was_playing:
                self._send(f"play {self._ALIAS} from {position}")

        def close(self) -> None:
            if self._open:
                try:
                    self._send(f"close {self._ALIAS}")
                except PlayerError:
                    pass
            self._open = False
            self._path = None
            self._duration_ms = 0

        def wait_until_finished(self, timeout: float = 30.0) -> None:
            deadline = time.monotonic() + timeout
            while self.is_playing and time.monotonic() < deadline:
                time.sleep(0.02)

        def __del__(self) -> None:  # pragma: no cover
            try:
                self.close()
            except Exception:
                pass

else:

    class FfplayPlayer:
        """Best-effort player for Linux/macOS using ffplay."""

        def __init__(self) -> None:
            self._ffplay = shutil.which("ffplay")
            self._path: Path | None = None
            self._duration_ms = 0
            self._anchor_ms = 0
            self._anchor_time = 0.0
            self._paused_position = 0
            self._paused = True
            self._proc: subprocess.Popen[bytes] | None = None
            self._open = False

        @property
        def path(self) -> Path | None:
            return self._path

        @property
        def duration_ms(self) -> int:
            return self._duration_ms

        @property
        def is_open(self) -> bool:
            return self._open

        @property
        def is_playing(self) -> bool:
            return bool(self._proc is not None and self._proc.poll() is None)

        @property
        def mode(self) -> str:
            if self.is_playing:
                return "playing"
            return "paused" if self._open else "closed"

        @property
        def position_ms(self) -> int:
            if self.is_playing:
                elapsed = (time.monotonic() - self._anchor_time) * 1000
                return min(self._duration_ms, int(self._anchor_ms + elapsed))
            if self._open and not self._paused and self._proc is not None:
                return self._duration_ms
            return self._paused_position

        def load(self, path: str | Path) -> None:
            path = Path(path).expanduser().resolve()
            if not path.is_file():
                raise PlayerError(f"Файл не найден: {path}")
            if not self._ffplay:
                raise PlayerError(
                    "ffplay не найден. Установите FFmpeg (ffmpeg/ffplay) для воспроизведения."
                )
            import soundfile as sf

            info = sf.info(str(path))
            self.close()
            self._path = path
            self._duration_ms = int(info.duration * 1000)
            self._paused_position = 0
            self._paused = True
            self._open = True

        def _spawn(self, position_ms: int) -> None:
            self._stop_proc()
            assert self._path is not None and self._ffplay is not None
            self._anchor_ms = max(0, min(position_ms, self._duration_ms))
            self._anchor_time = time.monotonic()
            self._paused = False
            seconds = self._anchor_ms / 1000
            self._proc = subprocess.Popen(
                [
                    self._ffplay,
                    "-nodisp",
                    "-autoexit",
                    "-loglevel",
                    "quiet",
                    "-ss",
                    f"{seconds:.3f}",
                    "-i",
                    str(self._path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        def _stop_proc(self) -> None:
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=1)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            self._proc = None

        def play(self, from_ms: int | None = None) -> None:
            if not self._open:
                raise PlayerError("Аудио не загружено")
            if from_ms is None:
                position = self.position_ms
                if position >= self._duration_ms - 30:
                    position = 0
            else:
                position = int(from_ms)
            self._paused_position = max(0, position)
            self._spawn(self._paused_position)

        def pause(self) -> None:
            if self.is_playing:
                self._paused_position = self.position_ms
                self._stop_proc()
                self._paused = True

        def toggle(self) -> None:
            if self.is_playing:
                self.pause()
            else:
                self.play(self._paused_position)

        def stop(self) -> None:
            self._stop_proc()
            self._paused = True
            self._paused_position = 0

        def seek(self, position_ms: int) -> None:
            if not self._open:
                return
            was_playing = self.is_playing
            position = max(0, min(int(position_ms), self._duration_ms))
            self._stop_proc()
            self._paused_position = position
            if was_playing:
                self._spawn(position)
            else:
                self._paused = True

        def close(self) -> None:
            self._stop_proc()
            self._open = False
            self._path = None
            self._duration_ms = 0
            self._paused_position = 0

        def wait_until_finished(self, timeout: float = 30.0) -> None:
            deadline = time.monotonic() + timeout
            while self.is_playing and time.monotonic() < deadline:
                time.sleep(0.02)

        def __del__(self) -> None:  # pragma: no cover
            try:
                self.close()
            except Exception:
                pass


def create_player():
    return MciPlayer() if IS_WINDOWS else FfplayPlayer()
