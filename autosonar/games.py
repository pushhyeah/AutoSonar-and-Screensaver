"""Pair common games with the Sonar presets installed on this PC."""

from __future__ import annotations

import logging
import os
import re
import winreg
from dataclasses import dataclass
from pathlib import Path

from autosonar.config import Rule, Settings, display_exe, exe_key
from autosonar.sonar import GamePreset
from autosonar.watcher import ProcessChoice

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnownGame:
    name: str
    exes: tuple[str, ...]
    aliases: tuple[str, ...]
    title_contains: str = ""
    steam_app_id: str = ""
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class GameSuggestion:
    name: str
    exe: str
    title_contains: str
    preset_id: str
    preset_name: str
    preset_favorite: bool
    running: bool
    installed: bool


# Executable names are the process Sonar should watch. Aliases are matched
# against preset names already stored in Sonar.
KNOWN_GAMES: tuple[KnownGame, ...] = (
    KnownGame("League of Legends", ("League of Legends.exe",), ("league of legends",), paths=(r"C:\Riot Games\League of Legends\Game\League of Legends.exe",)),
    KnownGame("VALORANT", ("VALORANT-Win64-Shipping.exe",), ("valorant",), "VALORANT", paths=(r"C:\Riot Games\VALORANT\live\ShooterGame\Binaries\Win64\VALORANT-Win64-Shipping.exe",)),
    KnownGame("Counter-Strike 2", ("cs2.exe",), ("counter-strike 2", "counter strike 2", "cs2"), steam_app_id="730"),
    KnownGame("Apex Legends", ("r5apex.exe", "r5apex_dx12.exe"), ("apex legends",), steam_app_id="1172470"),
    KnownGame("Fortnite", ("FortniteClient-Win64-Shipping.exe",), ("fortnite",)),
    KnownGame("Overwatch 2", ("Overwatch.exe",), ("overwatch 2", "overwatch")),
    KnownGame("Call of Duty", ("cod.exe",), ("call of duty", "warzone", "black ops")),
    KnownGame("Rainbow Six Siege", ("RainbowSix.exe",), ("rainbow six siege", "rainbow six"), steam_app_id="359550"),
    KnownGame("Rocket League", ("RocketLeague.exe",), ("rocket league",), steam_app_id="252950"),
    KnownGame("Dota 2", ("dota2.exe",), ("dota 2",), steam_app_id="570"),
    KnownGame("World of Warcraft", ("Wow.exe",), ("world of warcraft",)),
    KnownGame("Final Fantasy XIV", ("ffxiv_dx11.exe",), ("final fantasy xiv", "ffxiv")),
    KnownGame("Genshin Impact", ("GenshinImpact.exe",), ("genshin impact",)),
    KnownGame("Honkai: Star Rail", ("StarRail.exe",), ("honkai star rail", "star rail")),
    KnownGame("Elden Ring", ("eldenring.exe",), ("elden ring",), steam_app_id="1245620"),
    KnownGame("Cyberpunk 2077", ("Cyberpunk2077.exe",), ("cyberpunk 2077", "cyberpunk"), steam_app_id="1091500"),
    KnownGame("Baldur's Gate 3", ("bg3_dx11.exe", "bg3.exe"), ("baldurs gate 3",), steam_app_id="1086940"),
    KnownGame("Helldivers 2", ("helldivers2.exe",), ("helldivers 2",), steam_app_id="553850"),
    KnownGame("Marvel Rivals", ("Marvel-Win64-Shipping.exe",), ("marvel rivals",)),
    KnownGame("ARC Raiders", ("PioneerGame.exe",), ("arc raiders",), steam_app_id="1808500"),
    KnownGame("Deadlock", ("deadlock.exe",), ("deadlock",), steam_app_id="1422450"),
    KnownGame("Path of Exile", ("PathOfExileSteam.exe",), ("path of exile",), steam_app_id="238960"),
    KnownGame("Path of Exile 2", ("PathOfExileSteam.exe",), ("path of exile 2",), "Path of Exile 2", "2694490"),
    KnownGame("Diablo IV", ("Diablo IV.exe",), ("diablo iv", "diablo 4")),
    KnownGame("PUBG", ("TslGame.exe",), ("pubg", "playerunknowns battlegrounds"), steam_app_id="578080"),
    KnownGame("Rust", ("RustClient.exe",), ("rust",), steam_app_id="252490"),
    KnownGame("Escape from Tarkov", ("EscapeFromTarkov.exe",), ("escape from tarkov", "tarkov")),
    KnownGame("The Finals", ("Discovery.exe",), ("the finals",), steam_app_id="2073850"),
    KnownGame("Palworld", ("Palworld-Win64-Shipping.exe",), ("palworld",), steam_app_id="1623730"),
    KnownGame("Roblox", ("RobloxPlayerBeta.exe",), ("roblox",)),
    KnownGame("Minecraft", ("Minecraft.Windows.exe",), ("minecraft",)),
    KnownGame("Black Myth: Wukong", ("b1-Win64-Shipping.exe",), ("black myth", "wukong"), steam_app_id="2358720"),
    KnownGame("Monster Hunter Wilds", ("MonsterHunterWilds.exe",), ("monster hunter wilds",), steam_app_id="2246340"),
    KnownGame("Destiny 2", ("destiny2.exe",), ("destiny 2",), steam_app_id="1085660"),
    KnownGame("Halo Infinite", ("HaloInfinite.exe",), ("halo infinite",), steam_app_id="1240440"),
    KnownGame("Grand Theft Auto V", ("GTA5.exe",), ("grand theft auto v", "gta v", "gta 5"), steam_app_id="271590"),
    KnownGame("Red Dead Redemption 2", ("RDR2.exe",), ("red dead redemption 2",), steam_app_id="1174180"),
    KnownGame("Sea of Thieves", ("SoTGame.exe",), ("sea of thieves",), steam_app_id="1172620"),
    KnownGame("Phasmophobia", ("Phasmophobia.exe",), ("phasmophobia",), steam_app_id="739630"),
    KnownGame("Lethal Company", ("Lethal Company.exe",), ("lethal company",), steam_app_id="1966720"),
    KnownGame("VRChat", ("VRChat.exe",), ("vrchat",), steam_app_id="438100"),
    KnownGame("Stardew Valley", ("Stardew Valley.exe",), ("stardew valley",), steam_app_id="413150"),
    KnownGame("Hogwarts Legacy", ("HogwartsLegacy.exe",), ("hogwarts legacy",), steam_app_id="990080"),
    KnownGame("Warframe", ("Warframe.x64.exe",), ("warframe",), steam_app_id="230410"),
    KnownGame("Dead by Daylight", ("DeadByDaylight-Win64-Shipping.exe",), ("dead by daylight",), steam_app_id="381210"),
    KnownGame("Deep Rock Galactic", ("FSD-Win64-Shipping.exe",), ("deep rock galactic",), steam_app_id="548430"),
    KnownGame("Hunt: Showdown", ("HuntGame.exe",), ("hunt showdown",), steam_app_id="594650"),
    KnownGame("Valheim", ("valheim.exe",), ("valheim",), steam_app_id="892970"),
    KnownGame("Terraria", ("Terraria.exe",), ("terraria",), steam_app_id="105600"),
    KnownGame("Hearthstone", ("Hearthstone.exe",), ("hearthstone",)),
    KnownGame("Lost Ark", ("LOSTARK.exe",), ("lost ark",)),
    KnownGame("Battlefield 2042", ("BF2042.exe",), ("battlefield 2042",), steam_app_id="1517290"),
    KnownGame("Ready or Not", ("ReadyOrNotSteam-Win64-Shipping.exe",), ("ready or not",), steam_app_id="1144200"),
    KnownGame("Bodycam", ("Bodycam-Win64-Shipping.exe",), ("bodycam",)),
    KnownGame("Once Human", ("ONCE_HUMAN.exe",), ("once human",)),
    KnownGame("Delta Force", ("DeltaForce-Win64-Shipping.exe", "DeltaForceClient-Win64-Shipping.exe"), ("delta force",)),
    KnownGame("Supervive", ("Supervive-Win64-Shipping.exe",), ("supervive",)),
    KnownGame("FragPunk", ("FragPunk-Win64-Shipping.exe",), ("fragpunk", "frag punk")),
    KnownGame("osu!", ("osu!.exe",), ("osu",)),
)


def normalize_name(value: str) -> str:
    text = value.casefold().replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _contains_phrase(name: str, alias: str) -> bool:
    return f" {alias} " in f" {name} "


def preset_match_score(preset_name: str, aliases: tuple[str, ...]) -> int:
    """Higher means the Sonar preset name is a better fit for these aliases."""
    name = normalize_name(preset_name)
    best = 0
    for alias in aliases:
        cleaned = normalize_name(alias)
        if len(cleaned) < 3:
            continue
        if name == cleaned:
            best = max(best, 100 + len(cleaned))
        elif _contains_phrase(name, cleaned):
            best = max(best, 70 + len(cleaned))
    return best


def best_preset(presets: list[GamePreset], aliases: tuple[str, ...]) -> GamePreset | None:
    ranked: list[tuple[int, int, GamePreset]] = []
    for preset in presets:
        score = preset_match_score(preset.name, aliases)
        if score < 70:
            continue
        favorite_rank = 1 if preset.is_favorite else 0
        ranked.append((favorite_rank, score, preset))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked[0][2]


def installed_steam_app_ids() -> set[str]:
    try:
        return _steam_app_ids()
    except Exception:
        log.exception("Could not read the Steam library")
        return set()


def _steam_app_ids() -> set[str]:
    libraries: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path
        if resolved in seen:
            return
        seen.add(resolved)
        libraries.append(resolved)

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path, _kind = winreg.QueryValueEx(key, "SteamPath")
            add(Path(str(steam_path)))
    except OSError:
        pass
    program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    add(Path(program_files) / "Steam")

    vdf_paths = [library / "steamapps" / "libraryfolders.vdf" for library in list(libraries)]
    for vdf_path in vdf_paths:
        if not vdf_path.is_file():
            continue
        text = vdf_path.read_text(encoding="utf-8", errors="ignore")
        for raw in re.findall(r'"path"\s+"([^"]+)"', text):
            add(Path(raw.replace("\\\\", "\\")))

    app_ids: set[str] = set()
    for library in libraries:
        steamapps = library / "steamapps"
        if not steamapps.is_dir():
            continue
        for manifest in steamapps.glob("appmanifest_*.acf"):
            app_id = manifest.name.removeprefix("appmanifest_").removesuffix(".acf")
            if app_id.isdigit():
                app_ids.add(app_id)
    return app_ids


def _path_installed(game: KnownGame) -> bool:
    return any(Path(path).is_file() for path in game.paths)


def _choose_exe(game: KnownGame, running_keys: set[str]) -> str:
    for exe in game.exes:
        if exe_key(exe) in running_keys:
            return display_exe(exe)
    return display_exe(game.exes[0])


def known_exe_keys() -> set[str]:
    return {exe_key(exe) for game in KNOWN_GAMES for exe in game.exes}


_SKIP_EXE_MARKERS = (
    "crash",
    "unins",
    "redist",
    "vcredist",
    "setup",
    "install",
    "easyanticheat",
    "eac_",
    "dotnet",
    "unitycrash",
    "bugreport",
)


def find_game_exes(folder: Path) -> list[str]:
    """Return game executable names in a folder and its immediate subfolders."""
    if not folder.is_dir():
        return []
    roots = [folder]
    try:
        roots.extend(child for child in folder.iterdir() if child.is_dir())
    except OSError:
        return []
    found: list[Path] = []
    for root in roots:
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        found.extend(path for path in children if path.is_file() and path.suffix.casefold() == ".exe")
    usable = [path for path in found if not any(marker in path.stem.casefold() for marker in _SKIP_EXE_MARKERS)]
    if not usable:
        usable = found
    folder_name = normalize_name(folder.name)
    ranked: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for path in usable:
        key = exe_key(path.name)
        if not key or key in seen:
            continue
        seen.add(key)
        stem = normalize_name(path.stem)
        rank = 0
        if "launcher" in path.stem.casefold() and len(usable) > 1:
            rank += 10
        if stem == folder_name or (folder_name and folder_name in stem):
            rank -= 5
        ranked.append((rank, path.name.casefold(), display_exe(path.name)))
    ranked.sort()
    return [name for _rank, _sort_name, name in ranked]


def adopt_detected(settings: Settings, suggestions: list[GameSuggestion]) -> list[str]:
    """Add rules for installed or running games. Returns the names that were added.

    Games the user has removed stay removed.
    """
    dismissed = {exe_key(item) for item in settings.dismissed_exes}
    existing = {exe_key(rule.exe) for rule in settings.rules}
    added: list[str] = []
    for item in suggestions:
        if not item.running and not item.installed:
            continue
        key = exe_key(item.exe)
        if not key or key in dismissed or key in existing:
            continue
        settings.upsert_rule(
            Rule(
                exe=display_exe(item.exe),
                profile_id=item.preset_id,
                profile_name=item.preset_name,
                title_contains=item.title_contains,
            )
        )
        existing.add(key)
        added.append(item.name)
    return added


def build_suggestions(
    presets: list[GamePreset],
    processes: list[ProcessChoice],
    installed_app_ids: set[str] | None = None,
) -> list[GameSuggestion]:
    installed_app_ids = installed_app_ids if installed_app_ids is not None else set()
    running_keys = {exe_key(process.exe) for process in processes}
    suggestions: list[GameSuggestion] = []
    used_exes: set[str] = set()

    for game in KNOWN_GAMES:
        preset = best_preset(presets, game.aliases)
        if preset is None:
            continue
        exe = _choose_exe(game, running_keys)
        used_exes.add(exe_key(exe))
        running = any(exe_key(candidate) in running_keys for candidate in game.exes)
        installed = running or game.steam_app_id in installed_app_ids or _path_installed(game)
        suggestions.append(
            GameSuggestion(
                name=game.name,
                exe=exe,
                title_contains=game.title_contains,
                preset_id=preset.id,
                preset_name=preset.name,
                preset_favorite=preset.is_favorite,
                running=running,
                installed=installed,
            )
        )

    for process in processes:
        key = exe_key(process.exe)
        if key in used_exes or len(key) < 4:
            continue
        preset = best_preset(presets, (process.exe, key))
        if preset is None or preset_match_score(preset.name, (process.exe, key)) < 100:
            continue
        used_exes.add(key)
        suggestions.append(
            GameSuggestion(
                name=preset.name,
                exe=display_exe(process.exe),
                title_contains="",
                preset_id=preset.id,
                preset_name=preset.name,
                preset_favorite=preset.is_favorite,
                running=True,
                installed=True,
            )
        )

    suggestions.sort(
        key=lambda item: (
            0 if item.running else 1 if item.installed else 2,
            0 if item.preset_favorite else 1,
            item.name.casefold(),
        )
    )
    return suggestions


def filter_suggestions(
    suggestions: list[GameSuggestion],
    query: str,
    *,
    show_all: bool,
    added_exes: set[str],
) -> list[GameSuggestion]:
    cleaned = normalize_name(query)
    matched = [
        item
        for item in suggestions
        if not cleaned
        or cleaned in normalize_name(item.name)
        or cleaned in normalize_name(item.exe)
        or cleaned in normalize_name(item.preset_name)
    ]
    relevant = [
        item
        for item in matched
        if item.running or item.installed or item.preset_favorite or exe_key(item.exe) in added_exes
    ]
    if cleaned or show_all or not relevant:
        return matched
    return relevant


def suggestion_status(item: GameSuggestion, *, added: bool) -> str:
    parts: list[str] = []
    if added:
        parts.append("Added")
    if item.running:
        parts.append("Running")
    elif item.installed:
        parts.append("Installed")
    elif item.preset_favorite and not added:
        parts.append("Favorite")
    elif not added:
        parts.append("Common")
    return " · ".join(parts)
