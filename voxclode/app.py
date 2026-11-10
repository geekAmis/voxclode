"""VoxCLode — a colourful terminal studio for VoxCPM2 voice cloning."""

from __future__ import annotations

import importlib.metadata as md
import math
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterable

import numpy as np
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DirectoryTree,
    Footer,
    Input,
    Label,
    ProgressBar,
    RadioButton,
    RadioSet,
    RichLog,
    Select,
    Static,
    TextArea,
)

from .audio import (
    AUDIO_EXTENSIONS,
    AudioError,
    AudioInfo,
    format_bytes,
    format_clock,
    format_duration,
    parse_path_text,
    probe,
    waveform_peaks,
)
from .clipboard import clipboard_audio, get_clipboard_text
from .jobs import (
    PROJECT_ROOT,
    GenerationRequest,
    output_path,
    prepare_reference,
    run_generation,
    voxcpm_executable,
)
from .player import PlayerError, create_player

DEFAULT_TEXT = "Привет! Это мой голос, созданный нейросетью."
DEFAULT_CONTROL = ""

LOGO = r"""
 ██╗   ██╗ ██████╗ ██╗  ██╗ ██████╗██╗      ██████╗ ██████╗ ███████╗
 ██║   ██║██╔═══██╗╚██╗██╔╝██╔════╝██║     ██╔═══██╗██╔══██╗██╔════╝
 ██║   ██║██║   ██║ ╚███╔╝ ██║     ██║     ██║   ██║██║  ██║█████╗
 ╚██╗ ██╔╝██║   ██║ ██╔██╗ ██║     ██║     ██║   ██║██║  ██║██╔══╝
  ╚████╔╝ ╚██████╔╝██╔╝ ██╗╚██████╗███████╗╚██████╔╝██████╔╝███████╗
   ╚═══╝   ╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚══════╝ ╚═════╝ ╚═════╝ ╚══════╝
""".strip("\n")

LOGO_COLORS = ["#22d3ee", "#38bdf8", "#818cf8", "#a78bfa", "#e879f9", "#f472b6"]
BARS = "▁▂▃▄▅▆▇█"

STAGES: dict[str, tuple[float, float, str, float]] = {
    "convert": (0.0, 8.0, "Подготовка эталона", 3.0),
    "load": (8.0, 38.0, "Загрузка модели", 30.0),
    "generate": (38.0, 95.0, "Генерация речи", 120.0),
    "save": (95.0, 99.0, "Сохранение WAV", 2.0),
}


def logo_text() -> Text:
    text = Text()
    for line, color in zip(LOGO.splitlines(), LOGO_COLORS):
        text.append(line, style=f"bold {color}")
        text.append("\n")
    return text


def info_payload(info: AudioInfo) -> dict[str, object]:
    return {
        "path": str(info.path),
        "samplerate": info.samplerate,
        "channels": info.channels,
        "duration": info.duration,
        "size": info.size,
        "format": info.format,
        "subtype": info.subtype,
    }


class AudioTree(DirectoryTree):
    """Directory tree that only shows folders and audio files."""

    def filter_paths(self, paths: Iterable[Path]) -> list[Path]:
        visible: list[Path] = []
        for path in paths:
            try:
                if path.is_dir() or path.suffix.lower() in AUDIO_EXTENSIONS:
                    visible.append(path)
            except OSError:
                continue
        return visible


class FilePickerScreen(ModalScreen[Path | None]):
    BINDINGS = [Binding("escape", "cancel", "Отмена")]

    def __init__(self, start: Path) -> None:
        super().__init__()
        self.start = start

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static("ВЫБЕРИТЕ АУДИОФАЙЛ", classes="picker-title")
            yield Static(f"Корень: {self.start}", classes="tiny")
            yield AudioTree(self.start, id="picker-tree")
            with Horizontal(classes="picker-actions"):
                yield Button("Отмена", id="picker-cancel", variant="error")
                yield Static("Только аудиоформаты · Esc — закрыть", classes="tiny")

    @on(DirectoryTree.FileSelected)
    def _picked(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(Path(event.path))

    @on(Button.Pressed, "#picker-cancel")
    def _cancel_button(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ToggleButton(Button):
    """One-line on/off switch with an explicit ВКЛ / ВЫКЛ label."""

    def __init__(self, value: bool = False, **kwargs: object) -> None:
        super().__init__("ВКЛ" if value else "ВЫКЛ", **kwargs)
        self.value = value
        self.set_class(value, "on")

    def toggle(self) -> None:
        self.value = not self.value
        self.label = "ВКЛ" if self.value else "ВЫКЛ"
        self.set_class(self.value, "on")


class Waveform(Static):
    """Clickable one-line waveform with a play head."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.peaks: list[float] = []
        self.duration = 0.0
        self.position_ms = 0
        self.has_audio = False

    def set_audio(self, peaks: list[float], duration: float) -> None:
        self.peaks = peaks or []
        self.duration = max(0.0, duration)
        self.position_ms = 0
        self.has_audio = True
        self.refresh()

    def set_position(self, position_ms: int) -> None:
        self.position_ms = max(0, int(position_ms))
        self.refresh()

    def _width(self) -> int:
        region = getattr(self, "content_region", None)
        width = int(region.width) if region else 0
        return max(12, width or self.size.width or 40)

    def render(self) -> Text:
        width = self._width()
        if not self.has_audio or not self.peaks:
            return Text(
                "─" * width,
                style="#334155",
            )
        values = np.interp(
            np.linspace(0.0, 1.0, width),
            np.linspace(0.0, 1.0, len(self.peaks)),
            np.asarray(self.peaks, dtype=float),
        )
        cursor = 0
        if self.duration > 0:
            cursor = min(width - 1, int((self.position_ms / 1000) / self.duration * width))

        text = Text()
        for index, value in enumerate(values):
            char = BARS[min(len(BARS) - 1, int(max(0.0, min(1.0, value)) * (len(BARS) - 1)))]
            if index == cursor:
                text.append(char, style="bold #f472b6 on #1e293b")
            elif index < cursor:
                text.append(char, style="#34d399")
            elif value > 0.55:
                text.append(char, style="bold #22d3ee")
            else:
                text.append(char, style="#3b82f6")
        return text

    def on_click(self, event: events.Click) -> None:
        if not self.has_audio or self.duration <= 0:
            return
        if event.screen_x is None:
            return
        rel = event.screen_x - self.content_region.x
        ratio = max(0.0, min(1.0, rel / max(1, self.content_region.width)))
        seek = getattr(self.app, "seek_ratio", None)
        if seek:
            seek(ratio)


class VoxClodeApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "VoxCLode"
    SUB_TITLE = "VoxCPM2 voice studio"

    BINDINGS = [
        Binding("ctrl+v", "paste_audio", "Вставить файл", priority=True),
        Binding("ctrl+g", "generate", "Генерировать", priority=True),
        Binding("ctrl+o", "browse", "Обзор", priority=True),
        Binding("f8", "toggle_play", "Пауза / играть", priority=True),
        Binding("ctrl+q", "quit", "Выход", show=True),
        Binding("ctrl+l", "clear_log", "Очистить лог", show=False),
    ]

    def __init__(self, initial_reference: str | None = None) -> None:
        super().__init__()
        self.root = PROJECT_ROOT
        self.initial_reference = initial_reference
        self.player = create_player()
        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.generation_thread: threading.Thread | None = None
        self.prepare_thread: threading.Thread | None = None
        self._busy = False
        self._stage = ""
        self._stage_started = time.monotonic()
        self._stage_floor = 0.0
        self._device = "cpu"
        self._mode = "clone"
        self._prepared_source: Path | None = None
        self._prepared_wav: Path | None = None
        self._ref_timer = None
        self._last_prompt = ""
        self._last_control = ""
        self._result_path: Path | None = None
        self._closing = False

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(logo_text(), id="logo")
            yield Static("◆ VoxCLode", id="compact-title")
            with Vertical(id="badges"):
                yield Static("VoxCPM2", classes="badge badge-accent")
                yield Static("python 3.12 · torch", id="runtime-badge", classes="badge")
                yield Static("CPU · no CUDA", id="device-badge", classes="badge")

        with Horizontal(id="main"):
            with VerticalScroll(id="sidebar"):
                yield Static("РЕЖИМ", classes="panel-title")
                yield RadioSet(
                    RadioButton("Клонировать голос", value=True, id="mode-clone"),
                    RadioButton("Дизайн голоса", id="mode-design"),
                    id="mode",
                )
                yield Static("Для дизайна эталон не нужен", classes="tiny")

                yield Static("ОПЦИИ", classes="panel-title")
                with Horizontal(classes="switch-row"):
                    yield Static("Шумоподавление", classes="opt-label")
                    yield ToggleButton(id="denoise", classes="toggle", compact=True)
                yield Static("чистит шум в эталоне", classes="tiny")
                with Horizontal(classes="switch-row"):
                    yield Static("Нормализация", classes="opt-label")
                    yield ToggleButton(id="normalize", classes="toggle", compact=True)
                yield Static("нормализация текста", classes="tiny")

            with VerticalScroll(id="workspace"):
                yield Static("1 · ЭТАЛОННЫЙ ГОЛОС", classes="section")
                yield Input(placeholder="Путь к аудио: wav, mp3, ogg, mp4, m4a…", id="ref-path")
                with Horizontal(classes="row"):
                    yield Button("Вставить  Ctrl+V", id="paste", variant="primary", compact=True)
                    yield Button("Обзор", id="browse", compact=True)
                    yield Button("Очистить", id="clear", compact=True)
                yield Static("Эталон не выбран", id="ref-status", classes="tiny")

                yield Static("2 · ТЕКСТ ДЛЯ ГЕНЕРАЦИИ", classes="section")
                yield TextArea(
                    DEFAULT_TEXT,
                    placeholder="Введите фразу, которую должен произнести голос…",
                    id="prompt",
                    classes="prompt-area",
                )

                yield Static("3 · СТИЛЬ / ОПИСАНИЕ ГОЛОСА", classes="section")
                yield TextArea(
                    DEFAULT_CONTROL,
                    placeholder="Например: мужской голос, 35 лет, низкий тембр, спокойная речь",
                    id="control",
                    classes="control-area",
                )

        with Horizontal(id="action-bar"):
            yield Button("▶  СГЕНЕРИРОВАТЬ", id="generate", variant="success")
            yield Button("■  Стоп", id="stop", variant="error", disabled=True)
            yield Select(
                [("CPU", "cpu"), ("GPU · CUDA", "cuda")],
                value="cpu",
                allow_blank=False,
                id="device",
            )
            yield Select(
                [("4 · быстро", 4), ("10 · баланс", 10), ("20 · качество", 20), ("30 · максимум", 30)],
                value=10,
                allow_blank=False,
                id="steps",
            )
            yield Static("Ctrl+G", classes="kbd")
            yield Static(
                "Ctrl+V — вставить аудио · перетащите файл в поле пути",
                classes="hint",
            )

        with Vertical(id="progress-panel"):
            with Horizontal(id="progress-head"):
                yield Static("ПРОГРЕСС · 0%", id="overall-label", classes="panel-title")
                yield Static("ожидание", id="stage-label", classes="stage-label")
            with Horizontal(classes="bar-row"):
                yield Static("общая", classes="bar-label")
                yield ProgressBar(total=100, show_eta=False, id="overall")
            with Horizontal(classes="bar-row"):
                yield Static("этап", classes="bar-label")
                yield ProgressBar(total=100, show_eta=False, id="stage-bar")
            yield RichLog(max_lines=300, wrap=True, markup=False, id="log")

        with Vertical(id="player-panel"):
            with Horizontal(id="track-head"):
                yield Static("РЕЗУЛЬТАТ", classes="panel-title")
                yield Static("", id="track-title", classes="track-title")
            yield Waveform(id="waveform")
            with Horizontal(id="controls"):
                yield Button("⏮  −5 c", id="back", compact=True)
                yield Button("▶  Играть", id="play", variant="success", compact=True)
                yield Button("+5 c  ⏭", id="fwd", compact=True)
                yield Button("⏹", id="stop-play", compact=True)
                yield Static("00:00.00 / 00:00.00", id="play-time")
                yield Static("клик по волне — перемотка", classes="tiny")
            yield Static("", id="prompt-line", classes="prompt-line")

        yield Footer()

    def on_mount(self) -> None:
        torch_short = ".".join(self._pkg_version("torch").split(".")[:2])
        self.query_one("#runtime-badge", Static).update(
            f"python {sys.version_info.major}.{sys.version_info.minor} · torch {torch_short}"
        )
        self._log("SYS", "VoxCLode · VoxCPM2 voice studio")
        self._log("INFO", f"Python {sys.version.split()[0]} · voxcpm {self._pkg_version('voxcpm')} · torch {self._pkg_version('torch')}")
        self._log("INFO", f"Проект: {self.root}")
        self._log("INFO", "Ctrl+V — вставить скопированный файл · Ctrl+G — генерация · Ctrl+P — пауза")

        if not voxcpm_executable(self.root).exists():
            self._log("ERR", "voxcpm.exe не найден. Запустите scripts\\install.ps1")
            self.notify("voxcpm не установлен", severity="error")

        sample = self._default_reference()
        if self.initial_reference:
            candidate = Path(self.initial_reference).expanduser()
            if candidate.is_file():
                sample = candidate
        if sample:
            ref_input = self.query_one("#ref-path", Input)
            ref_input.value = str(sample)
            ref_input.cursor_position = len(ref_input.value)
            self._log("INFO", f"Пример эталона: {sample.name}")

        self.set_interval(0.08, self._drain_events)
        self.set_interval(0.15, self._tick_player)
        self.set_interval(0.5, self._animate_stage)
        self.query_one("#prompt", TextArea).focus()
        self._start_cuda_probe()

    def _default_reference(self) -> Path | None:
        audio = self.root / "audio"
        for name in ("anna_06.ogg", "reference_ru.wav"):
            candidate = audio / name
            if candidate.is_file():
                return candidate
        return None

    def _pkg_version(self, name: str) -> str:
        try:
            return md.version(name)
        except md.PackageNotFoundError:
            return "?"

    # ------------------------------------------------------------- clipboard

    def action_paste_audio(self) -> None:
        path, note = clipboard_audio()
        if path is not None:
            self._set_reference(path)
            self._log("OK", f"Из буфера: {path}")
            self.notify(f"Вставлен файл: {path.name}", severity="information")
            return

        # No file in the clipboard: preserve normal text pasting.
        text = get_clipboard_text()
        focused = self.focused
        if text and isinstance(focused, TextArea):
            focused.insert(text)
            self.notify("Вставлен текст", timeout=2)
            return
        if text and isinstance(focused, Input):
            focused.insert_text_at_cursor(text)
            self.notify("Вставлен текст", timeout=2)
            return
        self._log("WARN", f"Нечего вставлять: {note or 'буфер пуст'}")
        self.notify(note or "Буфер обмена пуст", severity="warning")

    def on_paste(self, event: events.Paste) -> None:
        path = parse_path_text(event.text)
        if path is not None and path.suffix.lower() in AUDIO_EXTENSIONS:
            self._set_reference(path)
            self._log("OK", f"Перетащен/вставлен файл: {path}")
            event.prevent_default()
            event.stop()

    def _set_reference(self, path: Path) -> None:
        ref_input = self.query_one("#ref-path", Input)
        ref_input.value = str(path)
        ref_input.cursor_position = len(ref_input.value)
        self._stage_status("⏳ проверяю эталон…", "pending")
        self._schedule_prepare(str(path), immediate=True)

    # ---------------------------------------------------------- reference UX

    @on(Input.Changed, "#ref-path")
    def _reference_changed(self, event: Input.Changed) -> None:
        self._schedule_prepare(event.value)

    def _schedule_prepare(self, value: str, immediate: bool = False) -> None:
        if self._ref_timer is not None:
            try:
                self._ref_timer.stop()
            except Exception:
                pass
        delay = 0.05 if immediate else 0.55
        self._ref_timer = self.set_timer(delay, lambda: self._prepare_async(value))

    def _prepare_async(self, value: str) -> None:
        path = parse_path_text(value) if value.strip() else None
        if path is None:
            if value.strip():
                self._stage_status("✗ файл не найден", "err")
            else:
                self._stage_status("Эталон не выбран", "")
            return
        if not path.suffix.lower() in AUDIO_EXTENSIONS:
            self._stage_status("✗ это не аудиофайл", "err")
            return
        if self._prepared_source == path and self._prepared_wav and self._prepared_wav.is_file():
            self._show_prepared(self._prepared_wav, self._prepared_source)
            return
        if self.prepare_thread and self.prepare_thread.is_alive():
            return

        self._stage_status(f"⏳ конвертирую {path.name}…", "pending")

        def worker() -> None:
            try:
                conversion = prepare_reference(path, self.root)
                self.events.put(
                    {
                        "type": "ref_done",
                        "source": str(path),
                        "path": str(conversion.output),
                        "info": info_payload(conversion.info),
                        "command": conversion.command,
                        "elapsed": conversion.elapsed,
                        "copied": conversion.copied,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.events.put({"type": "ref_error", "source": str(path), "message": str(exc)})

        self.prepare_thread = threading.Thread(target=worker, daemon=True)
        self.prepare_thread.start()

    def _show_prepared(self, wav: Path, source: Path | None = None) -> None:
        try:
            info = probe(wav)
            prefix = f"{source.name} → " if source is not None and source.name != wav.name else ""
            self._stage_status(
                f"✓ {prefix}WAV 16 kHz mono · {info.duration_human}",
                "ok",
            )
        except AudioError:
            self._stage_status(f"✓ {wav.name}", "ok")

    def _stage_status(self, message: str, kind: str) -> None:
        status = self.query_one("#ref-status", Static)
        status.update(message)
        status.set_class(kind == "ok", "ok")
        status.set_class(kind == "err", "err")
        status.set_class(kind == "pending", "pending")

    # -------------------------------------------------------------- generate

    def action_generate(self) -> None:
        self._start_generation()

    def _start_generation(self) -> None:
        if self._busy:
            self.notify("Генерация уже идёт", severity="warning")
            return

        mode = "design" if self.query_one("#mode", RadioSet).pressed_index == 1 else "clone"
        text = self.query_one("#prompt", TextArea).text.strip()
        control = self.query_one("#control", TextArea).text.strip()
        if not text:
            self._log("ERR", "Текст пуст — введите фразу для генерации")
            self.notify("Введите текст", severity="error")
            self.query_one("#prompt", TextArea).focus()
            return

        reference: Path | None = None
        if mode == "clone":
            ref_text = self.query_one("#ref-path", Input).value
            reference = parse_path_text(ref_text) if ref_text.strip() else None
            if reference is None:
                self._log("ERR", "Для клонирования нужен эталонный аудиофайл")
                self.notify("Укажите эталонный файл", severity="error")
                self.query_one("#ref-path", Input).focus()
                return
            if not reference.suffix.lower() in AUDIO_EXTENSIONS:
                self._log("ERR", f"Не аудиофайл: {reference}")
                return

        device = str(self.query_one("#device", Select).value or "cpu")
        steps = int(self.query_one("#steps", Select).value or 10)
        denoise = bool(self.query_one("#denoise", ToggleButton).value)
        normalize = bool(self.query_one("#normalize", ToggleButton).value)
        target = output_path(self.root, mode)

        request = GenerationRequest(
            mode=mode,
            text=text,
            output=target,
            reference=reference,
            control=control,
            device=device,
            timesteps=steps,
            denoise=denoise,
            normalize=normalize,
        )

        self._last_prompt = text
        self._last_control = control
        self._prepared_source = reference
        self._busy = True
        self.cancel_event.clear()
        self.query_one("#generate", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        self.query_one("#overall", ProgressBar).update(progress=0)
        self.query_one("#stage-bar", ProgressBar).update(progress=0)
        self.query_one("#overall-label", Static).update("ПРОГРЕСС · 0%")
        self.query_one("#stage-label", Static).update("подготовка")
        self._stage = ""
        self._log("SYS", "─" * 64)
        self._log("INFO", f"Режим: {'дизайн голоса' if mode == 'design' else 'клонирование'} · шаги {steps} · {device.upper()}")
        if denoise:
            self._log("WARN", "Шумоподавление включено: будет загружена дополнительная модель")
        self._log("INFO", "Запуск фоновой задачи…")
        self.notify("Генерация запущена", timeout=2)

        def worker() -> None:
            try:
                if mode == "clone" and reference is not None:
                    self.events.put(
                        {
                            "type": "log",
                            "level": "INFO",
                            "message": f"Конвертация эталона: {reference.name}",
                        }
                    )
                    self.events.put({"type": "stage", "stage": "convert", "percent": 0})
                    conversion = prepare_reference(reference, self.root)
                    self.events.put(
                        {
                            "type": "prepared",
                            "source": str(reference),
                            "path": str(conversion.output),
                            "info": info_payload(conversion.info),
                            "command": conversion.command,
                            "elapsed": conversion.elapsed,
                            "copied": conversion.copied,
                        }
                    )
                run_generation(request, self.events, self.cancel_event, self.root)
            except Exception as exc:  # noqa: BLE001
                self.events.put({"type": "error", "message": str(exc)})

        self.generation_thread = threading.Thread(target=worker, daemon=True)
        self.generation_thread.start()

    def action_stop_generation(self) -> None:
        if not self._busy:
            return
        self.cancel_event.set()
        self._log("WARN", "Останавливаю процесс…")
        self.notify("Остановка…", severity="warning", timeout=2)
        self.query_one("#stop", Button).disabled = True

    # ---------------------------------------------------------- event stream

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            kind = event.get("type")
            if kind == "log":
                self._log(str(event.get("level", "INFO")), str(event.get("message", "")))
            elif kind == "command":
                self._log("CMD", str(event.get("display", "")))
            elif kind == "prepared":
                self._handle_prepared(event)
            elif kind == "ref_done":
                self._handle_ref_done(event)
            elif kind == "ref_error":
                self._handle_ref_error(event)
            elif kind == "stage":
                self._set_stage(str(event.get("stage", "")), float(event.get("percent", 0)))
            elif kind == "done":
                self._handle_done(event)
            elif kind == "error":
                self._handle_error(str(event.get("message", "неизвестная ошибка")))
            elif kind == "cancelled":
                self._handle_cancelled()
            elif kind == "cuda":
                self._handle_cuda(bool(event.get("available", False)))

    def _handle_prepared(self, event: dict) -> None:
        source = event.get("source")
        path = Path(str(event.get("path", "")))
        self._prepared_source = Path(source) if source else None
        self._prepared_wav = path
        info = event.get("info", {})
        command = event.get("command")
        if command:
            self._log("CMD", subprocess.list2cmdline([str(part) for part in command]))
        if event.get("copied"):
            self._log("OK", f"WAV уже в нужном формате: {path.name}")
        else:
            duration = float(info.get("duration", 0.0))
            size = int(info.get("size", 0))
            source_name = Path(source).name if source else path.name
            self._log(
                "OK",
                f"Эталон готов: {source_name} → {path.name} · 16 kHz mono · "
                f"{format_duration(duration)} · {format_bytes(size)}",
            )
        self._show_prepared(path, self._prepared_source)
        if self._busy:
            self._set_stage("convert", 100)

    def _handle_ref_done(self, event: dict) -> None:
        source = Path(str(event.get("source", "")))
        current = self.query_one("#ref-path", Input).value
        parsed = parse_path_text(current)
        if parsed is None or parsed.resolve() != source.resolve():
            return
        self._handle_prepared(event)

    def _handle_ref_error(self, event: dict) -> None:
        source = Path(str(event.get("source", "")))
        current = self.query_one("#ref-path", Input).value
        parsed = parse_path_text(current)
        if parsed is not None and parsed.resolve() != source.resolve():
            return
        message = str(event.get("message", "не удалось подготовить эталон"))
        self._stage_status("✗ ошибка конвертации", "err")
        self._log("ERR", message)
        self.notify("Ошибка эталона", severity="error")

    def _set_stage(self, stage: str, percent: float = 0.0) -> None:
        if stage not in STAGES:
            return
        start, end, label, expected = STAGES[stage]
        if stage != self._stage:
            self._stage = stage
            self._stage_started = time.monotonic()
            self._stage_floor = max(0.0, min(99.0, percent))
            self._log("INFO", f"▶ {label}")
            if stage == "load" and not self._model_cached():
                self._log(
                    "WARN",
                    "Веса VoxCPM2 ещё не скачаны: первый запуск загрузит ~4.7 ГБ в .cache",
                )
        else:
            self._stage_floor = max(self._stage_floor, min(99.0, percent))

        stage_bar = self.query_one("#stage-bar", ProgressBar)
        stage_bar.update(progress=max(percent, 0.0))
        overall = start + (end - start) * max(percent, 0.0) / 100.0
        self.query_one("#overall", ProgressBar).update(progress=overall)
        self.query_one("#overall-label", Static).update(f"ПРОГРЕСС · {overall:.0f}%")
        self.query_one("#stage-label", Static).update(label.lower())

    def _animate_stage(self) -> None:
        if not self._busy or self._stage not in STAGES:
            return
        start, end, label, expected = STAGES[self._stage]
        if self._stage == "generate":
            device = str(self.query_one("#device", Select).value or "cpu")
            expected = 25.0 if device == "cuda" else 150.0
        elapsed = time.monotonic() - self._stage_started
        progress = 100.0 * (1.0 - math.exp(-elapsed / max(1.0, expected) * 1.7))
        progress = max(self._stage_floor, min(96.0, progress))
        self.query_one("#stage-bar", ProgressBar).update(progress=progress)
        overall = start + (end - start) * progress / 100.0
        self.query_one("#overall", ProgressBar).update(progress=overall)
        self.query_one("#overall-label", Static).update(f"ПРОГРЕСС · {overall:.0f}%")
        suffix = " · оценка" if self._stage in {"load", "generate"} else ""
        self.query_one("#stage-label", Static).update(f"{label.lower()} · {progress:.0f}%{suffix}")

    def _model_cached(self) -> bool:
        base = self.root / ".cache" / "huggingface" / "hub" / "models--openbmb--VoxCPM2"
        if not base.exists():
            return False
        return any(base.rglob("*.safetensors"))

    def _handle_done(self, event: dict) -> None:
        self._busy = False
        self.query_one("#generate", Button).disabled = False
        self.query_one("#stop", Button).disabled = True
        self.query_one("#overall", ProgressBar).update(progress=100)
        self.query_one("#stage-bar", ProgressBar).update(progress=100)
        self.query_one("#overall-label", Static).update("ПРОГРЕСС · 100%")
        self.query_one("#stage-label", Static).update("готово · 100%")

        path = Path(str(event.get("path", "")))
        info = event.get("info", {})
        peaks = [float(value) for value in event.get("peaks", [])]
        duration = float(info.get("duration", 0.0))
        size = int(info.get("size", 0))
        samplerate = int(info.get("samplerate", 0))

        self._result_path = path
        try:
            self.player.load(path)
        except PlayerError as exc:
            self._log("WARN", f"Плеер не смог открыть файл: {exc}")
        try:
            self.query_one("#waveform", Waveform).set_audio(peaks, duration)
        except Exception:
            pass

        self.query_one("#player-panel").display = True
        self.query_one("#track-title", Static).update(path.name)
        title = (
            f"{path.name} · {samplerate / 1000:.1f} kHz · {format_duration(duration)} · "
            f"{format_bytes(size)}"
        )
        self.query_one("#track-title", Static).update(title)
        self.query_one("#prompt-line", Static).update(f"Промпт: {self._last_prompt}")
        self.query_one("#play-time", Static).update(
            f"00:00.00 / {format_clock(duration * 1000)}"
        )
        self.query_one("#play", Button).label = "▶  Играть"
        self._log("OK", f"Готово: {path} · {format_duration(duration)} · {format_bytes(size)}")
        self.notify("Аудио готово — нажмите ▶", severity="information")
        self.query_one("#log", RichLog).scroll_end(animate=False)

    def _handle_error(self, message: str) -> None:
        self._busy = False
        self.query_one("#generate", Button).disabled = False
        self.query_one("#stop", Button).disabled = True
        self.query_one("#stage-label", Static).update("ошибка")
        self._log("ERR", message)
        lower = message.lower()
        if "cached snapshot" in lower or "local_files_only" in lower or "connection" in lower:
            self._log(
                "WARN",
                "Похоже, веса модели не загружены. Проверьте интернет: первый запуск скачает ~4.7 ГБ.",
            )
        self.notify("Генерация не удалась", severity="error", timeout=6)

    def _handle_cancelled(self) -> None:
        self._busy = False
        self.query_one("#generate", Button).disabled = False
        self.query_one("#stop", Button).disabled = True
        self.query_one("#stage-label", Static).update("отменено")
        self._log("WARN", "Генерация отменена")
        self.notify("Отменено", severity="warning")

    # ---------------------------------------------------------------- player

    def _tick_player(self) -> None:
        if not self.player.is_open:
            return
        position = self.player.position_ms
        duration = self.player.duration_ms
        playing = self.player.is_playing
        try:
            self.query_one("#waveform", Waveform).set_position(position)
        except Exception:
            pass
        self.query_one("#play-time", Static).update(
            f"{format_clock(position)} / {format_clock(duration)}"
        )
        button = self.query_one("#play", Button)
        expected = "⏸  Пауза" if playing else "▶  Играть"
        if str(button.label) != expected:
            button.label = expected
        if not playing and duration > 0 and position >= duration - 100:
            self.query_one("#waveform", Waveform).set_position(duration)

    def _toggle_play(self) -> None:
        if not self.player.is_open:
            self.notify("Сначала сгенерируйте аудио", severity="warning")
            return
        try:
            self.player.toggle()
        except PlayerError as exc:
            self.notify(f"Аудио: {exc}", severity="error", timeout=6)
            return
        self._tick_player()

    def action_toggle_play(self) -> None:
        self._toggle_play()

    def action_seek_back(self) -> None:
        self._seek_by(-5000)

    def action_seek_forward(self) -> None:
        self._seek_by(5000)

    def _seek_by(self, delta_ms: int) -> None:
        if not self.player.is_open:
            return
        try:
            self.player.seek(self.player.position_ms + delta_ms)
            self.player.play()
        except PlayerError as exc:
            self.notify(f"Перемотка: {exc}", severity="error")
            return
        self._tick_player()

    def seek_ratio(self, ratio: float) -> None:
        if not self.player.is_open:
            return
        target = int(max(0.0, min(1.0, ratio)) * self.player.duration_ms)
        try:
            self.player.seek(target)
            self.player.play()
        except PlayerError as exc:
            self.notify(f"Перемотка: {exc}", severity="error")
            return
        self._tick_player()

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    def _log(self, level: str, message: str) -> None:
        colors = {
            "INFO": "#7dd3fc",
            "OK": "#34d399",
            "WARN": "#fbbf24",
            "ERR": "#fb7185",
            "CMD": "#c4b5fd",
            "SYS": "#f472b6",
        }
        color = colors.get(level, "#cbd5e1")
        text = Text()
        text.append(time.strftime("%H:%M:%S "), style="#64748b")
        text.append(f"{level:<4} ", style=f"bold {color}")
        text.append(message, style="bold #fecdd3" if level == "ERR" else "#cbd5e1")
        try:
            self.query_one("#log", RichLog).write(text)
        except Exception:
            pass

    # ----------------------------------------------------------------- misc

    def _start_cuda_probe(self) -> None:
        if os.environ.get("VOXCLODE_NO_CUDA_PROBE") == "1":
            return
        python = Path(sys.executable)
        if not python.exists():
            return

        def worker() -> None:
            try:
                result = subprocess.run(
                    [str(python), "-c", "import torch; print(torch.cuda.is_available())"],
                    capture_output=True,
                    text=True,
                    timeout=90,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                available = result.stdout.strip().lower() == "true"
            except Exception:  # noqa: BLE001
                available = False
            self.events.put({"type": "cuda", "available": available})

        threading.Thread(target=worker, daemon=True).start()

    def _handle_cuda(self, available: bool) -> None:
        badge = self.query_one("#device-badge", Static)
        if available:
            badge.update("CUDA ready")
            badge.set_class(True, "badge-ok")
            self._log("OK", "CUDA доступна: можно выбрать устройство GPU")
        else:
            badge.update("CPU · no CUDA")
            self._log(
                "INFO",
                "CUDA недоступна в текущей сборке torch; генерация пойдёт на CPU",
            )

    def action_browse(self) -> None:
        self.run_worker(self._pick_file())

    async def _pick_file(self) -> None:
        start = self.root / "audio"
        if not start.is_dir():
            start = self.root
        picked = await self.push_screen_wait(FilePickerScreen(start))
        if picked is not None:
            self._set_reference(picked)

    @on(RadioSet.Changed, "#mode")
    def _mode_changed(self, event: RadioSet.Changed) -> None:
        self._mode = "design" if event.index == 1 else "clone"
        button = self.query_one("#generate", Button)
        button.label = "▶  ДИЗАЙН ГОЛОСА" if self._mode == "design" else "▶  СГЕНЕРИРОВАТЬ"
        if self._mode == "design":
            self._stage_status("Для дизайна эталон не нужен — опишите голос ниже", "")
            self.query_one("#control", TextArea).focus()
        else:
            self._schedule_prepare(self.query_one("#ref-path", Input).value)

    @on(Button.Pressed)
    def _button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "generate":
            self._start_generation()
        elif button_id == "stop":
            self.action_stop_generation()
        elif button_id == "paste":
            self.action_paste_audio()
        elif button_id == "browse":
            self.action_browse()
        elif button_id == "clear":
            self.query_one("#ref-path", Input).value = ""
            self._prepared_source = None
            self._prepared_wav = None
            self._stage_status("Эталон не выбран", "")
        elif button_id == "play":
            self._toggle_play()
        elif button_id == "back":
            self._seek_by(-5000)
        elif button_id == "fwd":
            self._seek_by(5000)
        elif button_id in ("denoise", "normalize"):
            if isinstance(event.button, ToggleButton):
                event.button.toggle()
        elif button_id == "stop-play":
            if self.player.is_open:
                self.player.stop()
                self._tick_player()

    def on_resize(self, event: events.Resize) -> None:
        try:
            self.screen.set_class(event.size.width < 100, "narrow")
            self.screen.set_class(event.size.height < 43, "short")
        except Exception:
            pass

    def on_unmount(self) -> None:
        self._closing = True
        self.cancel_event.set()
        try:
            self.player.close()
        except Exception:
            pass
