"""Talk to the SteelSeries Sonar localhost API."""

from __future__ import annotations

import http.client
import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

CORE_PROPS_PATH = Path(r"C:\ProgramData\SteelSeries\SteelSeries Engine 3\coreProps.json")
GAME_DEVICE = "game"
_PRESET_CACHE_SECONDS = 10
_RECHECK_SECONDS = 15


class SonarError(Exception):
    """Sonar is missing, stopped, or returned something we cannot use."""


@dataclass(frozen=True)
class GamePreset:
    id: str
    name: str
    is_favorite: bool
    favorite_position: int | None


class SonarClient:
    """Find Sonar's current port and select game presets.

    The port changes whenever Sonar starts. Discovery reads GG's coreProps
    file, then asks the GG process which address Sonar is using.
    """

    def __init__(self, core_props_path: Path = CORE_PROPS_PATH) -> None:
        self.core_props_path = core_props_path
        self.base_url: str | None = None
        self._lock = threading.RLock()
        self._presets: list[GamePreset] | None = None
        self._presets_at = 0.0
        self._selected_id: str | None = None
        self._selected_checked_at = 0.0

    def discover(self) -> str:
        with self._lock:
            return self._discover_unlocked()

    def list_game_presets(self, *, force: bool = False) -> list[GamePreset]:
        with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._presets is not None
                and (now - self._presets_at) < _PRESET_CACHE_SECONDS
            ):
                return list(self._presets)
            payload = self._get_json("/configs")
            presets = _game_presets(payload)
            self._presets = presets
            self._presets_at = time.monotonic()
            return list(presets)

    def selected_game_id(self) -> str | None:
        with self._lock:
            return self._selected_game_id_unlocked()

    def select_game_preset(self, preset_id: str) -> None:
        with self._lock:
            self._select_unlocked(preset_id)

    def cached_selected_id(self) -> str | None:
        with self._lock:
            return self._selected_id

    def ensure_selected(self, preset_id: str) -> bool:
        """Select a game preset unless Sonar already has it active.

        Returns True when a switch was sent. The last confirmed id is cached
        so a steady game does not call Sonar on every poll. A periodic recheck
        still notices a Sonar restart or a manual change.
        """
        with self._lock:
            now = time.monotonic()
            if (
                self._selected_id == preset_id
                and (now - self._selected_checked_at) < _RECHECK_SECONDS
            ):
                return False
            if (
                self._selected_id
                and self._selected_id != preset_id
            ):
                self._select_unlocked(preset_id)
                return True
            current = self._selected_game_id_unlocked()
            if current == preset_id:
                self._selected_id = preset_id
                self._selected_checked_at = time.monotonic()
                return False
            self._select_unlocked(preset_id)
            return True

    def _discover_unlocked(self) -> str:
        if not self.core_props_path.is_file():
            raise SonarError("SteelSeries GG is not installed (coreProps.json missing).")
        try:
            props = json.loads(self.core_props_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SonarError("Could not read SteelSeries GG coreProps.json.") from exc
        address = str(props.get("ggEncryptedAddress") or "").strip()
        if not address:
            raise SonarError("SteelSeries GG address is missing from coreProps.json.")
        url = f"https://{address}/subApps"
        context = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(url, context=context, timeout=5) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise SonarError("Could not reach SteelSeries GG.") from exc
        sonar = (payload.get("subApps") or {}).get("sonar") or {}
        metadata = sonar.get("metadata") or {}
        base = str(metadata.get("webServerAddress") or "").strip().rstrip("/")
        if not base:
            if not sonar.get("isRunning"):
                raise SonarError("SteelSeries Sonar is not running.")
            raise SonarError("Sonar did not publish a local address yet.")
        if "://" not in base:
            base = "http://" + base
        if self.base_url != base:
            log.info("Sonar API at %s", base)
        self.base_url = base
        return base

    def _selected_game_id_unlocked(self) -> str | None:
        payload = self._get_json("/configs/selected")
        if not isinstance(payload, list):
            raise SonarError("Unexpected response from Sonar.")
        for item in payload:
            if isinstance(item, dict) and item.get("virtualAudioDevice") == GAME_DEVICE:
                preset_id = str(item.get("id") or "")
                self._selected_id = preset_id or None
                self._selected_checked_at = time.monotonic()
                return self._selected_id
        self._selected_id = None
        self._selected_checked_at = time.monotonic()
        return None

    def _select_unlocked(self, preset_id: str) -> None:
        quoted = urllib.parse.quote(preset_id, safe="-")
        self._call("PUT", f"/configs/{quoted}/select")
        self._selected_id = preset_id
        self._selected_checked_at = time.monotonic()

    def _get_json(self, path: str) -> object:
        raw = self._call("GET", path)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SonarError("Sonar returned an unreadable response.") from exc

    def _call(self, method: str, path: str, *, retry: bool = True) -> bytes:
        if not self.base_url:
            self._discover_unlocked()
        try:
            return self._call_once(method, path)
        except SonarError:
            raise
        except Exception as exc:
            if not retry:
                raise SonarError("Could not reach SteelSeries Sonar.") from exc
            log.info("Sonar address %s failed (%s); looking it up again", self.base_url, exc)
            self.base_url = None
            self._presets = None
            self._discover_unlocked()
            try:
                return self._call_once(method, path)
            except SonarError:
                raise
            except Exception as exc2:
                raise SonarError("Could not reach SteelSeries Sonar.") from exc2

    def _call_once(self, method: str, path: str) -> bytes:
        if not self.base_url:
            raise SonarError("Sonar address is unknown.")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "http" or not parsed.hostname or not parsed.port:
            raise SonarError("Unexpected Sonar address.")
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=8)
        try:
            headers = {"Content-Length": "0"} if method == "PUT" else {}
            body = b"" if method == "PUT" else None
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            status = response.status
        finally:
            connection.close()
        if status < 200 or status >= 300:
            detail = payload[:180]
            raise ConnectionError(f"HTTP {status} for {method} {path}: {detail!r}")
        return payload


def _game_presets(payload: object) -> list[GamePreset]:
    if not isinstance(payload, list):
        raise SonarError("Unexpected response from Sonar.")
    presets: list[GamePreset] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("virtualAudioDevice") != GAME_DEVICE:
            continue
        name = str(item.get("name") or "").strip()
        preset_id = str(item.get("id") or "").strip()
        if not name or not preset_id:
            continue
        position = item.get("favoritePosition")
        presets.append(
            GamePreset(
                id=preset_id,
                name=name,
                is_favorite=bool(item.get("isFavorite")),
                favorite_position=position if isinstance(position, int) else None,
            )
        )
    presets.sort(key=_preset_sort_key)
    return presets


def _preset_sort_key(preset: GamePreset) -> tuple[int, int, str]:
    if preset.is_favorite:
        position = preset.favorite_position if preset.favorite_position is not None else 1_000_000
        return (0, position, preset.name.casefold())
    return (1, 1_000_000, preset.name.casefold())
