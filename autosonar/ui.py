"""Tray icon and the window used to map programs to Sonar presets."""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, ttk

import numpy as np

import pystray
from PIL import Image, ImageDraw, ImageFont, ImageTk

from autosonar.config import ConfigStore, Rule, display_exe, exe_key, set_run_at_startup
from autosonar.games import (
    GameSuggestion,
    build_suggestions,
    filter_suggestions,
    find_game_exes,
    installed_steam_app_ids,
    normalize_name,
    suggestion_status,
)
from autosonar.sonar import GamePreset, SonarClient, SonarError
from autosonar.watcher import ProcessWatcher, running_choices

log = logging.getLogger(__name__)

_BG = "#101418"
_CARD = "#1a2129"
_INK = "#f2f5f7"
_MUTED = "#9aa6b2"
_ACCENT = "#2dd4bf"
_ACCENT_DARK = "#14b8a6"
_LINE = "#31404c"
_SELECT = "#134e4a"
_ERROR = "#f0a8a0"
_HEADING = "#24303a"
_RING_GAME = "#2dd4bf"
_RING_LOOKING = "#0f766e"
_RING_MUTED = "#4b5563"


def make_icon(size: int = 64) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = size / 64
    draw.rounded_rectangle((2 * scale, 2 * scale, 62 * scale, 62 * scale), radius=14 * scale, fill=(22, 27, 36, 255))
    draw.rounded_rectangle((14 * scale, 34 * scale, 22 * scale, 48 * scale), radius=3 * scale, fill=(94, 214, 186, 255))
    draw.rounded_rectangle((28 * scale, 18 * scale, 36 * scale, 48 * scale), radius=3 * scale, fill=(94, 214, 186, 255))
    draw.rounded_rectangle((42 * scale, 26 * scale, 50 * scale, 48 * scale), radius=3 * scale, fill=(94, 214, 186, 255))
    return image


def icon_path() -> Path:
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "icon.ico"
        if bundled.is_file():
            return bundled
        return Path(sys.executable).resolve().parent / "icon.ico"
    return Path(__file__).resolve().parent / "icon.ico"


def apply_window_icon(window: tk.Misc) -> ImageTk.PhotoImage:
    path = icon_path()
    if path.is_file():
        try:
            window.iconbitmap(default=str(path))
        except tk.TclError:
            try:
                window.iconbitmap(str(path))
            except tk.TclError:
                pass
    photo = ImageTk.PhotoImage(make_icon(64))
    try:
        window.iconphoto(True, photo)
    except tk.TclError:
        pass
    return photo


def _enable_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return


def _dark_titlebar(window: tk.Misc) -> None:
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        return


def apply_theme(root: tk.Misc) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=_BG, foreground=_INK, font=("Segoe UI", 10))
    style.configure("TFrame", background=_BG)
    style.configure("Card.TFrame", background=_CARD)
    style.configure("TLabel", background=_BG, foreground=_INK)
    style.configure("Card.TLabel", background=_CARD, foreground=_INK)
    style.configure("Muted.TLabel", background=_BG, foreground=_MUTED)
    style.configure("CardMuted.TLabel", background=_CARD, foreground=_MUTED)
    style.configure("Error.TLabel", background=_BG, foreground=_ERROR)
    style.configure("Title.TLabel", background=_BG, foreground=_INK, font=("Segoe UI", 22, "bold"))
    style.configure("Section.TLabel", background=_CARD, foreground=_INK, font=("Segoe UI", 13, "bold"))
    style.configure("AccentText.TLabel", background=_CARD, foreground=_ACCENT, font=("Segoe UI", 10, "bold"))
    style.configure("TCheckbutton", background=_CARD, foreground=_INK)
    style.configure("Dialog.TCheckbutton", background=_BG, foreground=_INK)
    style.configure("TButton", padding=(12, 7), font=("Segoe UI", 10), background=_HEADING, foreground=_INK)
    style.map(
        "TButton",
        background=[("active", "#2c3a46"), ("pressed", _CARD)],
        foreground=[("disabled", _MUTED)],
    )
    style.configure(
        "Accent.TButton",
        background=_ACCENT,
        foreground="#042f2e",
        padding=(14, 8),
        borderwidth=0,
        focuscolor=_ACCENT,
    )
    style.map(
        "Accent.TButton",
        background=[("pressed", _ACCENT_DARK), ("active", "#5eead4"), ("disabled", "#1f3d3a")],
        foreground=[("disabled", "#94a3b8")],
    )
    style.configure(
        "Ghost.TButton",
        background=_BG,
        foreground=_INK,
        padding=(12, 7),
        borderwidth=0,
        focuscolor=_BG,
    )
    style.map("Ghost.TButton", background=[("active", _CARD), ("pressed", _CARD)])
    style.configure(
        "Treeview",
        font=("Segoe UI", 10),
        rowheight=32,
        background=_CARD,
        fieldbackground=_CARD,
        foreground=_INK,
        borderwidth=0,
        relief="flat",
    )
    style.configure(
        "Treeview.Heading",
        font=("Segoe UI", 9, "bold"),
        background=_HEADING,
        foreground=_MUTED,
        relief="flat",
    )
    style.map("Treeview", background=[("selected", _SELECT)], foreground=[("selected", _INK)])
    style.configure(
        "TEntry",
        fieldbackground=_CARD,
        foreground=_INK,
        bordercolor=_LINE,
        lightcolor=_LINE,
        padding=6,
    )
    style.configure(
        "TCombobox",
        fieldbackground=_CARD,
        background=_HEADING,
        foreground=_INK,
        arrowcolor=_INK,
        bordercolor=_LINE,
        padding=4,
    )
    style.map("TCombobox", fieldbackground=[("readonly", _CARD)], foreground=[("readonly", _INK)])
    style.configure(
        "Vertical.TScrollbar",
        background=_HEADING,
        troughcolor=_BG,
        arrowcolor=_MUTED,
        bordercolor=_BG,
    )
    style.configure("HomeStatus.TLabel", background=_BG, foreground="#c5d0da", font=("Segoe UI", 14))
    style.configure(
        "TSpinbox",
        fieldbackground=_CARD,
        foreground=_INK,
        arrowcolor=_INK,
        background=_HEADING,
        bordercolor=_LINE,
    )
    style.configure("CircleCaption.TLabel", background=_BG, foreground=_MUTED, font=("Segoe UI", 11))
    root.configure(bg=_BG)
    root.option_add("*TCombobox*Listbox.background", _CARD)
    root.option_add("*TCombobox*Listbox.foreground", _INK)
    root.option_add("*TCombobox*Listbox.selectBackground", _SELECT)
    root.option_add("*TCombobox*Listbox.selectForeground", _INK)


def _render_glow(state: str, text: str, width: int, height: int) -> Image.Image:
    scale = 3
    width = max(int(width), 1)
    height = max(int(height), 1)
    sharp = _render_glow_sharp(state, text, width * scale, height * scale, scale)
    return sharp.resize((width, height), Image.Resampling.LANCZOS)


def _render_glow_sharp(state: str, text: str, width: int, height: int, scale: int) -> Image.Image:
    if state == "game":
        color = np.array([45, 212, 191], dtype=np.float32)
        strength = 210.0
    elif state == "looking":
        color = np.array([32, 168, 158], dtype=np.float32)
        strength = 150.0
    else:
        color = np.array([130, 144, 160], dtype=np.float32)
        strength = 90.0
    canvas = np.zeros((height, width, 4), dtype=np.float32)
    canvas[..., 0] = 16
    canvas[..., 1] = 20
    canvas[..., 2] = 24
    canvas[..., 3] = 255
    center_x = (width - 1) / 2
    center_y = (height - 1) / 2
    radius = min(width, height) * 0.30
    yy, xx = np.ogrid[0:height, 0:width]
    distance = np.sqrt((xx - center_x) ** 2 + (yy - center_y) ** 2)
    outer = radius * 1.72
    inner = radius * 0.90
    falloff = np.clip(1 - (distance - inner) / max(outer - inner, 1), 0, 1)
    glow = strength * np.power(falloff, 1.7)
    glow = np.where(distance >= outer, 0, glow) / 255.0
    canvas[..., :3] = canvas[..., :3] * (1 - glow[..., None]) + color * glow[..., None]
    edge = 1.6 * scale
    coverage = np.clip((radius + edge / 2 - distance) / edge, 0, 1) * (235 / 255)
    disc = np.array([14, 20, 27], dtype=np.float32)
    canvas[..., :3] = canvas[..., :3] * (1 - coverage[..., None]) + disc * coverage[..., None]
    image = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGBA")
    draw = ImageDraw.Draw(image)
    font_size = (28 if len(text) <= 18 else 20 if len(text) <= 28 else 15) * scale
    font_path = Path(r"C:\Windows\Fonts\segoeuib.ttf")
    font = ImageFont.truetype(str(font_path), font_size) if font_path.is_file() else ImageFont.load_default()
    lines = _wrap_label(text, font, radius * 1.55)
    heights = []
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        heights.append(box[3] - box[1])
    total = sum(heights) + max(0, len(lines) - 1) * 6 * scale
    y = center_y - total / 2
    for line, line_height in zip(lines, heights):
        box = draw.textbbox((0, 0), line, font=font)
        x = center_x - (box[2] - box[0]) / 2
        draw.text((x, y), line, fill=(242, 245, 247, 255), font=font)
        y += line_height + 6 * scale
    return image


def _wrap_label(text: str, font: ImageFont.ImageFont, width: float) -> list[str]:
    words = text.split() or [text]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if font.getlength(trial) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:3]


class SettingsWindow:
    def __init__(self, app: App, *, present: bool = True, page: str = "home") -> None:
        self.app = app
        self.settings = app.store.load()
        self._presets: list[GamePreset] = []
        self._preset_counts: Counter[str] = Counter()
        self._suggestions: list[GameSuggestion] = []
        self._visible_suggestions: list[GameSuggestion] = []
        self._default_presets: list[GamePreset] = []
        self._custom_presets: list[GamePreset] = []
        self._load_generation = 0

        apply_theme(app.root)
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.title("AutoSonar")
        self.win.configure(bg=_BG)
        self.win.minsize(480, 560)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self._has_size = False
        self._wrapping: list[ttk.Label] = []

        self.search_var = tk.StringVar()
        self.show_all_var = tk.BooleanVar(value=False)
        self.default_query = tk.StringVar()
        self.custom_folder = tk.StringVar(value="No folder selected")
        self.custom_title = tk.StringVar()
        self.custom_preset_query = tk.StringVar()
        self._custom_exes: list[str] = []
        self.startup_var = tk.BooleanVar(value=self.settings.start_with_windows)
        self.home_status = tk.StringVar(value="Looking for a game")
        self.default_caption = tk.StringVar(value=self._default_caption())
        self.suggest_caption = tk.StringVar(
            value="Installed and running games are added automatically."
        )
        self.message = tk.StringVar(value="")
        self._page = "home"
        self._mappings_mtime = self._mappings_mtime_now()

        self._build()
        self.search_var.trace_add("write", lambda *_args: self._fill_suggestions())
        self.show_all_var.trace_add("write", lambda *_args: self._fill_suggestions())
        self.default_query.trace_add("write", lambda *_args: self._fill_defaults())
        self.custom_preset_query.trace_add("write", lambda *_args: self._fill_custom_presets())
        self.win.bind("<Escape>", self._on_escape)
        self.refresh()
        if present:
            if page == "settings":
                self.show_settings_page()
            else:
                self.show_home()
        self.win.after(400, self._poll_status)

    def _build(self) -> None:
        self.win.columnconfigure(0, weight=1)
        self.win.rowconfigure(0, weight=1)
        self.home_frame = ttk.Frame(self.win)
        self.settings_frame = ttk.Frame(self.win)
        self._build_home()
        self._build_settings_shell()

    def _build_home(self) -> None:
        self.home_frame.columnconfigure(0, weight=1)
        self.home_frame.rowconfigure(0, weight=1)
        inner = ttk.Frame(self.home_frame)
        inner.grid(row=0, column=0, sticky="ew")
        inner.columnconfigure(0, weight=1)
        self.circle = tk.Canvas(
            inner,
            width=280,
            height=280,
            bg=_BG,
            highlightthickness=0,
            bd=0,
        )
        self.circle.grid(row=0, column=0)
        self.circle.bind("<Configure>", lambda _event: self._draw_circle())
        self.home_status_label = ttk.Label(
            inner,
            textvariable=self.home_status,
            style="HomeStatus.TLabel",
            justify="center",
            anchor="center",
            wraplength=320,
        )
        self.home_status_label.grid(row=1, column=0, sticky="ew", padx=24, pady=(8, 16))
        ttk.Button(inner, text="Settings", style="Accent.TButton", command=self.show_settings_page).grid(
            row=2, column=0, pady=(0, 8)
        )
        self.home_frame.bind("<Configure>", self._layout_home)

    def _build_settings_shell(self) -> None:
        self.settings_frame.columnconfigure(0, weight=1)
        self.settings_frame.rowconfigure(0, weight=1)
        shell = ttk.Frame(self.settings_frame, padding=(20, 16, 20, 14))
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(1, weight=1)
        ttk.Button(header, text="Back", style="Ghost.TButton", command=self.show_home).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Settings", style="Title.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 0))

        scroller = ttk.Frame(shell)
        scroller.grid(row=1, column=0, sticky="nsew")
        scroller.columnconfigure(0, weight=1)
        scroller.rowconfigure(0, weight=1)
        canvas = tk.Canvas(scroller, bg=_BG, highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(scroller, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self._settings_canvas = canvas
        body = ttk.Frame(canvas)
        self._settings_body = body
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.columnconfigure(0, weight=1)

        def _fit_body(event: tk.Event | None = None) -> None:
            width = event.width if event is not None and getattr(event, "widget", None) is canvas else canvas.winfo_width()
            width = max(int(width), 1)
            canvas.itemconfigure(body_window, width=width)
            self._sync_wraps(width)
            canvas.configure(scrollregion=canvas.bbox("all") or (0, 0, width, 1))

        body.bind("<Configure>", lambda _event: _fit_body())
        canvas.bind("<Configure>", _fit_body)

        self._settings_row = 0
        self._build_games_card(body)
        self._build_default_card(body)
        self._build_screensaver(body)
        self._build_startup_card(body)
        self._bind_wheel(self.settings_frame)

        self.message_label = ttk.Label(shell, textvariable=self.message, style="Muted.TLabel", justify="left")
        self.message_label.grid(row=2, column=0, sticky="ew", pady=(10, 0))

    def _next_card(self, body: ttk.Frame) -> ttk.Frame:
        border = tk.Frame(body, bg=_LINE)
        border.grid(row=self._settings_row, column=0, sticky="ew", pady=(0, 12))
        self._settings_row += 1
        border.columnconfigure(0, weight=1)
        card = ttk.Frame(border, style="Card.TFrame", padding=(18, 16))
        card.grid(row=0, column=0, sticky="ew", padx=1, pady=1)
        card.columnconfigure(0, weight=1)
        return card

    def _hint(self, card: ttk.Frame, text: str, row: int) -> ttk.Label:
        label = ttk.Label(card, text=text, style="CardMuted.TLabel", justify="left")
        label.grid(row=row, column=0, sticky="ew", pady=(2, 8))
        self._wrapping.append(label)
        return label

    def _listbox(self, parent: tk.Misc, *, height: int) -> tk.Listbox:
        return tk.Listbox(
            parent,
            height=height,
            activestyle="none",
            exportselection=False,
            font=("Segoe UI", 10),
            bg=_CARD,
            fg=_INK,
            selectbackground=_SELECT,
            selectforeground=_INK,
            highlightthickness=1,
            highlightbackground=_LINE,
            highlightcolor=_ACCENT,
            borderwidth=0,
            relief="flat",
        )

    def _build_games_card(self, body: ttk.Frame) -> None:
        card = self._next_card(body)
        ttk.Label(card, text="Games", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self._hint(card, "These switch the Sonar preset while they are running.", 1)
        map_frame = ttk.Frame(card, style="Card.TFrame")
        map_frame.grid(row=2, column=0, sticky="ew")
        map_frame.columnconfigure(0, weight=1)
        self.rules_tree = ttk.Treeview(
            map_frame,
            columns=("game", "preset"),
            show="headings",
            height=6,
            selectmode="browse",
        )
        for key, title, width in (("game", "Game", 220), ("preset", "Preset", 220)):
            self.rules_tree.heading(key, text=title, anchor="w")
            self.rules_tree.column(key, width=width, anchor="w", stretch=True)
        map_scroll = ttk.Scrollbar(map_frame, orient="vertical", command=self.rules_tree.yview)
        self.rules_tree.configure(yscrollcommand=map_scroll.set)
        self.rules_tree.grid(row=0, column=0, sticky="ew")
        map_scroll.grid(row=0, column=1, sticky="ns")
        self.rules_tree.bind("<Delete>", lambda _event: self._on_remove())
        ttk.Button(card, text="Remove", command=self._on_remove).grid(row=3, column=0, sticky="w", pady=(8, 0))
        self._hint(card, "Installed and running games are added on their own.", 4)
        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=5, column=0, sticky="w")
        ttk.Button(actions, text="Add a game", style="Accent.TButton", command=self._open_add_game).pack(side="left")
        ttk.Button(actions, text="Add an installed game", command=self._open_installed_game).pack(side="left", padx=(8, 0))
        self.reload_rules()

    def _build_default_card(self, body: ttk.Frame) -> None:
        card = self._next_card(body)
        ttk.Label(card, text="When nothing is running", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self._hint(card, "Click a preset to use it until a game starts.", 1)
        caption_row = ttk.Frame(card, style="Card.TFrame")
        caption_row.grid(row=2, column=0, sticky="ew")
        caption_row.columnconfigure(0, weight=1)
        ttk.Label(caption_row, textvariable=self.default_caption, style="AccentText.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(caption_row, text="Clear", command=self._on_clear_default).grid(row=0, column=1, padx=(8, 0))
        ttk.Entry(card, textvariable=self.default_query).grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.default_list = self._listbox(card, height=6)
        self.default_list.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.default_list.bind("<ButtonRelease-1>", self._on_choose_default)

    def _build_startup_card(self, body: ttk.Frame) -> None:
        card = self._next_card(body)
        ttk.Label(card, text="Startup", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            card,
            text="Start AutoSonar with Windows",
            variable=self.startup_var,
            command=self._on_startup,
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._hint(card, "Chat, mic, media, and aux stay put. Turn off QuickSet for these same games.", 2)

    def _build_screensaver(self, body: ttk.Frame) -> None:
        from autosonar.screensaver import load_settings

        self._saver = load_settings()
        card = self._next_card(body)
        ttk.Label(card, text="Dock screensaver", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self._hint(
            card,
            "Plays on the Nova Pro dock after it has been still. It stays off while media is playing. Use 0 minutes to turn it off.",
            1,
        )
        controls = ttk.Frame(card, style="Card.TFrame")
        controls.grid(row=2, column=0, sticky="ew")
        controls.columnconfigure(3, weight=1)
        ttk.Label(controls, text="Minutes", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.saver_minutes = ttk.Spinbox(controls, from_=0, to=180, width=5, command=self._save_screensaver)
        self.saver_minutes.set(str(self._saver.idle_minutes))
        self.saver_minutes.grid(row=0, column=1, sticky="w", padx=(8, 16))
        self.saver_minutes.bind("<FocusOut>", lambda _event: self._save_screensaver())
        self.saver_minutes.bind("<Return>", lambda _event: self._save_screensaver())
        ttk.Label(controls, text="Plays", style="Card.TLabel").grid(row=0, column=2, sticky="w")
        self.saver_choice = ttk.Combobox(controls, state="readonly", width=12)
        self.saver_choice.grid(row=0, column=3, sticky="ew", padx=(8, 0))
        self.saver_choice.bind("<<ComboboxSelected>>", lambda _event: self._save_screensaver())
        buttons = ttk.Frame(card, style="Card.TFrame")
        buttons.grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Test", style="Accent.TButton", command=self._test_screensaver).pack(side="left")
        ttk.Button(buttons, text="Add clip", command=self._add_screensaver_clip).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Remove clip", command=self._remove_screensaver_clip).pack(side="left", padx=(8, 0))
        self._saver_ids: list[str] = []
        self._refresh_saver_choices()

    def _refresh_saver_choices(self) -> None:
        from autosonar.screensaver import BUILTINS, list_media

        labels = {
            "starfield": "Starfield",
            "matrix": "Matrix rain",
            "bounce": "Bouncing mark",
            "clock": "Clock",
            "plasma": "Plasma",
            "shuffle": "Shuffle",
        }
        self._saver_ids = list(BUILTINS) + list_media()
        self.saver_choice["values"] = [labels.get(item, item) for item in self._saver_ids]
        if self._saver.choice in self._saver_ids:
            self.saver_choice.current(self._saver_ids.index(self._saver.choice))
        elif self._saver_ids:
            self.saver_choice.current(0)

    def _save_screensaver(self) -> None:
        from autosonar.oled import reload_screen_settings
        from autosonar.screensaver import ScreenSettings, save_settings

        try:
            minutes = int(float(self.saver_minutes.get()))
        except ValueError:
            minutes = self._saver.idle_minutes
        minutes = max(0, min(180, minutes))
        index = self.saver_choice.current()
        choice = self._saver_ids[index] if 0 <= index < len(self._saver_ids) else self._saver.choice
        self._saver = ScreenSettings(idle_minutes=minutes, choice=choice)
        try:
            save_settings(self._saver)
        except OSError:
            log.exception("Could not save the screensaver")
            self._message("Could not save the screensaver.", error=True)
            return
        reload_screen_settings()

    def _test_screensaver(self) -> None:
        from autosonar.oled import screen

        self._save_screensaver()
        screen.preview()
        self._message("Playing it on the dock for a few seconds.")

    def _add_screensaver_clip(self) -> None:
        from autosonar.screensaver import add_media

        selected = filedialog.askopenfilename(
            title="Add a screensaver clip",
            parent=self.win,
            filetypes=[("GIF or video", "*.gif *.mp4 *.webm *.mov *.mkv *.avi")],
        )
        if not selected:
            return
        try:
            name = add_media(Path(selected))
        except (OSError, ValueError) as exc:
            self._message(str(exc), error=True)
            return
        self._saver.choice = name
        self._refresh_saver_choices()
        self._save_screensaver()
        self._message(f"Added {name}.")

    def _remove_screensaver_clip(self) -> None:
        from autosonar.screensaver import BUILTINS, remove_media

        if self._saver.choice in BUILTINS:
            self._message("Choose one of your clips to remove it.")
            return
        remove_media(self._saver.choice)
        self._saver.choice = "starfield"
        self._refresh_saver_choices()
        self._save_screensaver()
        self._message("Clip removed.")

    def show_home(self) -> None:
        self._page = "home"
        self.settings_frame.grid_remove()
        self.home_frame.grid(row=0, column=0, sticky="nsew")
        self._ensure_size()
        self._present()
        self._layout_home()
        self._draw_circle()

    def show_settings_page(self) -> None:
        self._page = "settings"
        self.home_frame.grid_remove()
        self.settings_frame.grid(row=0, column=0, sticky="nsew")
        self._ensure_size()
        self._present()
        self.win.update_idletasks()
        self._sync_wraps(self._settings_canvas.winfo_width())

    def _ensure_size(self) -> None:
        self.win.minsize(480, 560)
        if self._has_size:
            return
        self._has_size = True
        width = min(560, max(480, self.win.winfo_screenwidth() - 40))
        height = min(700, max(560, self.win.winfo_screenheight() - 80))
        x = max(0, (self.win.winfo_screenwidth() - width) // 2)
        y = max(0, (self.win.winfo_screenheight() - height) // 2)
        self.win.geometry(f"{width}x{height}+{x}+{y}")

    def _layout_home(self, _event: object = None) -> None:
        if not self._alive_widget("circle"):
            return
        width = self.home_frame.winfo_width()
        height = self.home_frame.winfo_height()
        if width < 2 or height < 2:
            return
        size = int(max(220, min(420, width - 48, height - 160)))
        if size != getattr(self, "_circle_size", None):
            self._circle_size = size
            self.circle.configure(width=size, height=size)
        wrap = max(200, width - 48)
        if wrap != getattr(self, "_home_wrap", None) and self._alive_widget("home_status_label"):
            self._home_wrap = wrap
            self.home_status_label.configure(wraplength=wrap)

    def _sync_wraps(self, width: int) -> None:
        wrap = max(200, int(width) - 56)
        if wrap != getattr(self, "_settings_wrap", None):
            self._settings_wrap = wrap
            for label in self._wrapping:
                if self._widget_ok(label):
                    label.configure(wraplength=wrap)
        if self._alive_widget("message_label"):
            message_wrap = max(200, self.win.winfo_width() - 48)
            if message_wrap != getattr(self, "_message_wrap", None):
                self._message_wrap = message_wrap
                self.message_label.configure(wraplength=message_wrap)

    def _bind_wheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_settings_wheel)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _on_settings_wheel(self, event: tk.Event) -> str:
        if self.win.grab_current() is not None:
            return ""
        widget = event.widget
        steps = int(-event.delta / 120)
        if isinstance(widget, tk.Listbox) and widget.size() > int(widget.cget("height")):
            widget.yview_scroll(steps, "units")
            return "break"
        if isinstance(widget, ttk.Treeview):
            first, last = widget.yview()
            if first > 0.001 or last < 0.999:
                widget.yview_scroll(steps, "units")
                return "break"
        self._settings_canvas.yview_scroll(steps, "units")
        return "break"

    def _widget_ok(self, widget: tk.Misc | None) -> bool:
        if widget is None:
            return False
        try:
            return bool(widget.winfo_exists())
        except tk.TclError:
            return False

    def _alive_widget(self, name: str) -> bool:
        return self._widget_ok(getattr(self, name, None))

    def _present(self) -> None:
        self._window_icon = apply_window_icon(self.win)
        self.win.deiconify()
        self.win.lift()
        self.win.update_idletasks()
        _dark_titlebar(self.win)

    def _draw_circle(self) -> None:
        if not hasattr(self, "circle"):
            return
        try:
            if not self.circle.winfo_exists():
                return
        except tk.TclError:
            return
        snap = self.app.watcher.snapshot()
        self.home_status.set(snap.sentence)
        canvas = self.circle
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 2 or height < 2:
            return
        key = (snap.state, snap.circle_text, width, height)
        if getattr(self, "_glow_key", None) != key:
            image = _render_glow(snap.state, snap.circle_text, width, height)
            self._glow_photo = ImageTk.PhotoImage(image)
            self._glow_key = key
        canvas.create_image(width / 2, height / 2, image=self._glow_photo)

    def hide(self) -> None:
        self.win.withdraw()

    def _on_escape(self, _event: object = None) -> None:
        if self._page == "settings":
            self.show_home()
        else:
            self.hide()

    def refresh(self) -> None:
        self._load_generation += 1
        generation = self._load_generation
        self._message("Looking up your games…")

        def work() -> None:
            try:
                presets = self.app.sonar.list_game_presets(force=True)
                processes = running_choices()
                installed = installed_steam_app_ids()
                suggestions = build_suggestions(presets, processes, installed)
            except SonarError as exc:
                self._post(lambda message=str(exc): self._load_failed(generation, message))
                return
            except Exception:
                log.exception("Could not build game suggestions")
                self._post(lambda: self._load_failed(generation, "Could not load games from Sonar."))
                return
            self._post(lambda items=presets, found=suggestions: self._load_ready(generation, items, found))

        threading.Thread(target=work, name="autosonar-refresh", daemon=True).start()

    def reload_rules(self) -> None:
        children = self.rules_tree.get_children()
        if children:
            self.rules_tree.delete(*children)
        for index, rule in enumerate(self.settings.rules):
            self.rules_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(self._game_label(rule), rule.profile_name or rule.profile_id),
            )
        self.default_caption.set(self._default_caption())
        self._fill_suggestions()

    def add_suggestion(self, item: GameSuggestion) -> str | None:
        self._pull_settings()
        rule = Rule(
            exe=display_exe(item.exe),
            profile_id=item.preset_id,
            profile_name=item.preset_name,
            title_contains=item.title_contains,
        )
        self.settings.upsert_rule(rule)
        if not self._save():
            return "Could not save mappings."
        self.reload_rules()
        return None

    def set_default_preset(self, preset: GamePreset) -> str | None:
        self._pull_settings()
        self.settings.default_profile_id = preset.id
        self.settings.default_profile_name = preset.name
        if not self._save():
            return "Could not save mappings."
        self.default_caption.set(self._default_caption())
        self._fill_defaults()
        return None

    def clear_default_preset(self) -> str | None:
        self._pull_settings()
        self.settings.default_profile_id = ""
        self.settings.default_profile_name = ""
        if not self._save():
            return "Could not save mappings."
        self.default_caption.set(self._default_caption())
        self._fill_defaults()
        return None

    def add_custom_rule(self) -> str | None:
        preset = self._selected_custom_preset()
        exe = self._selected_custom_exe()
        if not exe:
            return "Browse to the game folder and choose its program."
        if preset is None:
            return "Choose a Sonar preset from the list."
        self._pull_settings()
        title = self.custom_title.get().strip()
        rule = Rule(
            exe=exe,
            profile_id=preset.id,
            profile_name=preset.name,
            title_contains=title,
        )
        self.settings.upsert_rule(rule)
        if not self._save():
            return "Could not save mappings."
        self.custom_folder.set("No folder selected")
        self.custom_title.set("")
        self.custom_preset_query.set("")
        self._custom_exes = []
        if self._alive_widget("custom_exe_list"):
            self.custom_exe_list.delete(0, tk.END)
        self.reload_rules()
        return None

    def _browse_game_folder(self, parent: tk.Misc | None = None) -> None:
        if not self._alive_widget("custom_exe_list"):
            return
        grab = self.win.grab_current()
        if grab is not None:
            grab.grab_release()
        try:
            selected = filedialog.askdirectory(title="Choose the game folder", parent=parent or self.win)
        finally:
            if grab is not None and self._widget_ok(grab):
                grab.grab_set()
        if not selected:
            return
        folder = Path(selected)
        self.custom_folder.set(folder.name)
        self._custom_exes = find_game_exes(folder)
        self.custom_exe_list.delete(0, tk.END)
        for name in self._custom_exes:
            self.custom_exe_list.insert(tk.END, name)
        several = len(self._custom_exes) > 1
        if self._alive_widget("_exe_label"):
            self._exe_label.grid() if several else self._exe_label.grid_remove()
        self.custom_exe_list.grid() if several else self.custom_exe_list.grid_remove()
        if len(self._custom_exes) == 1:
            self.custom_exe_list.selection_set(0)
            self._message(f"Using {self._custom_exes[0]}.")
        elif not self._custom_exes:
            self._message("No program was found there. Choose the folder that contains the game.", error=True)
        else:
            self.custom_exe_list.selection_clear(0, tk.END)
            self._message("Several programs were found. Choose the game itself.")

    def _selected_custom_exe(self) -> str:
        if not self._alive_widget("custom_exe_list"):
            return ""
        selection = self.custom_exe_list.curselection()
        if not selection:
            return ""
        index = int(selection[0])
        if index < 0 or index >= len(self._custom_exes):
            return ""
        return self._custom_exes[index]

    def remove_selected_rule(self) -> str | None:
        selection = self.rules_tree.selection()
        if not selection:
            return "Select a game first."
        index = int(selection[0])
        if index < 0 or index >= len(self.settings.rules):
            return "Select a game first."
        exe = self.settings.rules[index].exe
        self._pull_settings()
        for position, rule in enumerate(self.settings.rules):
            if exe_key(rule.exe) != exe_key(exe):
                continue
            self.settings.dismiss_exe(rule.exe)
            del self.settings.rules[position]
            break
        else:
            return "Select a game first."
        if not self._save():
            return "Could not save mappings."
        self.reload_rules()
        return None

    def _dialog(self, title: str, width: int, height: int) -> tk.Toplevel:
        dialog = tk.Toplevel(self.win)
        dialog.title(title)
        dialog.configure(bg=_BG)
        dialog.transient(self.win)
        dialog.minsize(420, 420)
        self.win.update_idletasks()
        x = max(0, self.win.winfo_rootx() + (self.win.winfo_width() - width) // 2)
        y = max(0, self.win.winfo_rooty() + (self.win.winfo_height() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        dialog.grab_set()
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.after(20, lambda: _dark_titlebar(dialog))
        return dialog

    def _bind_list_wheel(self, widget: tk.Misc) -> None:
        def _wheel(event: tk.Event, target: tk.Misc = widget) -> str:
            target.yview_scroll(int(-event.delta / 120), "units")
            return "break"

        widget.bind("<MouseWheel>", _wheel)

    def _open_add_game(self) -> None:
        dialog = self._dialog("Add a game", 520, 580)
        self._add_dialog = dialog
        frame = ttk.Frame(dialog, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(6, weight=1)
        ttk.Label(
            frame,
            text="Browse to the game folder, then choose the Sonar preset.",
            style="Muted.TLabel",
            wraplength=460,
        ).grid(row=0, column=0, sticky="ew")
        folder = ttk.Frame(frame)
        folder.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        folder.columnconfigure(0, weight=1)
        ttk.Label(folder, textvariable=self.custom_folder, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(folder, text="Browse folder", command=lambda: self._browse_game_folder(dialog)).grid(
            row=0, column=1, padx=(8, 0)
        )
        self._exe_label = ttk.Label(frame, text="Choose the program", style="Muted.TLabel")
        self._exe_label.grid(row=2, column=0, sticky="w", pady=(10, 0))
        self._exe_label.grid_remove()
        self.custom_exe_list = self._listbox(frame, height=4)
        self.custom_exe_list.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.custom_exe_list.grid_remove()
        self._bind_list_wheel(self.custom_exe_list)
        ttk.Label(frame, text="Only when the window title contains", style="Muted.TLabel").grid(
            row=4, column=0, sticky="w", pady=(12, 0)
        )
        ttk.Entry(frame, textvariable=self.custom_title).grid(row=5, column=0, sticky="ew", pady=(4, 0))
        preset_box = ttk.Frame(frame)
        preset_box.grid(row=6, column=0, sticky="nsew", pady=(12, 0))
        preset_box.columnconfigure(0, weight=1)
        preset_box.rowconfigure(2, weight=1)
        ttk.Label(preset_box, text="Sonar preset", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(preset_box, textvariable=self.custom_preset_query).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.custom_preset_list = self._listbox(preset_box, height=8)
        self.custom_preset_list.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        self._bind_list_wheel(self.custom_preset_list)
        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, sticky="w", pady=(12, 0))
        ttk.Button(buttons, text="Add", style="Accent.TButton", command=self._on_add_custom).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=(8, 0))
        self.custom_folder.set("No folder selected")
        self.custom_title.set("")
        self.custom_preset_query.set("")
        self._custom_exes = []
        self._fill_custom_presets()

    def _open_installed_game(self) -> None:
        dialog = self._dialog("Add an installed game", 640, 520)
        frame = ttk.Frame(dialog, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        caption = ttk.Label(frame, textvariable=self.suggest_caption, style="Muted.TLabel", justify="left")
        caption.grid(row=0, column=0, sticky="ew")
        dialog.bind("<Configure>", lambda event: caption.configure(wraplength=max(200, event.width - 40)))
        search = ttk.Frame(frame)
        search.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        search.columnconfigure(0, weight=1)
        entry = ttk.Entry(search, textvariable=self.search_var)
        entry.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(
            search,
            text="Show every common game",
            style="Dialog.TCheckbutton",
            variable=self.show_all_var,
        ).grid(row=0, column=1, padx=(12, 0))
        tree_frame = ttk.Frame(frame)
        tree_frame.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.suggestion_tree = ttk.Treeview(
            tree_frame,
            columns=("game", "preset", "status"),
            show="headings",
            height=10,
            selectmode="browse",
        )
        for key, title, width in (("game", "Game", 180), ("preset", "Preset", 180), ("status", "Status", 120)):
            self.suggestion_tree.heading(key, text=title, anchor="w")
            self.suggestion_tree.column(key, width=width, anchor="w", stretch=key != "status")
        self.suggestion_tree.tag_configure("running", foreground=_ACCENT)
        self.suggestion_tree.tag_configure("added", foreground=_MUTED)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.suggestion_tree.yview)
        self.suggestion_tree.configure(yscrollcommand=scroll.set)
        self.suggestion_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.suggestion_tree.bind("<Double-1>", self._on_suggestion_activate)
        self.suggestion_tree.bind("<Return>", self._on_suggestion_activate)
        self._bind_list_wheel(self.suggestion_tree)
        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="Add", style="Accent.TButton", command=self._on_add_selected).pack(side="left")
        ttk.Button(buttons, text="Refresh", command=self.refresh).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side="right")
        self.search_var.set("")
        self.show_all_var.set(False)
        self._fill_suggestions()
        entry.focus_set()

    def _on_add_selected(self) -> None:
        item = self._selected_suggestion()
        if item is None:
            self._message("Select a game first.", error=True)
            return
        error = self.add_suggestion(item)
        self._message(error or f"Added {item.name}.", error=bool(error))

    def _on_add_custom(self) -> None:
        exe = self._selected_custom_exe()
        error = self.add_custom_rule()
        if error:
            if error != "Could not save mappings.":
                self._message(error, error=True)
            return
        self._message(f"Added {exe}.")
        if self._alive_widget("_add_dialog"):
            self._add_dialog.destroy()

    def _on_remove(self) -> None:
        error = self.remove_selected_rule()
        if error == "Could not save mappings.":
            return
        self._message(error or "Removed.", error=bool(error))

    def _on_choose_default(self, _event: object = None) -> None:
        selection = self.default_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        if index < 0 or index >= len(self._default_presets):
            return
        preset = self._default_presets[index]
        error = self.set_default_preset(preset)
        self._message(error or f"Default preset is {preset.name}.", error=bool(error))

    def _on_clear_default(self) -> None:
        error = self.clear_default_preset()
        if error == "Could not save mappings.":
            return
        self._message(error or "Default preset cleared.", error=bool(error))

    def _on_suggestion_activate(self, event: tk.Event) -> None:
        if event.type == tk.EventType.ButtonPress:
            region = self.suggestion_tree.identify_region(event.x, event.y)
            if region not in {"cell", "tree"}:
                return
        self._on_add_selected()

    def _on_startup(self) -> None:
        enabled = bool(self.startup_var.get())
        previous = self.settings.start_with_windows
        try:
            set_run_at_startup(enabled)
        except OSError:
            self.startup_var.set(previous)
            self._message("Could not change the Windows startup setting.", error=True)
            return
        self._pull_settings()
        self.settings.start_with_windows = enabled
        if not self._save():
            self.startup_var.set(previous)
            self.settings.start_with_windows = previous
            return
        if enabled:
            self._message("AutoSonar will start with Windows.")
        else:
            self._message("AutoSonar will not start with Windows.")

    def _load_ready(self, generation: int, presets: list[GamePreset], suggestions: list[GameSuggestion]) -> None:
        if generation != self._load_generation or not self._alive():
            return
        self._presets = presets
        self._preset_counts = Counter(preset.name.casefold() for preset in presets)
        self._suggestions = suggestions
        self.reload_rules()
        self._fill_defaults()
        self._fill_custom_presets()
        self._message("")

    def _load_failed(self, generation: int, message: str) -> None:
        if generation != self._load_generation or not self._alive():
            return
        self.suggest_caption.set(message)
        self._message(message, error=True)

    def _fill_suggestions(self) -> None:
        if not self._alive_widget("suggestion_tree"):
            return
        added = self._added_exes()
        self._visible_suggestions = filter_suggestions(
            self._suggestions,
            self.search_var.get(),
            show_all=bool(self.show_all_var.get()),
            added_exes=added,
        )
        children = self.suggestion_tree.get_children()
        if children:
            self.suggestion_tree.delete(*children)
        for index, item in enumerate(self._visible_suggestions):
            tags: list[str] = []
            if exe_key(item.exe) in added:
                tags.append("added")
            elif item.running:
                tags.append("running")
            preset = f"★ {item.preset_name}" if item.preset_favorite else item.preset_name
            self.suggestion_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(item.name, preset, suggestion_status(item, added=exe_key(item.exe) in added)),
                tags=tuple(tags),
            )
        shown = len(self._visible_suggestions)
        total = len(self._suggestions)
        if self.show_all_var.get() or self.search_var.get().strip():
            self.suggest_caption.set(f"Showing {shown} of {total}. Installed and running games are added on their own.")
        elif shown:
            self.suggest_caption.set(
                "Installed and running games are added automatically. Double-click another game to add it."
            )
        elif total:
            self.suggest_caption.set("No installed games yet. Show every common game to add one yourself.")
        else:
            self.suggest_caption.set("Installed and running games are added automatically.")

    def _fill_defaults(self) -> None:
        if not hasattr(self, "default_list"):
            return
        self._default_presets = self._matching_presets(self.default_query.get(), favorites_when_empty=True, limit=30)
        self.default_list.delete(0, tk.END)
        for preset in self._default_presets:
            self.default_list.insert(tk.END, self._preset_label(preset))
            if preset.id == self.settings.default_profile_id:
                index = self.default_list.size() - 1
                self.default_list.itemconfig(index, foreground=_ACCENT)
                self.default_list.selection_set(index)

    def _fill_custom_presets(self) -> None:
        if not self._alive_widget("custom_preset_list"):
            return
        self._custom_presets = self._matching_presets(
            self.custom_preset_query.get(),
            favorites_when_empty=False,
            limit=1000,
        )
        self._custom_presets.sort(key=lambda preset: (0 if preset.is_favorite else 1, preset.name.casefold()))
        self.custom_preset_list.delete(0, tk.END)
        for preset in self._custom_presets:
            self.custom_preset_list.insert(tk.END, self._preset_label(preset))

    def _matching_presets(self, query: str, *, favorites_when_empty: bool, limit: int) -> list[GamePreset]:
        cleaned = normalize_name(query.lstrip("★").strip())
        if not cleaned and favorites_when_empty:
            favorites = [preset for preset in self._presets if preset.is_favorite]
            if favorites:
                return favorites[:limit]
        matched = [
            preset
            for preset in self._presets
            if not cleaned or cleaned in normalize_name(preset.name)
        ]
        return matched[:limit]

    def _selected_suggestion(self) -> GameSuggestion | None:
        if not self._alive_widget("suggestion_tree"):
            return None
        selection = self.suggestion_tree.selection()
        if not selection:
            return None
        index = int(selection[0])
        if index < 0 or index >= len(self._visible_suggestions):
            return None
        return self._visible_suggestions[index]

    def _selected_custom_preset(self) -> GamePreset | None:
        if not self._alive_widget("custom_preset_list"):
            return None
        selection = self.custom_preset_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if index < 0 or index >= len(self._custom_presets):
            return None
        return self._custom_presets[index]

    def _preset_label(self, preset: GamePreset) -> str:
        star = "★ " if preset.is_favorite else ""
        if self._preset_counts[preset.name.casefold()] > 1:
            return f"{star}{preset.name} ({preset.id[:8]})"
        return f"{star}{preset.name}"

    def _added_exes(self) -> set[str]:
        return {exe_key(rule.exe) for rule in self.settings.rules}

    def _game_label(self, rule: Rule) -> str:
        if rule.title_contains:
            return f"{Path(rule.exe).stem}  ·  {rule.title_contains}"
        key = exe_key(rule.exe)
        for item in self._suggestions:
            if exe_key(item.exe) == key:
                return item.name
        return Path(rule.exe).stem

    def _default_caption(self) -> str:
        if self.settings.default_profile_name:
            return f"Using {self.settings.default_profile_name}"
        return "No default selected yet"

    def _poll_status(self) -> None:
        if not self._alive():
            return
        self._sync_mappings()
        if self._page == "home":
            self._draw_circle()
        self.win.after(400, self._poll_status)

    def _mappings_mtime_now(self) -> float:
        path = self.app.store.path
        try:
            return path.stat().st_mtime if path.is_file() else 0.0
        except OSError:
            return 0.0

    def _pull_settings(self) -> None:
        self.settings = self.app.store.load()
        self._mappings_mtime = self._mappings_mtime_now()

    def _sync_mappings(self) -> None:
        mtime = self._mappings_mtime_now()
        if mtime == self._mappings_mtime:
            return
        self._pull_settings()
        if hasattr(self, "rules_tree"):
            self.startup_var.set(self.settings.start_with_windows)
            self.reload_rules()

    def _save(self) -> bool:
        try:
            self.app.store.save(self.settings)
        except OSError:
            log.exception("Could not save mappings")
            self._message("Could not save mappings.", error=True)
            return False
        self._mappings_mtime = self._mappings_mtime_now()
        return True

    def _message(self, text: str, *, error: bool = False) -> None:
        self.message.set(text)
        if hasattr(self, "message_label"):
            self.message_label.configure(style="Error.TLabel" if error else "Muted.TLabel")

    def _alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _post(self, callback) -> None:
        self.app.call_on_ui(callback)


class App:
    def __init__(
        self,
        store: ConfigStore | None = None,
        sonar: SonarClient | None = None,
        *,
        manage_startup: bool = False,
        show_window: bool = True,
    ) -> None:
        _enable_dpi()
        self.store = store or ConfigStore()
        self.sonar = sonar or SonarClient()
        self.manage_startup = manage_startup
        self.show_window = show_window
        self.watcher = ProcessWatcher(self.sonar, self.store)
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("AutoSonar")
        self._window_icon = apply_window_icon(self.root)
        self.icon: pystray.Icon | None = None
        self._settings: SettingsWindow | None = None
        self._quitting = False
        self._icon_lock = threading.Lock()
        self._ui_queue: queue.Queue[object] = queue.Queue()
        self.watcher.set_on_change(self._on_status)
        self.root.after(0, self._pump_ui)

    def run(self) -> None:
        self._sync_startup()
        self.watcher.start()
        from autosonar.oled import start_screen

        start_screen()
        self._start_tray()
        if self.show_window:
            self.root.after(200, self.show_home)
        self.root.after(500, self._refresh_icon)
        try:
            self.root.mainloop()
        finally:
            self.watcher.stop()
            self._stop_icon()
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    def show_home(self) -> None:
        window = self._open_window()
        window.show_home()
        window.win.focus_force()

    def show_settings(self) -> None:
        window = self._open_window()
        window.show_settings_page()
        window.win.focus_force()

    def _open_window(self) -> SettingsWindow:
        if self._settings is None or not self._settings._alive():
            self._settings = SettingsWindow(self, present=False)
        return self._settings

    def call_on_ui(self, callback) -> None:
        self._ui_queue.put(callback)

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.watcher.stop()
        self._stop_icon()
        self.root.quit()

    def _pump_ui(self) -> None:
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except Exception:
                    log.exception("UI callback failed")
        except queue.Empty:
            pass
        if self._quitting:
            return
        try:
            self.root.after(100, self._pump_ui)
        except tk.TclError:
            return

    def _sync_startup(self) -> None:
        if not self.manage_startup:
            return
        settings = self.store.load()
        try:
            set_run_at_startup(settings.start_with_windows)
        except OSError:
            log.exception("Could not update the Windows startup entry")

    def _start_tray(self) -> None:
        try:
            self.icon = pystray.Icon("autosonar", make_icon(), "AutoSonar", self._menu())
            self.icon.run_detached()
        except Exception:
            log.exception("Could not create the tray icon")
            self.icon = None
            self.root.after(0, self.show_home)

    def _menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(self._profile_label, None, enabled=False),
            pystray.MenuItem("Open", self._open_home, default=True),
            pystray.MenuItem("Pause", self._toggle_pause, checked=lambda _item: self.watcher.paused),
            pystray.MenuItem("Settings", self._open_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._quit),
        )

    def _profile_label(self, _item: object) -> str:
        name = self.watcher.profile_name()
        if self.watcher.paused:
            return f"Paused — {name}" if name else "Paused"
        return name or "No default preset"

    def _toggle_pause(self, _icon: pystray.Icon, _item: object) -> None:
        self.watcher.paused = not self.watcher.paused

    def _open_home(self, _icon: pystray.Icon, _item: object) -> None:
        self.call_on_ui(self.show_home)

    def _open_settings(self, _icon: pystray.Icon, _item: object) -> None:
        self.call_on_ui(self.show_settings)

    def _quit(self, _icon: pystray.Icon, _item: object) -> None:
        self.call_on_ui(self.quit)

    def _on_status(self) -> None:
        self.call_on_ui(self._refresh_icon)

    def _refresh_icon(self) -> None:
        if self._quitting:
            return
        icon = self.icon
        if icon is None:
            return
        with self._icon_lock:
            try:
                icon.title = f"AutoSonar — {self.watcher.status_line()}"
                icon.update_menu()
            except Exception:
                log.debug("Tray menu was not ready to update", exc_info=True)

    def _stop_icon(self) -> None:
        icon = self.icon
        if icon is None:
            return
        try:
            icon.stop()
        except Exception:
            log.debug("Tray icon was already stopped", exc_info=True)
