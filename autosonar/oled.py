"""Flash a preset name on the Arctis Nova Pro dock OLED."""

from __future__ import annotations

import logging
import random
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

_VENDOR_ID = 0x1038
_PRODUCT_ID = 0x12E0
_INTERFACE = 4
_OLED_USAGE_PAGE = 0xFFC0
_REPORT_ID = 0x06
_DRAW_COMMAND = 0x93
_RETURN_COMMAND = 0x95
_WIDTH = 128
_HEIGHT = 64
_CHUNK = 64
_HOLD_SECONDS = 5.0
_MEDIA_GRACE = 5.0
_FONT = Path(r"C:\Windows\Fonts\segoeuib.ttf")
_INFO_USAGE_PAGE = 0xFF00


class NovaDisplay:
    def __init__(self, device) -> None:
        self._device = device

    @classmethod
    def connect(cls) -> NovaDisplay | None:
        import hid

        matches = [
            info
            for info in hid.enumerate(_VENDOR_ID, _PRODUCT_ID)
            if info.get("interface_number") == _INTERFACE and info.get("usage_page") == _OLED_USAGE_PAGE
        ]
        if not matches:
            return None
        device = hid.device()
        device.open_path(matches[0]["path"])
        return cls(device)

    def draw_text(self, text: str) -> None:
        self.draw_image(_render(text))

    def draw_image(self, image: Image.Image) -> None:
        if image.size != (_WIDTH, _HEIGHT) or image.mode != "1":
            image = image.convert("L").resize((_WIDTH, _HEIGHT)).point(lambda value: 255 if value > 140 else 0).convert("1")
        pixels = image.load()
        for offset in range(0, _WIDTH, _CHUNK):
            self._device.send_feature_report(_pack(pixels, offset, 0, _CHUNK, _HEIGHT))

    def return_to_ui(self) -> None:
        report = bytes([_REPORT_ID, _RETURN_COMMAND]) + bytes(62)
        sent = self._device.send_feature_report(report)
        if sent < 2:
            raise OSError(f"Nova Pro did not accept the return command ({sent})")

    def close(self) -> None:
        try:
            self._device.close()
        except Exception:
            log.debug("Nova Pro OLED was already closed", exc_info=True)


def show_preset(name: str) -> None:
    """Show the preset name for a few seconds, then hand the screen back to GG."""
    screen.show_preset(name)


def start_screen() -> None:
    screen.start()


def note_activity() -> None:
    screen.note_activity()


def reload_screen_settings() -> None:
    screen.reload_settings()


class DockScreen:
    """Owns the dock OLED so the toast and the screensaver never draw together."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._toast_text = ""
        self._toast_until = 0.0
        self._last_activity = time.monotonic()
        self._wake = False
        self._holding = False
        self._settings = None
        self._effect_name = ""
        self._effect = None
        self._clip = None
        self._info = None
        self._last_report = b""
        self._media_playing = False
        self._media_until = 0.0
        self._media_checked = 0.0
        self._preview_until = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.reload_settings()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="autosonar-oled", daemon=True)
        self._thread.start()

    def preview(self) -> None:
        """Play the chosen screensaver on the dock for a few seconds."""
        self.start()
        with self._lock:
            self._preview_until = time.monotonic() + 8
            self._toast_until = 0.0
            self._effect = None
            self._clip = None
            self._effect_name = ""

    def show_preset(self, name: str) -> None:
        with self._lock:
            self._toast_text = name.strip() or "Preset"
            self._toast_until = time.monotonic() + _HOLD_SECONDS
            self._last_activity = time.monotonic()
            self._wake = True

    def note_activity(self) -> None:
        with self._lock:
            self._last_activity = time.monotonic()
            self._wake = True

    def reload_settings(self) -> None:
        from autosonar.screensaver import load_settings

        with self._lock:
            self._settings = load_settings()
            self._effect = None
            self._effect_name = ""
            self._clip = None

    def _loop(self) -> None:
        display = None
        try:
            while not self._stop.is_set():
                self._poll_dock()
                self._poll_media()
                frame, returning = self._next_frame()
                if frame is not None:
                    if display is None:
                        display = NovaDisplay.connect()
                    if display is None:
                        log.warning("Nova Pro OLED is not connected")
                        time.sleep(2)
                        continue
                    try:
                        display.draw_image(frame)
                        self._holding = True
                    except Exception:
                        log.exception("Could not update the Nova Pro screen")
                        display.close()
                        display = None
                    time.sleep(0.08)
                    continue
                if returning and display is not None:
                    try:
                        display.return_to_ui()
                    except Exception:
                        log.exception("Could not hand the Nova Pro screen back to SteelSeries")
                    display.close()
                    display = None
                    self._holding = False
                time.sleep(0.25)
        finally:
            if display is not None:
                try:
                    display.return_to_ui()
                except Exception:
                    log.debug("Could not restore the dock screen", exc_info=True)
                display.close()

    def _next_frame(self):
        now = time.monotonic()
        with self._lock:
            toast = now < self._toast_until
            text = self._toast_text
            idle_minutes = 5 if self._settings is None else self._settings.idle_minutes
            idle_for = now - self._last_activity
            playing = self._media_playing
            holding = self._holding
            preview = now < self._preview_until
        if preview:
            return self._saver_frame(), False
        if toast and text:
            return _render(text), False
        if idle_minutes > 0 and not playing and idle_for >= idle_minutes * 60:
            return self._saver_frame(), False
        return None, holding

    def _saver_frame(self):
        from autosonar import screensaver as saver

        with self._lock:
            settings = self._settings or saver.load_settings()
            choice = settings.choice
        try:
            if choice not in saver.BUILTINS:
                if self._clip is None or self._effect_name != choice:
                    self._clip = saver.load_clip(choice)
                    self._effect_name = choice
                    self._effect = None
                return self._clip.frame()
            name = choice
            if choice == "shuffle":
                if self._effect is None:
                    name = random.choice([item for item in saver.BUILTINS if item != "shuffle"])
                    self._effect = saver.make_effect(name)
                    self._effect_name = name
                return self._effect.frame()
            if self._effect is None or self._effect_name != name:
                self._effect = saver.make_effect(name)
                self._effect_name = name
                self._clip = None
            return self._effect.frame()
        except Exception:
            log.exception("Could not draw the screensaver")
            self._effect = saver.make_effect("starfield")
            self._effect_name = "starfield"
            return self._effect.frame()

    def _poll_dock(self) -> None:
        device = self._ensure_info()
        if device is None:
            return
        try:
            device.set_nonblocking(True)
            report = bytes(device.read(64) or b"")
        except Exception:
            log.debug("Dock control read failed", exc_info=True)
            self._close_info()
            return
        if not report or len(report) < 2 or report[1] == 0xB7 or report == self._last_report:
            return
        self._last_report = report
        self.note_activity()

    def _ensure_info(self):
        if self._info is not None:
            return self._info
        try:
            import hid

            matches = [
                info
                for info in hid.enumerate(_VENDOR_ID, _PRODUCT_ID)
                if info.get("interface_number") == _INTERFACE and info.get("usage_page") == _INFO_USAGE_PAGE
            ]
            if not matches:
                return None
            device = hid.device()
            device.open_path(matches[0]["path"])
            device.set_nonblocking(True)
            self._info = device
            return device
        except Exception:
            log.debug("Could not open the Nova Pro controls", exc_info=True)
            return None

    def _close_info(self) -> None:
        if self._info is None:
            return
        try:
            self._info.close()
        except Exception:
            pass
        self._info = None

    def _poll_media(self) -> None:
        now = time.monotonic()
        if now - self._media_checked < 1:
            return
        self._media_checked = now
        from autosonar.media import media_is_playing

        playing = media_is_playing()
        if playing is None:
            return
        if playing:
            self._media_until = now + _MEDIA_GRACE
        active = now < self._media_until
        with self._lock:
            was = self._media_playing
            self._media_playing = active
        if active == was:
            return
        self.note_activity()
        if active:
            log.info("Media is playing, leaving the Nova Pro screen to SteelSeries")


screen = DockScreen()


def _render(text: str) -> Image.Image:
    image = Image.new("1", (_WIDTH, _HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    size = 18 if len(text) <= 16 else 14 if len(text) <= 28 else 11
    font = ImageFont.truetype(str(_FONT), size) if _FONT.is_file() else ImageFont.load_default()
    lines = _wrap(text, font, _WIDTH - 8)
    heights = [draw.textbbox((0, 0), line, font=font)[3] for line in lines]
    total = sum(heights) + max(0, len(lines) - 1) * 2
    y = max(0, (_HEIGHT - total) // 2)
    for line, height in zip(lines, heights):
        box = draw.textbbox((0, 0), line, font=font)
        x = max(0, (_WIDTH - (box[2] - box[0])) // 2)
        draw.text((x, y), line, fill=1, font=font)
        y += height + 2
    return image


def _wrap(text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
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


def _pack(pixels, origin_x: int, origin_y: int, width: int, height: int) -> bytes:
    padded_h = ((height + 7) // 8) * 8
    report = bytearray(1024)
    report[0] = _REPORT_ID
    report[1] = _DRAW_COMMAND
    report[2] = origin_x
    report[3] = origin_y
    report[4] = width
    report[5] = padded_h
    for y in range(height):
        for x in range(width):
            if not pixels[origin_x + x, origin_y + y]:
                continue
            index = x * padded_h + y
            report[(index // 8) + 6] |= 1 << (index % 8)
    return bytes(report)
