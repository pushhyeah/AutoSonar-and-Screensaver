"""Load and save app-to-preset mappings."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import winreg
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AutoSonar"


def app_dir() -> Path:
    """Folder for mappings, the log, and the startup launcher.

    The Microsoft Store build of Python redirects AppData writes into a private
    package cache. Windows logon cannot see that cache, so this install keeps
    AutoSonar files in the user profile instead.
    """
    if _store_python():
        return Path.home() / "AutoSonar"
    roaming = os.environ.get("APPDATA")
    base = Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
    return base / "AutoSonar"


def _store_python() -> bool:
    markers = (
        getattr(sys, "base_prefix", ""),
        getattr(sys, "_base_executable", ""),
        sys.executable,
    )
    return any("WindowsApps" in str(path) for path in markers)


def migrate_legacy_config() -> None:
    """Copy mappings saved by the Store-Python build into the normal AppData folder."""
    if _store_python():
        return
    legacy = Path.home() / "AutoSonar" / "mappings.json"
    current = app_dir() / "mappings.json"
    try:
        if not legacy.is_file() or current.is_file():
            return
        if legacy.resolve() == current.resolve():
            return
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(legacy.read_bytes())
    except OSError:
        log.exception("Could not copy the older AutoSonar settings")


def default_mappings_path() -> Path:
    return app_dir() / "mappings.json"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def exe_key(name: str) -> str:
    """Compare executable names without case or a trailing .exe."""
    cleaned = Path(name.strip().strip('"')).name.casefold()
    if cleaned.endswith(".exe"):
        cleaned = cleaned[:-4]
    return cleaned


def display_exe(name: str) -> str:
    """Store executable names with an .exe suffix for the settings list."""
    cleaned = Path(name.strip().strip('"')).name
    if not cleaned:
        return ""
    if not cleaned.casefold().endswith(".exe"):
        cleaned += ".exe"
    return cleaned


@dataclass
class Rule:
    exe: str
    profile_id: str
    profile_name: str
    title_contains: str = ""
    name: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "exe": self.exe,
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "title_contains": self.title_contains,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: object) -> Rule | None:
        if not isinstance(data, dict):
            return None
        exe = str(data.get("exe") or "").strip()
        profile_id = str(data.get("profile_id") or "").strip()
        if not exe or not profile_id:
            return None
        return cls(
            exe=exe,
            profile_id=profile_id,
            profile_name=str(data.get("profile_name") or "").strip(),
            title_contains=str(data.get("title_contains") or "").strip(),
            name=str(data.get("name") or "").strip(),
        )


def display_name(rule: Rule) -> str:
    if rule.name.strip():
        return rule.name.strip()
    return Path(rule.exe).stem


@dataclass
class Settings:
    default_profile_id: str = ""
    default_profile_name: str = ""
    poll_interval_ms: int = 1000
    start_with_windows: bool = True
    rules: list[Rule] = field(default_factory=list)
    dismissed_exes: list[str] = field(default_factory=list)

    def upsert_rule(self, rule: Rule) -> None:
        self._forget_dismissal(rule.exe)
        key = (exe_key(rule.exe), rule.title_contains.casefold())
        kept: list[Rule] = []
        replaced = False
        for existing in self.rules:
            existing_key = (exe_key(existing.exe), existing.title_contains.casefold())
            if existing_key == key:
                if not replaced:
                    kept.append(rule)
                    replaced = True
            else:
                kept.append(existing)
        if not replaced:
            kept.append(rule)
        self.rules = kept

    def dismiss_exe(self, exe: str) -> None:
        """Remember a game the user removed so it is not added again automatically."""
        self._forget_dismissal(exe)
        shown = display_exe(exe)
        if shown:
            self.dismissed_exes.append(shown)

    def _forget_dismissal(self, exe: str) -> None:
        key = exe_key(exe)
        self.dismissed_exes = [item for item in self.dismissed_exes if exe_key(item) != key]

    def to_dict(self) -> dict[str, object]:
        return {
            "default_profile_id": self.default_profile_id,
            "default_profile_name": self.default_profile_name,
            "poll_interval_ms": self.poll_interval_ms,
            "start_with_windows": self.start_with_windows,
            "dismissed_exes": list(self.dismissed_exes),
            "rules": [rule.to_dict() for rule in self.rules],
        }

    @classmethod
    def from_dict(cls, data: object) -> Settings:
        if not isinstance(data, dict):
            return cls()
        rules: list[Rule] = []
        raw_rules = data.get("rules")
        if isinstance(raw_rules, list):
            for item in raw_rules:
                rule = Rule.from_dict(item)
                if rule is not None:
                    rules.append(rule)
        interval = data.get("poll_interval_ms", 1000)
        if not isinstance(interval, int):
            interval = 1000
        interval = min(10_000, max(200, interval))
        dismissed: list[str] = []
        raw_dismissed = data.get("dismissed_exes")
        if isinstance(raw_dismissed, list):
            for item in raw_dismissed:
                shown = display_exe(str(item))
                if shown and exe_key(shown) not in {exe_key(existing) for existing in dismissed}:
                    dismissed.append(shown)
        return cls(
            default_profile_id=str(data.get("default_profile_id") or "").strip(),
            default_profile_name=str(data.get("default_profile_name") or "").strip(),
            poll_interval_ms=interval,
            start_with_windows=True if data.get("start_with_windows", True) is None else bool(data.get("start_with_windows", True)),
            rules=rules,
            dismissed_exes=dismissed,
        )


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_mappings_path()
        self._lock = threading.Lock()

    def load(self) -> Settings:
        with self._lock:
            if not self.path.is_file():
                return Settings()
            try:
                raw = self.path.read_text(encoding="utf-8-sig")
                return Settings.from_dict(json.loads(raw))
            except (OSError, json.JSONDecodeError):
                log.exception("Could not read %s", self.path)
                return Settings()

    def save(self, settings: Settings) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(settings.to_dict(), indent=2, ensure_ascii=False) + "\n"
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self.path)


def pythonw_executable() -> Path:
    current = Path(sys.executable)
    pythonw = current.with_name("pythonw.exe")
    if pythonw.is_file():
        return pythonw
    return current


def set_run_at_startup(enabled: bool) -> None:
    """Point the current-user Run key at AutoSonar."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if not enabled:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
                return
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, startup_command())
    except OSError:
        log.exception("Could not update the Windows startup entry")
        raise


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}" --startup'
    app_dir().mkdir(parents=True, exist_ok=True)
    launcher = app_dir() / "launch.pyw"
    launcher.write_text(
        "\n".join(
            [
                "import runpy",
                "import sys",
                f"sys.path.insert(0, {str(project_root())!r})",
                "runpy.run_module('autosonar', run_name='__main__')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return f'"{pythonw_executable()}" "{launcher}"'
