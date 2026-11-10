"""Clipboard helpers.

Windows reads Explorer file copies (CF_HDROP) and Unicode text. Other systems
fall back to Tk's text clipboard; file drops still work through terminal paste.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from .audio import AUDIO_EXTENSIONS, parse_path_text

IS_WINDOWS = sys.platform == "win32"

CF_UNICODETEXT = 13
CF_HDROP = 15


if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.argtypes = []
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.GetClipboardData.argtypes = [wintypes.UINT]
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    _user32.IsClipboardFormatAvailable.restype = wintypes.BOOL

    _shell32.DragQueryFileW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPWSTR,
        wintypes.UINT,
    ]
    _shell32.DragQueryFileW.restype = wintypes.UINT

    _kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalLock.restype = wintypes.LPVOID
    _kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalUnlock.restype = wintypes.BOOL

    def _open_clipboard(retries: int = 8) -> bool:
        for _ in range(retries):
            if _user32.OpenClipboard(None):
                return True
            time.sleep(0.03)
        return False

    def get_clipboard_files() -> list[Path]:
        if not _open_clipboard():
            return []
        try:
            handle = _user32.GetClipboardData(CF_HDROP)
            if not handle:
                return []
            count = _shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
            files: list[Path] = []
            for index in range(int(count)):
                length = _shell32.DragQueryFileW(handle, index, None, 0)
                if length <= 0:
                    continue
                buffer = ctypes.create_unicode_buffer(length + 1)
                _shell32.DragQueryFileW(handle, index, buffer, length + 1)
                if buffer.value:
                    files.append(Path(buffer.value))
            return files
        finally:
            _user32.CloseClipboard()

    def get_clipboard_text() -> str:
        if not _open_clipboard():
            return ""
        try:
            if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            handle = _user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = _kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer) or ""
            finally:
                _kernel32.GlobalUnlock(handle)
        finally:
            _user32.CloseClipboard()

else:

    def get_clipboard_files() -> list[Path]:
        return []

    def get_clipboard_text() -> str:
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            try:
                return root.clipboard_get()
            finally:
                root.destroy()
        except Exception:
            return ""


def clipboard_audio() -> tuple[Path | None, str | None]:
    """Best-effort audio path from the clipboard.

    Returns (path, note). ``note`` describes why nothing usable was found.
    """
    files = get_clipboard_files()
    if files:
        for file in files:
            if file.suffix.lower() in AUDIO_EXTENSIONS and file.is_file():
                return file, None
        for file in files:
            if file.is_file():
                return file, "В буфере файл не аудиоформата"
        return None, "В буфере нет доступных файлов"

    text = get_clipboard_text()
    if text.strip():
        path = parse_path_text(text)
        if path and path.suffix.lower() in AUDIO_EXTENSIONS:
            return path, None
        if path:
            return path, "В буфере путь к не-аудиофайлу"
        return None, "В буфере текст, но не путь к существующему файлу"

    return None, "Буфер обмена пуст или недоступен"
