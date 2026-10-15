"""Built-in dock animations and user clips for the Nova Pro OLED."""

from __future__ import annotations

import json
import logging
import math
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageSequence

from autosonar.config import app_dir

log = logging.getLogger(__name__)

WIDTH = 128
HEIGHT = 64
BUILTINS = ("starfield", "matrix", "bounce", "clock", "plasma", "shuffle")
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
MEDIA_SUFFIXES = {".gif", *VIDEO_SUFFIXES}
_FONT = Path(r"C:\Windows\Fonts\segoeui.ttf")


@dataclass
class ScreenSettings:
    idle_minutes: int = 5
    choice: str = "starfield"

    def to_dict(self) -> dict[str, object]:
        return {"idle_minutes": self.idle_minutes, "choice": self.choice}


def settings_path() -> Path:
    return app_dir() / "screensaver.json"


def media_dir() -> Path:
    folder = app_dir() / "screensaver"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def load_settings() -> ScreenSettings:
    path = settings_path()
    if not path.is_file():
        return ScreenSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read screensaver settings")
        return ScreenSettings()
    minutes = data.get("idle_minutes", 5)
    if not isinstance(minutes, int):
        minutes = 5
    choice = str(data.get("choice") or "starfield")
    return ScreenSettings(idle_minutes=max(0, min(180, minutes)), choice=choice)


def save_settings(settings: ScreenSettings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2) + "\n", encoding="utf-8")


def list_media() -> list[str]:
    folder = media_dir()
    return sorted(path.name for path in folder.iterdir() if path.suffix.casefold() in MEDIA_SUFFIXES)


def add_media(source: Path) -> str:
    if source.suffix.casefold() not in MEDIA_SUFFIXES:
        raise ValueError("Choose a GIF or a video.")
    if source.stat().st_size > 80 * 1024 * 1024:
        raise ValueError("That file is larger than 80 MB.")
    target = media_dir() / source.name
    shutil.copy2(source, target)
    return target.name


def remove_media(name: str) -> None:
    path = media_dir() / Path(name).name
    if path.is_file():
        path.unlink()


def blank() -> Image.Image:
    return Image.new("1", (WIDTH, HEIGHT), 0)


class Effect:
    def frame(self) -> Image.Image:
        raise NotImplementedError


class Starfield(Effect):
    def __init__(self) -> None:
        self._stars = [[random.randrange(WIDTH), random.randrange(HEIGHT), random.uniform(0.4, 2.2)] for _ in range(40)]

    def frame(self) -> Image.Image:
        image = blank()
        pixels = image.load()
        for star in self._stars:
            star[0] += star[2]
            if star[0] >= WIDTH:
                star[0] = 0
                star[1] = random.randrange(HEIGHT)
            x = int(star[0])
            y = int(star[1])
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                pixels[x, y] = 1
        return image


# 3x5 glyphs: digits plus a few katakana-like shapes. Packed as five rows of three bits.
_MATRIX_GLYPHS = (
    (0b111, 0b101, 0b101, 0b101, 0b111),
    (0b010, 0b110, 0b010, 0b010, 0b111),
    (0b111, 0b001, 0b111, 0b100, 0b111),
    (0b111, 0b001, 0b111, 0b001, 0b111),
    (0b101, 0b101, 0b111, 0b001, 0b001),
    (0b111, 0b100, 0b111, 0b001, 0b111),
    (0b111, 0b100, 0b111, 0b101, 0b111),
    (0b111, 0b001, 0b010, 0b010, 0b010),
    (0b111, 0b101, 0b111, 0b101, 0b111),
    (0b111, 0b101, 0b111, 0b001, 0b111),
    (0b010, 0b101, 0b111, 0b101, 0b101),
    (0b100, 0b101, 0b111, 0b101, 0b001),
    (0b010, 0b010, 0b111, 0b010, 0b010),
    (0b111, 0b010, 0b010, 0b010, 0b111),
    (0b101, 0b101, 0b010, 0b101, 0b101),
    (0b001, 0b011, 0b111, 0b110, 0b100),
    (0b110, 0b101, 0b111, 0b101, 0b011),
    (0b011, 0b100, 0b111, 0b001, 0b110),
    (0b101, 0b111, 0b010, 0b111, 0b101),
    (0b001, 0b001, 0b111, 0b101, 0b111),
)
_CELL_W = 4
_CELL_H = 6
_MATRIX_COLUMNS = WIDTH // _CELL_W


def _plot_glyph(pixels, x: int, y: int, glyph: tuple[int, ...], level: int) -> None:
    for row, bits in enumerate(glyph):
        py = y + row
        if py < 0 or py >= HEIGHT:
            continue
        for col in range(3):
            if ((bits >> (2 - col)) & 1) == 0:
                continue
            if level == 1 and col == 2:
                continue
            if level >= 2 and col != 1:
                continue
            px = x + col
            if 0 <= px < WIDTH:
                pixels[px, py] = 1


class MatrixRain(Effect):
    def __init__(self) -> None:
        self._tick = 0
        self._columns: list[dict[str, object]] = [self._spawn(on_screen=True) for _ in range(_MATRIX_COLUMNS)]

    def _spawn(self, *, on_screen: bool) -> dict[str, object]:
        return {
            "y": random.randrange(-HEIGHT, HEIGHT) if on_screen else random.randrange(-48, -_CELL_H),
            "every": random.choice((1, 1, 2, 2, 3)),
            "phase": random.randrange(3),
            "length": random.randint(6, 14),
            "chars": [random.randrange(len(_MATRIX_GLYPHS)) for _ in range(16)],
        }

    def frame(self) -> Image.Image:
        image = blank()
        pixels = image.load()
        self._tick += 1
        for index, column in enumerate(self._columns):
            every = int(column["every"])
            if self._tick % every == int(column["phase"]) % every:
                column["y"] = int(column["y"]) + _CELL_H
                chars = column["chars"]
                assert isinstance(chars, list)
                chars.insert(0, random.randrange(len(_MATRIX_GLYPHS)))
                chars.pop()
            chars = column["chars"]
            assert isinstance(chars, list)
            if random.random() < 0.35 and len(chars) > 2:
                chars[random.randrange(1, min(7, len(chars)))] = random.randrange(len(_MATRIX_GLYPHS))
            head = int(column["y"])
            length = int(column["length"])
            x = index * _CELL_W
            for step in range(length):
                glyph = _MATRIX_GLYPHS[int(chars[step % len(chars)])]
                if step == 0:
                    level = 0
                elif step < 3:
                    level = 0
                elif step < 7:
                    level = 1
                else:
                    level = 2
                _plot_glyph(pixels, x, head - step * _CELL_H, glyph, level)
            if head - length * _CELL_H > HEIGHT:
                self._columns[index] = self._spawn(on_screen=False)
        return image


class Bounce(Effect):
    def __init__(self) -> None:
        self._x = 20.0
        self._y = 16.0
        self._dx = 2.2
        self._dy = 1.4

    def frame(self) -> Image.Image:
        image = blank()
        draw = ImageDraw.Draw(image)
        radius = 6
        self._x += self._dx
        self._y += self._dy
        if self._x < radius or self._x > WIDTH - radius:
            self._dx *= -1
        if self._y < radius or self._y > HEIGHT - radius:
            self._dy *= -1
        draw.ellipse((self._x - radius, self._y - radius, self._x + radius, self._y + radius), fill=1)
        return image


class Clock(Effect):
    def frame(self) -> Image.Image:
        image = blank()
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(_FONT), 22) if _FONT.is_file() else ImageFont.load_default()
        import datetime

        label = datetime.datetime.now().strftime("%H:%M")
        box = draw.textbbox((0, 0), label, font=font)
        x = (WIDTH - (box[2] - box[0])) // 2
        y = (HEIGHT - (box[3] - box[1])) // 2 - 2
        draw.text((x, y), label, fill=1, font=font)
        return image


class Plasma(Effect):
    def __init__(self) -> None:
        self._t = 0.0

    def frame(self) -> Image.Image:
        image = blank()
        pixels = image.load()
        self._t += 0.15
        for y in range(0, HEIGHT, 2):
            for x in range(0, WIDTH, 2):
                value = (
                    math.sin(x * 0.15 + self._t)
                    + math.sin(y * 0.22 + self._t * 1.3)
                    + math.sin((x + y) * 0.09 + self._t * 0.6)
                )
                if value > 0.6:
                    pixels[x, y] = 1
                    if x + 1 < WIDTH:
                        pixels[x + 1, y] = 1
        return image


def make_effect(name: str) -> Effect:
    if name == "matrix":
        return MatrixRain()
    if name == "bounce":
        return Bounce()
    if name == "clock":
        return Clock()
    if name == "plasma":
        return Plasma()
    return Starfield()


class Clip:
    def __init__(self, frames: list[Image.Image]) -> None:
        self.frames = frames or [blank()]
        self._index = 0

    def frame(self) -> Image.Image:
        image = self.frames[self._index % len(self.frames)]
        self._index += 1
        return image


def load_clip(name: str) -> Clip:
    path = media_dir() / Path(name).name
    suffix = path.suffix.casefold()
    if suffix == ".gif":
        return Clip(_gif_frames(path))
    return Clip(_video_frames(path))


def _to_oled(image: Image.Image) -> Image.Image:
    gray = image.convert("L").resize((WIDTH, HEIGHT), Image.Resampling.BOX)
    return gray.point(lambda value: 255 if value > 140 else 0).convert("1")


def _gif_frames(path: Path) -> list[Image.Image]:
    frames: list[Image.Image] = []
    with Image.open(path) as image:
        for index, frame in enumerate(ImageSequence.Iterator(image)):
            if index >= 120:
                break
            frames.append(_to_oled(frame))
    return frames


def _video_frames(path: Path) -> list[Image.Image]:
    import imageio.v3 as iio

    frames: list[Image.Image] = []
    for index, frame in enumerate(iio.imiter(path)):
        if index % 4:
            continue
        if len(frames) >= 80:
            break
        frames.append(_to_oled(Image.fromarray(frame)))
    return frames
