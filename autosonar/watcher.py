"""Watch running processes and choose which Sonar preset should be active."""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

import psutil

from autosonar.config import ConfigStore, Rule, Settings, exe_key
from autosonar.sonar import SonarClient, SonarError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatcherSnapshot:
    """What the home circle should show right now."""

    state: str
    preset_name: str
    detail: str

    @property
    def circle_text(self) -> str:
        if self.state == "needs_setup" or not self.preset_name:
            return "No preset"
        return self.preset_name

    @property
    def sentence(self) -> str:
        if self.state == "game":
            return f"Detected {self.detail}" if self.detail else "Detected a game"
        if self.state == "looking":
            return "Looking for a game"
        if self.state == "paused":
            return "Paused"
        if self.state == "error":
            return self.detail or "SteelSeries Sonar is not running"
        return "Choose a default in Settings"

user32 = ctypes.WinDLL("user32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int


@dataclass(frozen=True)
class ObservedProcess:
    pid: int
    exe: str
    create_time: float


@dataclass(frozen=True)
class ProcessChoice:
    pid: int
    exe: str
    title: str
    create_time: float


def titles_by_pid() -> dict[int, list[str]]:
    try:
        return _collect_titles()
    except Exception:
        log.exception("Could not read window titles")
        return {}


def _collect_titles() -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        text = buffer.value.strip()
        if text:
            found.setdefault(int(process_id.value), []).append(text)
        return True

    enum_callback = _WNDENUMPROC(callback)
    user32.EnumWindows(enum_callback, 0)
    return found


def list_processes() -> list[ObservedProcess]:
    found: list[ObservedProcess] = []
    for proc in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            info = proc.info
            name = info.get("name") or ""
            pid = info.get("pid")
            if not name or not pid:
                continue
            create_time = float(info.get("create_time") or 0.0)
        except (psutil.Error, TypeError, ValueError):
            continue
        found.append(ObservedProcess(pid=int(pid), exe=name, create_time=create_time))
    return found


def running_choices() -> list[ProcessChoice]:
    titles = titles_by_pid()
    choices: list[ProcessChoice] = []
    for proc in list_processes():
        window_titles = titles.get(proc.pid) or []
        choices.append(
            ProcessChoice(
                pid=proc.pid,
                exe=proc.exe,
                title=" | ".join(window_titles),
                create_time=proc.create_time,
            )
        )
    choices.sort(key=lambda choice: (not choice.title, choice.exe.casefold(), choice.pid))
    return choices


def choose_match(processes: list[ObservedProcess], rules: list[Rule], titles: dict[int, list[str]]) -> Rule | None:
    """Pick the rule whose process started most recently.

    A newer process wins over an older one. When one process matches two
    rules, the rule with a window-title filter wins; otherwise the earlier
    rule in the list wins.
    """
    best: tuple[float, int, int, Rule] | None = None
    for index, rule in enumerate(rules):
        wanted = exe_key(rule.exe)
        if not wanted or not rule.profile_id:
            continue
        title_query = rule.title_contains.strip().casefold()
        specificity = 1 if title_query else 0
        for proc in processes:
            if exe_key(proc.exe) != wanted:
                continue
            if title_query:
                window_titles = titles.get(proc.pid) or []
                if not any(title_query in title.casefold() for title in window_titles):
                    continue
            candidate = (proc.create_time, specificity, -index, rule)
            if best is None or candidate[:3] > best[:3]:
                best = candidate
    return None if best is None else best[3]


class ProcessWatcher:
    def __init__(self, sonar: SonarClient, store: ConfigStore) -> None:
        self.sonar = sonar
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = threading.RLock()
        self._paused = False
        self._profile_name = ""
        self._detail = ""
        self._error = ""
        self._on_change = None
        self._last_reported = ""
        self._last_catalog_scan = 0.0
        self._installed_ids: set[str] | None = None
        self._dock_game = ""

    @property
    def paused(self) -> bool:
        with self._state:
            return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        with self._state:
            self._paused = value
        self._set_state()

    def status_line(self) -> str:
        with self._state:
            return self._status_line_unlocked()

    def profile_name(self) -> str:
        with self._state:
            return self._profile_name

    def snapshot(self) -> WatcherSnapshot:
        with self._state:
            return self._snapshot_unlocked()

    def set_on_change(self, callback) -> None:
        self._on_change = callback

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="autosonar-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)

    def tick(self) -> None:
        with self._state:
            paused = self._paused
        settings = self.store.load()
        processes = list_processes()
        try:
            settings = self._adopt_new_games(settings, processes)
        except SonarError as exc:
            if not paused:
                self._set_state(error=str(exc))
                return
        except Exception:
            log.exception("Could not add detected games")
        if paused:
            self._set_state()
            return

        needs_titles = any(rule.title_contains.strip() for rule in settings.rules)
        titles = titles_by_pid() if needs_titles else {}
        target = active_target(settings, processes, titles)
        if target is None:
            self._set_state(profile_name="", detail="", error="")
            return
        target_id, target_name, reason = target
        showing_game = reason != "default"
        if showing_game and self._dock_game != reason:
            self._dock_game = reason
            self._show_on_dock(target_name)
        elif not showing_game:
            self._dock_game = ""

        try:
            changed = self.sonar.ensure_selected(target_id)
        except SonarError as exc:
            self._set_state(error=str(exc))
            return
        except Exception:
            log.exception("Preset switch failed")
            self._set_state(error="Could not switch the Sonar preset.")
            return

        if changed:
            log.info("Switched game preset to %s (%s)", target_name, reason)
        self._set_state(profile_name=target_name, detail=reason, error="")

    def _show_on_dock(self, preset_name: str) -> None:
        try:
            from autosonar.oled import show_preset

            show_preset(preset_name)
        except Exception:
            log.exception("Could not show the preset on the Nova Pro dock")

    def _adopt_new_games(self, settings: Settings, processes: list[ObservedProcess]) -> Settings:
        from autosonar.games import adopt_detected, build_suggestions, installed_steam_app_ids, known_exe_keys

        now = time.monotonic()
        running_keys = {exe_key(process.exe) for process in processes}
        dismissed = {exe_key(item) for item in settings.dismissed_exes}
        mapped = {exe_key(rule.exe) for rule in settings.rules}
        pending = (running_keys & known_exe_keys()) - dismissed - mapped
        catalog_due = self._last_catalog_scan == 0 or (now - self._last_catalog_scan) >= 60
        if not pending and not catalog_due:
            return settings
        presets = self.sonar.list_game_presets()
        if catalog_due or self._installed_ids is None:
            self._installed_ids = installed_steam_app_ids()
            self._last_catalog_scan = now
        choices = [
            ProcessChoice(pid=process.pid, exe=process.exe, title="", create_time=process.create_time)
            for process in processes
        ]
        added = adopt_detected(settings, build_suggestions(presets, choices, self._installed_ids))
        if added:
            self.store.save(settings)
            log.info("Added games automatically: %s", ", ".join(added))
        return settings

    def poll_seconds(self) -> float:
        settings = self.store.load()
        return max(0.2, settings.poll_interval_ms / 1000)

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.tick()
            except Exception:
                log.exception("Watcher tick failed")
            remaining = self.poll_seconds() - (time.monotonic() - started)
            self._stop.wait(max(0.2, remaining))

    def _set_state(
        self,
        *,
        profile_name: str | None = None,
        detail: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._state:
            if profile_name is not None:
                self._profile_name = profile_name
            if detail is not None:
                self._detail = detail
            if error is not None and error != self._error:
                if error:
                    log.warning("%s", error)
                self._error = error
            elif error is not None:
                self._error = error
            line = self._status_line_unlocked()
        if line != self._last_reported:
            self._last_reported = line
            callback = self._on_change
            if callback is not None:
                try:
                    callback()
                except Exception:
                    log.exception("Status callback failed")

    def _status_line_unlocked(self) -> str:
        snap = self._snapshot_unlocked()
        if snap.state == "paused":
            base = f"Paused ({snap.preset_name})" if snap.preset_name else "Paused"
        elif snap.state == "needs_setup":
            base = "No default preset"
        elif snap.state == "game":
            base = f"{snap.preset_name} ({snap.detail})" if snap.detail else snap.preset_name
        elif snap.state == "looking":
            base = snap.preset_name or "Looking for a game"
        elif snap.state == "error":
            return snap.detail or "SteelSeries Sonar is not running"
        else:
            base = snap.preset_name or "Waiting"
        return base

    def _snapshot_unlocked(self) -> WatcherSnapshot:
        if self._paused:
            return WatcherSnapshot("paused", self._profile_name, "")
        if self._error:
            return WatcherSnapshot("error", self._profile_name, self._error)
        if not self._profile_name:
            return WatcherSnapshot("needs_setup", "", "")
        if self._detail and self._detail != "default":
            return WatcherSnapshot("game", self._profile_name, self._detail)
        return WatcherSnapshot("looking", self._profile_name, "")


def active_target(settings: Settings, processes: list[ObservedProcess], titles: dict[int, list[str]]) -> tuple[str, str, str] | None:
    """Return (profile id, profile name, reason) for tests and the watcher."""
    match = choose_match(processes, settings.rules, titles)
    if match is not None:
        reason = match.exe
        if match.title_contains.strip():
            reason = f"{match.exe} / {match.title_contains.strip()}"
        return match.profile_id, match.profile_name or match.exe, reason
    if settings.default_profile_id:
        return settings.default_profile_id, settings.default_profile_name or "Default", "default"
    return None
