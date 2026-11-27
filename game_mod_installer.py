"""
Game Mod Installer  (v2.0)
Установщик модов из .zip / .7z / .rar архивов (или распакованных папок) сразу для многих игр.

Как это работает
  * Для каждой игры есть профиль: где искать игру, куда и как раскладывать моды.
  * Два типа профилей:
      "folder" — каждый мод = отдельная папка внутри папки назначения (RDR2 lml, Stardew Mods ...)
      "root"   — содержимое архива копируется в папку игры/подпапку по структуре архива
                 (Cyberpunk, GTA V, Skyrim Data ...)
  * Все установленные файлы записываются, поэтому мод можно удалить, а перезаписанные
    оригинальные файлы автоматически восстанавливаются из резервной копии.
  * Свои игры добавляются кнопкой «Своя игра» или правкой games.json (см. папку настроек).
  * Свои переводы: положи <код>.json в папку lang рядом с настройками (см. LANG_DIR).
"""
from __future__ import annotations

import codecs
import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from collections import deque
from datetime import datetime
from functools import lru_cache
from pathlib import Path
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:  # без Tk (CI/тесты) логика установки всё равно доступна
    tk = ttk = filedialog = messagebox = None

# Тяжёлые/необязательные модули подгружаются лениво — окно появляется мгновенно.
_PY7ZR = None
_PY7ZR_TRIED = False
TkinterDnD = None
DND_FILES = None


def get_py7zr():
    """py7zr импортируется только когда реально открывают .7z."""
    global _PY7ZR, _PY7ZR_TRIED
    if not _PY7ZR_TRIED:
        _PY7ZR_TRIED = True
        try:
            import py7zr
            _PY7ZR = py7zr
        except ImportError:
            _PY7ZR = None
    return _PY7ZR


def load_dnd() -> bool:
    global TkinterDnD, DND_FILES
    try:
        from tkinterdnd2 import TkinterDnD as _t, DND_FILES as _f
        TkinterDnD, DND_FILES = _t, _f
    except ImportError:
        TkinterDnD = DND_FILES = None
    return TkinterDnD is not None

APP_NAME = "Game Mod Installer"
APP_VERSION = "2.0"
FONT = "Segoe UI"
ARCHIVE_EXTS = {".zip", ".7z", ".rar"}
PACK_SUFFIX = ".gmpack.zip"  # сборки модов (экспорт/импорт)

APPDATA = Path(os.environ.get("APPDATA", Path.home()))
CONFIG_DIR = APPDATA / "GameModInstaller"
CONFIG_FILE = CONFIG_DIR / "config.json"
DB_FILE = CONFIG_DIR / "installs.json"
GAMES_FILE = CONFIG_DIR / "games.json"
BACKUP_DIR = CONFIG_DIR / "backups"
LANG_DIR = CONFIG_DIR / "lang"
GAMES_DIR = CONFIG_DIR / "games.d"  # *.json от сообщества: список профилей или один профиль
OLD_CONFIG_FILE = APPDATA / "RDR2ModInstaller" / "config.json"  # от v1/v2

DOC_EXTS = {".txt", ".md", ".pdf", ".url", ".rtf", ".docx", ".html", ".htm", ".jpg", ".jpeg", ".png", ".nfo"}
JUNK_NAMES = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}

THEMES = {
    "dark": {  # классический графит в стиле Discord
        "bg": "#1e1f22", "surface": "#2b2d31", "surface2": "#383a40", "hover": "#45484f",
        "fg": "#e3e5e8", "muted": "#9aa0a6", "accent": "#5865f2", "accent_hover": "#6d78f7",
        "on_accent": "#ffffff", "accent2": "#9ea7ff", "border": "#4a4d55",
        "ok": "#57d28f", "warn": "#f0b232", "err": "#f26d6d", "is_dark": True,
    },
    "light": {
        "bg": "#e9ebf0", "surface": "#ffffff", "surface2": "#eef0f4", "hover": "#d9dce2",
        "fg": "#1f2328", "muted": "#6b7280", "accent": "#4f5bd5", "accent_hover": "#3f4ab8",
        "on_accent": "#ffffff", "accent2": "#2f7fd0", "border": "#c3c9d4",
        "ok": "#1a7f4b", "warn": "#b7791f", "err": "#c0392b", "is_dark": False,
    },
    "oled": {  # абсолютный чёрный + неон
        "bg": "#000000", "surface": "#0c0c0f", "surface2": "#17171c", "hover": "#24242b",
        "fg": "#f2f2f2", "muted": "#8b8b96", "accent": "#00e5ff", "accent_hover": "#5cf1ff",
        "on_accent": "#000000", "accent2": "#ff2bd6", "border": "#2b2b34",
        "ok": "#39ff88", "warn": "#ffd500", "err": "#ff4d6d", "is_dark": True,
    },
    "cyberpunk": {
        "bg": "#0d1117", "surface": "#161b22", "surface2": "#21262d", "hover": "#30363d",
        "fg": "#e6edf3", "muted": "#8b949e", "accent": "#fcee0a", "accent_hover": "#fff44f",
        "on_accent": "#0d1117", "accent2": "#00f0ff", "border": "#363c46",
        "ok": "#3ddc97", "warn": "#ffb800", "err": "#ff2a6d", "is_dark": True,
    },
    "dracula": {
        "bg": "#21222c", "surface": "#282a36", "surface2": "#343746", "hover": "#44475a",
        "fg": "#f8f8f2", "muted": "#8b93bd", "accent": "#bd93f9", "accent_hover": "#cfa9ff",
        "on_accent": "#282a36", "accent2": "#ff79c6", "border": "#44475a",
        "ok": "#50fa7b", "warn": "#f1fa8c", "err": "#ff5555", "is_dark": True,
    },
    "nord": {
        "bg": "#2e3440", "surface": "#3b4252", "surface2": "#434c5e", "hover": "#4c566a",
        "fg": "#eceff4", "muted": "#a3adc2", "accent": "#88c0d0", "accent_hover": "#9fd0de",
        "on_accent": "#2e3440", "accent2": "#81a1c1", "border": "#4c566a",
        "ok": "#a3be8c", "warn": "#ebcb8b", "err": "#bf616a", "is_dark": True,
    },
    "outlaw": {  # вестерн / RDR2: кожа, пергамент, янтарь
        "bg": "#1f1a17", "surface": "#2d2520", "surface2": "#3a3029", "hover": "#4a3d33",
        "fg": "#efe3cf", "muted": "#a8957d", "accent": "#d48b38", "accent_hover": "#e6a04f",
        "on_accent": "#1f1a17", "accent2": "#c9a96e", "border": "#524436",
        "ok": "#8fb573", "warn": "#e0b04a", "err": "#d0604f", "is_dark": True,
    },
}
THEME_ORDER = ["dark", "light", "oled", "cyberpunk", "dracula", "nord", "outlaw"]

# =============================================================== профили игр ==
BETHESDA_MARKERS = [
    "meshes", "textures", "sound", "music", "scripts", "interface", "strings", "shaders", "seq",
    "grass", "lodsettings", "video", "facegen", "calientetools", "fonts", "skse", "f4se", "nvse",
    "obse", "fose", "sfse", "skyproc patchers", ".esp", ".esm", ".esl", ".bsa", ".ba2",
]


def _bethesda(gid, name, exe, folders, target="Data"):
    return dict(id=gid, name=name, mode="root", detect=exe, folders=folders, target=target,
                markers=BETHESDA_MARKERS, note="note_bethesda")


BUILTIN_GAMES: list[dict] = [
    dict(id="rdr2", name="Red Dead Redemption 2", mode="folder", detect=["RDR2.exe"],
         folders=["Red Dead Redemption 2"], target="lml", wrappers=["lml"],
         markers=["install.xml"], note="note_rdr2"),
    dict(id="gta5", name="Grand Theft Auto V", mode="root", detect=["GTA5.exe"],
         folders=["Grand Theft Auto V"], target="",
         markers=["scripts", "mods", "x64", "update", "plugins", ".asi"],
         loose={".asi": "", ".cs": "scripts", ".vb": "scripts"}, note="note_gta5"),
    dict(id="gtasa", name="Grand Theft Auto: San Andreas", mode="root",
         detect=["gta_sa.exe", "gta-sa.exe"], folders=["Grand Theft Auto San Andreas"], target="",
         markers=["cleo", "modloader", "models", "data", "text", "audio", "anim", ".asi"],
         loose={".asi": "", ".cs": "cleo"}, note="note_gtasa"),
    dict(id="cp2077", name="Cyberpunk 2077", mode="root", detect=["bin/x64/Cyberpunk2077.exe"],
         folders=["Cyberpunk 2077"], target="",
         markers=["archive", "bin", "r6", "red4ext", "engine", "mods"],
         loose={".archive": "archive/pc/mod", ".xl": "archive/pc/mod", ".reds": "r6/scripts"},
         note="note_cp2077"),
    _bethesda("skyrimse", "The Elder Scrolls V: Skyrim Special Edition", ["SkyrimSE.exe"],
              ["Skyrim Special Edition"]),
    _bethesda("skyrimle", "The Elder Scrolls V: Skyrim (Legendary)", ["TESV.exe"], ["Skyrim"]),
    _bethesda("skyrimvr", "The Elder Scrolls V: Skyrim VR", ["SkyrimVR.exe"], ["SkyrimVR"]),
    _bethesda("oblivion", "The Elder Scrolls IV: Oblivion", ["Oblivion.exe"], ["Oblivion"]),
    _bethesda("morrowind", "The Elder Scrolls III: Morrowind", ["Morrowind.exe"], ["Morrowind"],
              target="Data Files"),
    _bethesda("fo4", "Fallout 4", ["Fallout4.exe"], ["Fallout 4"]),
    _bethesda("fnv", "Fallout: New Vegas", ["FalloutNV.exe"], ["Fallout New Vegas"]),
    _bethesda("fo3", "Fallout 3", ["Fallout3.exe"], ["Fallout 3 goty", "Fallout 3"]),
    _bethesda("starfield", "Starfield", ["Starfield.exe"], ["Starfield"]),
    dict(id="witcher3", name="The Witcher 3: Wild Hunt", mode="folder",
         detect=["bin/x64/witcher3.exe"], folders=["The Witcher 3", "The Witcher 3 Wild Hunt"],
         target="mods", wrappers=["mods", "dlc"], markers=["content"], note="note_witcher3"),
    dict(id="stardew", name="Stardew Valley", mode="folder", detect=["Stardew Valley.exe"],
         folders=["Stardew Valley"], target="Mods", wrappers=["Mods"],
         markers=["manifest.json"], note="note_stardew"),
    dict(id="bannerlord", name="Mount & Blade II: Bannerlord", mode="folder",
         detect=["bin/Win64_Shipping_Client/Bannerlord.exe"],
         folders=["Mount & Blade II Bannerlord"], target="Modules", wrappers=["Modules"],
         markers=["SubModule.xml"], note="note_bannerlord"),
    dict(id="kcd", name="Kingdom Come: Deliverance", mode="folder",
         detect=["Bin/Win64/KingdomCome.exe"], folders=["KingdomComeDeliverance"],
         target="Mods", wrappers=["Mods"], markers=["mod.manifest"]),
    dict(id="rimworld", name="RimWorld", mode="folder", detect=["RimWorldWin64.exe"],
         folders=["RimWorld"], target="Mods", wrappers=["Mods"], markers=["About"],
         note="note_rimworld"),
    dict(id="ksp", name="Kerbal Space Program", mode="root", detect=["KSP_x64.exe"],
         folders=["Kerbal Space Program"], target="", markers=["GameData"]),
    dict(id="l4d2", name="Left 4 Dead 2", mode="root", detect=["left4dead2.exe"],
         folders=["Left 4 Dead 2"], target="left4dead2", rooted=["left4dead2"],
         markers=["addons"], loose={".vpk": "left4dead2/addons"}),
    dict(id="gmod", name="Garry's Mod", mode="folder", detect=["garrysmod"],
         folders=["GarrysMod"], target="garrysmod/addons",
         wrappers={"addons": "garrysmod/addons", "garrysmod": "garrysmod"},
         markers=["addon.json", "lua"]),
    dict(id="mhw", name="Monster Hunter: World", mode="root", detect=["MonsterHunterWorld.exe"],
         folders=["Monster Hunter World"], target="", markers=["nativePC"]),
    dict(id="hogwarts", name="Hogwarts Legacy", mode="root",
         detect=["Phoenix/Binaries/Win64/HogwartsLegacy.exe"], folders=["Hogwarts Legacy"],
         target="Phoenix/Content/Paks", rooted=["phoenix"], markers=["~mods"],
         loose={".pak": "Phoenix/Content/Paks/~mods", ".utoc": "Phoenix/Content/Paks/~mods",
                ".ucas": "Phoenix/Content/Paks/~mods"}, note="note_ue4"),
    dict(id="palworld", name="Palworld", mode="root", detect=["Palworld.exe"],
         folders=["Palworld"], target="Pal/Content/Paks", rooted=["pal"], markers=["~mods"],
         loose={".pak": "Pal/Content/Paks/~mods", ".utoc": "Pal/Content/Paks/~mods",
                ".ucas": "Pal/Content/Paks/~mods"}, note="note_ue4"),
    dict(id="ets2", name="Euro Truck Simulator 2", mode="root",
         paths=[r"%USERPROFILE%\Documents\Euro Truck Simulator 2",
                r"%OneDrive%\Documents\Euro Truck Simulator 2"],
         detect=["profiles", "config.cfg", "mod"], target="", markers=["mod"],
         loose={".scs": "mod"}, note="note_ets"),
    dict(id="ats", name="American Truck Simulator", mode="root",
         paths=[r"%USERPROFILE%\Documents\American Truck Simulator",
                r"%OneDrive%\Documents\American Truck Simulator"],
         detect=["profiles", "config.cfg", "mod"], target="", markers=["mod"],
         loose={".scs": "mod"}, note="note_ets"),
    dict(id="sims4", name="The Sims 4", mode="folder",
         paths=[r"%USERPROFILE%\Documents\Electronic Arts\The Sims 4",
                r"%OneDrive%\Documents\Electronic Arts\The Sims 4"],
         detect=["Mods", "Saves"], target="Mods", wrappers=["Mods"], markers=[],
         note="note_sims4"),
]


# Движковые пресеты: профиль может указать "engine": "<ключ>", недостающие поля берутся отсюда.
ENGINE_PRESETS: dict[str, dict] = {
    "bepinex": dict(mode="root", target="BepInEx/plugins", markers=["BepInEx", "plugins", ".dll"],
                    loose={".dll": "BepInEx/plugins"}, rooted=["BepInEx"], note="note_bepinex"),
    "melonloader": dict(mode="root", target="Mods", markers=["MelonLoader", "Mods", ".dll"],
                        loose={".dll": "Mods"},
                        rooted=["MelonLoader", "Mods", "Plugins", "UserLibs", "UserData"],
                        note="note_melonloader"),
    "re_engine": dict(mode="root", target="", markers=["reframework", "natives"],
                      loose={".lua": "reframework/autorun"}, note="note_re_engine"),
    "unreal_engine": dict(mode="root", target="~mods", markers=["~mods", "LogicMods", "Paks"],
                          loose={".pak": "~mods", ".ucas": "~mods", ".utoc": "~mods"},
                          rooted=["~mods", "LogicMods", "Paks"], note="note_unreal"),
}
ENGINE_ORDER = ["custom", "bepinex", "melonloader", "unreal_engine", "re_engine"]
ENGINE_LABELS = {"bepinex": "BepInEx", "melonloader": "MelonLoader",
                 "unreal_engine": "Unreal Engine", "re_engine": "RE Engine"}
ENGINE_PRESETS["bepinex"]["deps"] = ["winhttp.dll", "BepInEx"]
ENGINE_PRESETS["melonloader"]["deps"] = ["version.dll", "MelonLoader"]

# deps — загрузчики, наличие которых проверяет «Доктор зависимостей»; saves — папки сохранений.
_DOC = r"%USERPROFILE%\Documents"
PROFILE_EXTRAS: dict[str, dict] = {
    "rdr2": dict(deps=["ScriptHookRDR2.dll", "lml"], saves=[_DOC + r"\Rockstar Games\Red Dead Redemption 2\Profiles"]),
    "gta5": dict(deps=["ScriptHookV.dll", "dinput8.dll"], saves=[_DOC + r"\Rockstar Games\GTA V\Profiles"]),
    "gtasa": dict(deps=["cleo", "modloader"]),
    "cp2077": dict(deps=["bin/x64/winmm.dll", "red4ext", "r6/scripts"],
                   saves=[r"%USERPROFILE%\Saved Games\CD Projekt Red\Cyberpunk 2077"]),
    "skyrimse": dict(deps=["skse64_loader.exe"], saves=[_DOC + r"\My Games\Skyrim Special Edition\Saves"]),
    "skyrimle": dict(deps=["skse_loader.exe"], saves=[_DOC + r"\My Games\Skyrim\Saves"]),
    "fo4": dict(deps=["f4se_loader.exe"], saves=[_DOC + r"\My Games\Fallout4\Saves"]),
    "fnv": dict(deps=["nvse_loader.exe"], saves=[_DOC + r"\My Games\FalloutNV\Saves"]),
    "fo3": dict(deps=["fose_loader.exe"], saves=[_DOC + r"\My Games\Fallout3\Saves"]),
    "oblivion": dict(deps=["obse_loader.exe"], saves=[_DOC + r"\My Games\Oblivion\Saves"]),
    "starfield": dict(deps=["sfse_loader.exe"], saves=[_DOC + r"\My Games\Starfield\Saves"]),
    "stardew": dict(deps=["StardewModdingAPI.exe"], saves=[r"%APPDATA%\StardewValley\Saves"]),
    "witcher3": dict(saves=[_DOC + r"\The Witcher 3\gamesaves"]),
    "bannerlord": dict(saves=[_DOC + r"\Mount and Blade II Bannerlord\Game Saves"]),
    "kcd": dict(saves=[r"%USERPROFILE%\Saved Games\kingdomcome\saves"]),
    "rimworld": dict(saves=[r"%USERPROFILE%\AppData\LocalLow\Ludeon Studios\RimWorld by Ludeon Studios\Saves"]),
    "sims4": dict(saves=[_DOC + r"\Electronic Arts\The Sims 4\saves"]),
    "hogwarts": dict(saves=[r"%LOCALAPPDATA%\Hogwarts Legacy\Saved\SaveGames"]),
    "palworld": dict(saves=[r"%LOCALAPPDATA%\Pal\Saved\SaveGames"]),
    "ets2": dict(saves=[_DOC + r"\Euro Truck Simulator 2\profiles"]),
    "ats": dict(saves=[_DOC + r"\American Truck Simulator\profiles"]),
}
for _g in BUILTIN_GAMES:
    _g.update(PROFILE_EXTRAS.get(_g["id"], {}))

_ID_RE = re.compile(r"^\w[\w.-]{0,63}$")  # id попадает в путь бэкапов — только безопасные символы


def apply_preset(p: dict) -> dict:
    preset = ENGINE_PRESETS.get(p.get("engine", ""))
    if preset:
        for k, v in preset.items():
            p.setdefault(k, json.loads(json.dumps(v)))
    return p


def _safe_rel(path) -> bool:
    """Относительный путь без выхода за пределы папки игры."""
    if not isinstance(path, str):
        return False
    q = path.replace("\\", "/")
    if q.startswith("/") or re.match(r"^[A-Za-z]:", q):
        return False
    return ".." not in q.split("/")


def sanitize_profile(raw) -> dict | None:
    """Проверяет профиль из JSON (games.json / games.d). Небезопасные или битые -> None."""
    if not isinstance(raw, dict):
        return None
    p = apply_preset(dict(raw))
    if not _ID_RE.match(str(p.get("id", ""))) or not p.get("name") or p.get("mode") not in ("folder", "root"):
        return None
    for key in ("detect", "folders", "markers", "rooted", "paths", "deps", "saves"):
        v = p.get(key, [])
        if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            return None
    if not all(_safe_rel(x) for x in p.get("detect", []) + p.get("folders", []) + p.get("deps", [])):
        return None
    if not isinstance(p.get("target", ""), str) or not _safe_rel(p.get("target", "")):
        return None
    loose = p.get("loose", {})
    if not isinstance(loose, dict) or not all(isinstance(k, str) and _safe_rel(v) for k, v in loose.items()):
        return None
    wr = p.get("wrappers", [])
    if isinstance(wr, list):
        if not all(_safe_rel(x) for x in wr):
            return None
    elif not (isinstance(wr, dict) and all(isinstance(k, str) and _safe_rel(v) for k, v in wr.items())):
        return None
    if "note" in p and not isinstance(p["note"], str):
        return None
    return p


def parse_loose(text: str) -> dict[str, str]:
    """'.dll=BepInEx/plugins, .pak=~mods' -> {'.dll': 'BepInEx/plugins', '.pak': '~mods'}"""
    out: dict[str, str] = {}
    for part in text.split(","):
        if "=" in part:
            ext, dst = (x.strip() for x in part.split("=", 1))
            if ext:
                out[ext if ext.startswith(".") else "." + ext] = dst.replace("\\", "/").strip("/")
    return out


def format_loose(loose: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in loose.items())


def _profile_entries(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def load_profiles() -> dict[str, dict]:
    profiles = {b["id"]: apply_preset(dict(b)) for b in BUILTIN_GAMES}
    sources = [GAMES_FILE]
    try:
        sources += sorted(GAMES_DIR.glob("*.json"))
    except OSError:
        pass
    for src in sources:
        try:
            entries = _profile_entries(src)
        except Exception:
            continue
        for e in entries:
            prof = sanitize_profile(e)
            if prof:
                prof["custom"] = True
                profiles[prof["id"]] = prof
    return dict(sorted(profiles.items(), key=lambda kv: kv[1]["name"].lower()))


def save_custom_profile(profile: dict) -> None:
    try:
        data = json.loads(GAMES_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []
    data = [p for p in data if p.get("id") != profile["id"]] + [profile]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    GAMES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ========================================================================= i18n ==
STR: dict[str, dict[str, str]] = {
    "ru": {
        "subtitle": "Установка модов из архивов в нужные папки игры",
        "theme_to_light": "☀ Светлая", "theme_to_dark": "🌙 Тёмная", "language": "Язык",
        "game": "Игра", "add_game": "＋ Своя игра", "folder": "Папка", "browse": "Обзор…", "detect": "Найти",
        "st_ok": "✔ Игра найдена", "st_nodetect": "⚠ Файлы игры в этой папке не найдены",
        "st_nofolder": "✖ Папка не найдена — укажи папку игры",
        "drop_title": "⬇  Перетащи архивы или папки с модами сюда",
        "drop_hover": "Отпусти, чтобы установить", "drop_nodnd": "Нажми, чтобы выбрать архивы",
        "drop_sub": "или кликни для выбора  ·  .zip   .7z   .rar",
        "ready": "Готово к установке", "installing": "Установка {i}/{n}: {name}",
        "done_status": "Готово: установлено {ok} из {n}",
        "log": "Журнал", "clear": "Очистить", "installed": "Установленные моды", "search": "Поиск",
        "delete": "Удалить", "refresh": "Обновить", "open_dir": "Открыть папку",
        "hint_unmanaged": "* — установлено не через программу",
        "confirm_delete": "Удалить выбранное?\n\n{names}",
        "msg_busy": "Установка уже идёт, подожди немного.",
        "msg_folder": "Сначала укажи корректную папку игры.",
        "msg_nodetect": "В этой папке не найдены файлы игры «{game}». Всё равно использовать?",
        "dlg_archives": "Выбери архивы с модами", "ft_archives": "Архивы", "ft_all": "Все файлы",
        "dlg_folder": "Папка игры: {game}",
        "log_skip": "⚠ пропущено (не архив и не папка): {name}",
        "log_update": "↻ мод уже был установлен — старая версия удалена, ставлю заново",
        "log_placed": "✔ {dst} — файлов: {n}",
        "log_loose": "⚠ структура не распознана, файлы разложены по типам",
        "log_fallback": "⚠ признаки мода не найдены, скопировано как есть → {dst}",
        "log_autodetect_fail": "⚠ Автопоиск не нашёл игру — укажи папку вручную",
        "err_fomod": "✖ в архиве FOMOD-установщик с выбором опций — такие моды ставь через Vortex / Mod Organizer 2",
        "err_layout": "✖ не удалось определить, куда класть файлы из этого архива (проверь описание мода)",
        "err_perm": "✖ нет доступа к папке — запусти программу от имени администратора",
        "err_empty": "✖ архив пустой",
        "err_no_rar": "✖ для .rar нужен 7-Zip (7z.exe) или UnRAR: установи 7-Zip с 7-zip.org или положи 7z.exe и 7z.dll рядом с программой",
        "err_extract": "✖ не удалось распаковать: {err}",
        "err_format": "✖ формат {ext} не поддерживается",
        "log_done_ok": "✔ Готово: установлено {ok} из {n}", "log_done_part": "⚠ Готово: установлено {ok} из {n}",
        "log_deleted": "🗑 удалено: {name}", "log_delete_fail": "✖ не удалось удалить {name}: {err}",
        "footer_rar_ok": "RAR: ✔ ({tool})", "footer_rar_no": "RAR: ✖ нужен 7-Zip или UnRAR",
        "footer_dnd_no": "перетаскивание: ✖ (pip install tkinterdnd2)",
        "dlg_title": "Добавить свою игру", "dlg_name": "Название игры", "dlg_mode": "Как устанавливать",
        "mode_folder": "Каждый мод — отдельная папка внутри папки назначения",
        "mode_root": "Копировать содержимое по структуре архива",
        "dlg_target": "Папка назначения (внутри папки игры, можно пусто)",
        "dlg_detect": "Файл для опознания игры (например game.exe)",
        "dlg_markers": "Признаки мода через запятую (например manifest.json, .esp)",
        "dlg_save": "Сохранить", "dlg_cancel": "Отмена", "dlg_need_name": "Укажи название игры.",
        "note_rdr2": "Нужен установленный Lenny's Mod Loader (папка lml в папке игры).",
        "note_gta5": "Нужны Script Hook V и ASI Loader. Моды для папки mods (OpenIV) требуют заранее подготовленной копии mods.",
        "note_gtasa": "Нужны ASI Loader, CLEO и/или Mod Loader — они ставятся отдельно.",
        "note_cp2077": "Многим модам нужны RED4ext, Cyber Engine Tweaks и ArchiveXL — они ставятся отдельно.",
        "note_bethesda": "Плагины (.esp/.esm/.esl) нужно включить в Vortex, MO2 или Plugins.txt. Моды с FOMOD-установщиком лучше ставить через Vortex.",
        "note_witcher3": "Конфликты скриптов решает Script Merger; некоторым модам нужны правки конфигов в bin/config.",
        "note_stardew": "Нужен SMAPI.", "note_bannerlord": "Модуль нужно включить в лаунчере игры.",
        "note_rimworld": "Моды нужно включить в меню Mods внутри игры.",
        "note_ue4": "Некоторым модам нужны дополнительные загрузчики (например, UE4SS).",
        "note_ets": "Моды (.scs) включаются в менеджере модов внутри игры.",
        "note_sims4": "Включи моды и скрипт-моды в настройках игры (Game Options → Other).",
    },
    "en": {
        "subtitle": "Install mods from archives into the right game folders",
        "theme_to_light": "☀ Light", "theme_to_dark": "🌙 Dark", "language": "Language",
        "game": "Game", "add_game": "＋ Custom game", "folder": "Folder", "browse": "Browse…", "detect": "Find",
        "st_ok": "✔ Game found", "st_nodetect": "⚠ Game files not found in this folder",
        "st_nofolder": "✖ Folder not found — select the game folder",
        "drop_title": "⬇  Drop mod archives or folders here",
        "drop_hover": "Release to install", "drop_nodnd": "Click to choose archives",
        "drop_sub": "or click to browse  ·  .zip   .7z   .rar",
        "ready": "Ready to install", "installing": "Installing {i}/{n}: {name}",
        "done_status": "Done: {ok} of {n} installed",
        "log": "Log", "clear": "Clear", "installed": "Installed mods", "search": "Search",
        "delete": "Uninstall", "refresh": "Refresh", "open_dir": "Open folder",
        "hint_unmanaged": "* — not installed by this program",
        "confirm_delete": "Remove the selected items?\n\n{names}",
        "msg_busy": "An installation is already running, please wait.",
        "msg_folder": "Please select a valid game folder first.",
        "msg_nodetect": "No files of “{game}” found in this folder. Use it anyway?",
        "dlg_archives": "Choose mod archives", "ft_archives": "Archives", "ft_all": "All files",
        "dlg_folder": "Game folder: {game}",
        "log_skip": "⚠ skipped (not an archive or folder): {name}",
        "log_update": "↻ mod was already installed — old version removed, reinstalling",
        "log_placed": "✔ {dst} — files: {n}",
        "log_loose": "⚠ layout not recognised, files placed by type",
        "log_fallback": "⚠ no mod markers found, copied as is → {dst}",
        "log_autodetect_fail": "⚠ Auto-detect did not find the game — choose the folder manually",
        "err_fomod": "✖ the archive contains a FOMOD installer with options — install such mods with Vortex / Mod Organizer 2",
        "err_layout": "✖ could not work out where this archive's files belong (check the mod description)",
        "err_perm": "✖ access denied — run the program as administrator",
        "err_empty": "✖ the archive is empty",
        "err_no_rar": "✖ .rar needs 7-Zip (7z.exe) or UnRAR: install 7-Zip from 7-zip.org or put 7z.exe and 7z.dll next to the program",
        "err_extract": "✖ extraction failed: {err}",
        "err_format": "✖ format {ext} is not supported",
        "log_done_ok": "✔ Done: {ok} of {n} installed", "log_done_part": "⚠ Done: {ok} of {n} installed",
        "log_deleted": "🗑 removed: {name}", "log_delete_fail": "✖ could not remove {name}: {err}",
        "footer_rar_ok": "RAR: ✔ ({tool})", "footer_rar_no": "RAR: ✖ needs 7-Zip or UnRAR",
        "footer_dnd_no": "drag & drop: ✖ (pip install tkinterdnd2)",
        "dlg_title": "Add a custom game", "dlg_name": "Game name", "dlg_mode": "Install mode",
        "mode_folder": "Each mod is its own folder inside the target folder",
        "mode_root": "Copy contents following the archive structure",
        "dlg_target": "Target folder (inside the game folder, may be empty)",
        "dlg_detect": "File that identifies the game (e.g. game.exe)",
        "dlg_markers": "Mod markers, comma-separated (e.g. manifest.json, .esp)",
        "dlg_save": "Save", "dlg_cancel": "Cancel", "dlg_need_name": "Enter a game name.",
        "note_rdr2": "Requires Lenny's Mod Loader (the lml folder inside the game folder).",
        "note_gta5": "Requires Script Hook V and an ASI Loader. Mods for the mods folder (OpenIV) need a prepared copy of mods.",
        "note_gtasa": "Requires ASI Loader, CLEO and/or Mod Loader — installed separately.",
        "note_cp2077": "Many mods need RED4ext, Cyber Engine Tweaks and ArchiveXL — installed separately.",
        "note_bethesda": "Enable plugins (.esp/.esm/.esl) in Vortex, MO2 or Plugins.txt. Mods with a FOMOD installer are better installed with Vortex.",
        "note_witcher3": "Use Script Merger for script conflicts; some mods need config edits in bin/config.",
        "note_stardew": "Requires SMAPI.", "note_bannerlord": "Enable the module in the game launcher.",
        "note_rimworld": "Enable mods in the in-game Mods menu.",
        "note_ue4": "Some mods need extra loaders (e.g. UE4SS).",
        "note_ets": "Mods (.scs) are enabled in the in-game Mod Manager.",
        "note_sims4": "Enable mods and script mods in Game Options → Other.",
    },
    "uk": {
        "subtitle": "Встановлення модів з архівів у потрібні папки гри",
        "theme_to_light": "☀ Світла", "theme_to_dark": "🌙 Темна", "language": "Мова",
        "game": "Гра", "add_game": "＋ Своя гра", "folder": "Папка", "browse": "Огляд…", "detect": "Знайти",
        "st_ok": "✔ Гру знайдено", "st_nodetect": "⚠ Файлів гри в цій папці не знайдено",
        "st_nofolder": "✖ Папку не знайдено — вкажи папку гри",
        "drop_title": "⬇  Перетягни архіви або папки з модами сюди",
        "drop_hover": "Відпусти, щоб встановити", "drop_nodnd": "Натисни, щоб вибрати архіви",
        "drop_sub": "або клікни для вибору  ·  .zip   .7z   .rar",
        "ready": "Готово до встановлення", "installing": "Встановлення {i}/{n}: {name}",
        "done_status": "Готово: встановлено {ok} з {n}",
        "log": "Журнал", "clear": "Очистити", "installed": "Встановлені моди", "search": "Пошук",
        "delete": "Видалити", "refresh": "Оновити", "open_dir": "Відкрити папку",
        "hint_unmanaged": "* — встановлено не через програму",
        "confirm_delete": "Видалити вибране?\n\n{names}",
        "msg_busy": "Встановлення вже триває, зачекай.",
        "msg_folder": "Спершу вкажи коректну папку гри.",
        "msg_nodetect": "У цій папці не знайдено файлів гри «{game}». Все одно використати?",
        "dlg_archives": "Обери архіви з модами", "ft_archives": "Архіви", "ft_all": "Усі файли",
        "dlg_folder": "Папка гри: {game}",
        "log_skip": "⚠ пропущено (не архів і не папка): {name}",
        "log_update": "↻ мод уже був встановлений — стару версію видалено, встановлюю заново",
        "log_placed": "✔ {dst} — файлів: {n}",
        "log_loose": "⚠ структуру не розпізнано, файли розкладено за типами",
        "log_fallback": "⚠ ознак мода не знайдено, скопійовано як є → {dst}",
        "log_autodetect_fail": "⚠ Автопошук не знайшов гру — вкажи папку вручну",
        "err_fomod": "✖ в архіві FOMOD-інсталятор з вибором опцій — такі моди став через Vortex / Mod Organizer 2",
        "err_layout": "✖ не вдалося визначити, куди класти файли з цього архіву (перевір опис мода)",
        "err_perm": "✖ немає доступу до папки — запусти програму від імені адміністратора",
        "err_empty": "✖ архів порожній",
        "err_no_rar": "✖ для .rar потрібен 7-Zip (7z.exe) або UnRAR: встанови 7-Zip з 7-zip.org або поклади 7z.exe і 7z.dll поруч із програмою",
        "err_extract": "✖ не вдалося розпакувати: {err}",
        "err_format": "✖ формат {ext} не підтримується",
        "log_done_ok": "✔ Готово: встановлено {ok} з {n}", "log_done_part": "⚠ Готово: встановлено {ok} з {n}",
        "log_deleted": "🗑 видалено: {name}", "log_delete_fail": "✖ не вдалося видалити {name}: {err}",
        "footer_rar_ok": "RAR: ✔ ({tool})", "footer_rar_no": "RAR: ✖ потрібен 7-Zip або UnRAR",
        "footer_dnd_no": "перетягування: ✖ (pip install tkinterdnd2)",
        "dlg_title": "Додати свою гру", "dlg_name": "Назва гри", "dlg_mode": "Як встановлювати",
        "mode_folder": "Кожен мод — окрема папка всередині папки призначення",
        "mode_root": "Копіювати вміст за структурою архіву",
        "dlg_target": "Папка призначення (всередині папки гри, можна порожньо)",
        "dlg_detect": "Файл для розпізнавання гри (напр. game.exe)",
        "dlg_markers": "Ознаки мода через кому (напр. manifest.json, .esp)",
        "dlg_save": "Зберегти", "dlg_cancel": "Скасувати", "dlg_need_name": "Вкажи назву гри.",
    },
    "de": {
        "subtitle": "Mods aus Archiven in die richtigen Spielordner installieren",
        "theme_to_light": "☀ Hell", "theme_to_dark": "🌙 Dunkel", "language": "Sprache",
        "game": "Spiel", "add_game": "＋ Eigenes Spiel", "folder": "Ordner", "browse": "Durchsuchen…", "detect": "Finden",
        "st_ok": "✔ Spiel gefunden", "st_nodetect": "⚠ Keine Spieldateien in diesem Ordner gefunden",
        "st_nofolder": "✖ Ordner nicht gefunden – Spielordner angeben",
        "drop_title": "⬇  Mod-Archive oder -Ordner hierher ziehen",
        "drop_hover": "Loslassen zum Installieren", "drop_nodnd": "Klicken, um Archive auszuwählen",
        "drop_sub": "oder klicken zum Auswählen  ·  .zip   .7z   .rar",
        "ready": "Bereit zur Installation", "installing": "Installiere {i}/{n}: {name}",
        "done_status": "Fertig: {ok} von {n} installiert",
        "log": "Protokoll", "clear": "Leeren", "installed": "Installierte Mods", "search": "Suche",
        "delete": "Löschen", "refresh": "Aktualisieren", "open_dir": "Ordner öffnen",
        "hint_unmanaged": "* – nicht über dieses Programm installiert",
        "confirm_delete": "Auswahl löschen?\n\n{names}",
        "msg_busy": "Es läuft bereits eine Installation, bitte warten.",
        "msg_folder": "Bitte zuerst einen gültigen Spielordner angeben.",
        "msg_nodetect": "In diesem Ordner wurden keine Dateien von „{game}“ gefunden. Trotzdem verwenden?",
        "dlg_archives": "Mod-Archive auswählen", "ft_archives": "Archive", "ft_all": "Alle Dateien",
        "dlg_folder": "Spielordner: {game}",
        "log_skip": "⚠ übersprungen (kein Archiv/Ordner): {name}",
        "log_update": "↻ Mod war bereits installiert – alte Version entfernt, installiere neu",
        "log_placed": "✔ {dst} – Dateien: {n}",
        "log_loose": "⚠ Struktur nicht erkannt, Dateien nach Typ verteilt",
        "log_fallback": "⚠ keine Mod-Merkmale gefunden, unverändert kopiert → {dst}",
        "log_autodetect_fail": "⚠ Automatische Suche erfolglos – Ordner manuell wählen",
        "err_fomod": "✖ Das Archiv enthält einen FOMOD-Installer mit Optionen – solche Mods bitte mit Vortex / Mod Organizer 2 installieren",
        "err_layout": "✖ Zielordner für die Dateien dieses Archivs konnte nicht bestimmt werden (Modbeschreibung prüfen)",
        "err_perm": "✖ Kein Zugriff auf den Ordner – Programm als Administrator starten",
        "err_empty": "✖ Archiv ist leer",
        "err_no_rar": "✖ Für .rar wird 7-Zip (7z.exe) oder UnRAR benötigt: 7-Zip von 7-zip.org installieren oder 7z.exe und 7z.dll neben das Programm legen",
        "err_extract": "✖ Entpacken fehlgeschlagen: {err}",
        "err_format": "✖ Format {ext} wird nicht unterstützt",
        "log_done_ok": "✔ Fertig: {ok} von {n} installiert", "log_done_part": "⚠ Fertig: {ok} von {n} installiert",
        "log_deleted": "🗑 gelöscht: {name}", "log_delete_fail": "✖ {name} konnte nicht gelöscht werden: {err}",
        "footer_rar_ok": "RAR: ✔ ({tool})", "footer_rar_no": "RAR: ✖ 7-Zip oder UnRAR nötig",
        "footer_dnd_no": "Drag & Drop: ✖ (pip install tkinterdnd2)",
        "dlg_title": "Eigenes Spiel hinzufügen", "dlg_name": "Spielname", "dlg_mode": "Installationsart",
        "mode_folder": "Jeder Mod = eigener Ordner im Zielordner",
        "mode_root": "Inhalt nach Archivstruktur kopieren",
        "dlg_target": "Zielordner (innerhalb des Spielordners, darf leer sein)",
        "dlg_detect": "Datei zur Erkennung des Spiels (z. B. game.exe)",
        "dlg_markers": "Mod-Merkmale, kommagetrennt (z. B. manifest.json, .esp)",
        "dlg_save": "Speichern", "dlg_cancel": "Abbrechen", "dlg_need_name": "Bitte einen Spielnamen angeben.",
    },
}
LANG_NAMES = {"ru": "Русский", "en": "English", "uk": "Українська", "de": "Deutsch",
              "fr": "Français", "es": "Español", "zh": "简体中文"}


_STR_EXTRA: dict[str, dict[str, str]] = {
    "ru": {
        "engine_template": "Шаблон движка", "engine_custom": "Пользовательский",
        "dlg_loose": "Раскладка по расширению (например .dll=BepInEx/plugins)",
        "dlg_invalid": "Проверь поля: пути должны быть относительными и без «..».",
        "conflict_title": "Конфликт файлов",
        "conflict_header": "Внимание! Этот мод перезапишет следующие файлы мода \"{name}\":",
        "conflict_more": "… и ещё {n}", "conflict_question": "Продолжить установку?",
        "btn_overwrite": "Да, перезаписать", "btn_skip": "Пропустить / Отмена",
        "log_conflict_skip": "⚠ установка пропущена из-за конфликта файлов",
        "note_bepinex": "Нужен установленный BepInEx (распакуй его в папку игры).",
        "note_melonloader": "Нужен установленный MelonLoader.",
        "note_re_engine": "Нужен REFramework (dinput8.dll) в папке игры.",
        "note_unreal": "Выбери папку Paks (или ту, где лежит ~mods) как папку игры либо задай полный путь в поле «Папка назначения».",
    },
    "en": {
        "engine_template": "Engine template", "engine_custom": "Custom",
        "dlg_loose": "Placement by extension (e.g. .dll=BepInEx/plugins)",
        "dlg_invalid": "Check the fields: paths must be relative and must not contain “..”.",
        "conflict_title": "File conflict",
        "conflict_header": "Warning! This mod will overwrite the following files of the mod \"{name}\":",
        "conflict_more": "… and {n} more", "conflict_question": "Continue the installation?",
        "btn_overwrite": "Yes, overwrite", "btn_skip": "Skip / Cancel",
        "log_conflict_skip": "⚠ installation skipped because of a file conflict",
        "note_bepinex": "Requires BepInEx to be installed (extract it into the game folder).",
        "note_melonloader": "Requires MelonLoader to be installed.",
        "note_re_engine": "Requires REFramework (dinput8.dll) in the game folder.",
        "note_unreal": "Select the Paks folder (or the one containing ~mods) as the game folder, or enter the full path in “Target folder”.",
    },
    "uk": {
        "engine_template": "Шаблон рушія", "engine_custom": "Користувацький",
        "dlg_loose": "Розкладка за розширенням (напр. .dll=BepInEx/plugins)",
        "dlg_invalid": "Перевір поля: шляхи мають бути відносними й без «..».",
        "conflict_title": "Конфлікт файлів",
        "conflict_header": "Увага! Цей мод перезапише такі файли моду \"{name}\":",
        "conflict_more": "… і ще {n}", "conflict_question": "Продовжити встановлення?",
        "btn_overwrite": "Так, перезаписати", "btn_skip": "Пропустити / Скасувати",
        "log_conflict_skip": "⚠ встановлення пропущено через конфлікт файлів",
        "note_rdr2": "Потрібен встановлений Lenny's Mod Loader (папка lml у папці гри).",
        "note_gta5": "Потрібні Script Hook V та ASI Loader. Моди для папки mods (OpenIV) потребують заздалегідь підготовленої копії mods.",
        "note_gtasa": "Потрібні ASI Loader, CLEO та/або Mod Loader — вони встановлюються окремо.",
        "note_cp2077": "Багатьом модам потрібні RED4ext, Cyber Engine Tweaks та ArchiveXL — вони встановлюються окремо.",
        "note_bethesda": "Плагіни (.esp/.esm/.esl) потрібно ввімкнути у Vortex, MO2 або Plugins.txt. Моди з FOMOD-інсталятором краще ставити через Vortex.",
        "note_witcher3": "Конфлікти скриптів вирішує Script Merger; деяким модам потрібні правки конфігів у bin/config.",
        "note_stardew": "Потрібен SMAPI.", "note_bannerlord": "Модуль потрібно ввімкнути в лаунчері гри.",
        "note_rimworld": "Моди потрібно ввімкнути в меню Mods усередині гри.",
        "note_ue4": "Деяким модам потрібні додаткові завантажувачі (наприклад, UE4SS).",
        "note_ets": "Моди (.scs) вмикаються в менеджері модів усередині гри.",
        "note_sims4": "Увімкни моди та скрипт-моди в налаштуваннях гри (Game Options → Other).",
        "note_bepinex": "Потрібен встановлений BepInEx (розпакуй його в папку гри).",
        "note_melonloader": "Потрібен встановлений MelonLoader.",
        "note_re_engine": "Потрібен REFramework (dinput8.dll) у папці гри.",
        "note_unreal": "Обери папку Paks (або ту, де лежить ~mods) як папку гри, або задай повний шлях у полі «Папка призначення».",
    },
    "de": {
        "engine_template": "Engine-Vorlage", "engine_custom": "Benutzerdefiniert",
        "dlg_loose": "Zuordnung nach Endung (z. B. .dll=BepInEx/plugins)",
        "dlg_invalid": "Felder prüfen: Pfade müssen relativ und ohne „..“ sein.",
        "conflict_title": "Dateikonflikt",
        "conflict_header": "Achtung! Dieser Mod überschreibt folgende Dateien des Mods \"{name}\":",
        "conflict_more": "… und {n} weitere", "conflict_question": "Installation fortsetzen?",
        "btn_overwrite": "Ja, überschreiben", "btn_skip": "Überspringen / Abbrechen",
        "log_conflict_skip": "⚠ Installation wegen Dateikonflikt übersprungen",
        "note_rdr2": "Lenny's Mod Loader muss installiert sein (Ordner lml im Spielordner).",
        "note_gta5": "Script Hook V und ein ASI Loader werden benötigt. Mods für den mods-Ordner (OpenIV) brauchen eine vorbereitete mods-Kopie.",
        "note_gtasa": "ASI Loader, CLEO und/oder Mod Loader werden benötigt – separat installieren.",
        "note_cp2077": "Viele Mods brauchen RED4ext, Cyber Engine Tweaks und ArchiveXL – separat installieren.",
        "note_bethesda": "Plugins (.esp/.esm/.esl) in Vortex, MO2 oder Plugins.txt aktivieren. Mods mit FOMOD-Installer besser über Vortex installieren.",
        "note_witcher3": "Skriptkonflikte löst der Script Merger; manche Mods brauchen Config-Anpassungen in bin/config.",
        "note_stardew": "SMAPI wird benötigt.", "note_bannerlord": "Das Modul im Spiel-Launcher aktivieren.",
        "note_rimworld": "Mods im Mods-Menü des Spiels aktivieren.",
        "note_ue4": "Manche Mods brauchen zusätzliche Loader (z. B. UE4SS).",
        "note_ets": "Mods (.scs) werden im Mod-Manager des Spiels aktiviert.",
        "note_sims4": "Mods und Skript-Mods in den Spieloptionen aktivieren (Game Options → Other).",
        "note_bepinex": "BepInEx muss installiert sein (in den Spielordner entpacken).",
        "note_melonloader": "MelonLoader muss installiert sein.",
        "note_re_engine": "REFramework (dinput8.dll) wird im Spielordner benötigt.",
        "note_unreal": "Den Ordner Paks (oder den mit ~mods) als Spielordner wählen oder den vollständigen Pfad im Feld „Zielordner“ angeben.",
    },
}
for _lang, _extra in _STR_EXTRA.items():
    STR[_lang].update(_extra)

_STR_V32: dict[str, dict[str, str]] = {
    "ru": {
        "detect": "Автопоиск",
        "choose_game": "Выберите игру...",
        "st_no_game": "Сначала выберите игру из списка выше",
        "drop_no_game": "Сначала выберите игру для установки модов",
        "drop_no_game_sub": "Нажмите «Найти» или откройте список игр выше",
        "msg_choose_game": "Сначала выберите игру из списка — тогда станет понятно, куда устанавливать моды.",
        "find_game": "Найти", "search_title": "Поиск игры", "search_hint": "Начните вводить название игры",
        "search_empty": "Ничего не найдено", "badge_installed": "Установлена", "badge_notfound": "Не найдена",
        "appearance": "Внешний вид", "theme_dark": "Тёмная (графит)", "theme_light": "Светлая",
        "theme_oled": "OLED (чёрная)", "theme_cyberpunk": "Cyberpunk", "theme_dracula": "Dracula",
        "theme_nord": "Nord", "theme_outlaw": "Outlaw (вестерн)", "pick_archives": "Выбрать архивы…",
    },
    "en": {
        "detect": "Auto-detect",
        "choose_game": "Choose game...",
        "st_no_game": "Choose a game from the list above first",
        "drop_no_game": "Choose a game first to install mods",
        "drop_no_game_sub": "Press “Find” or open the game list above",
        "msg_choose_game": "Please choose a game from the list first — then I'll know where to install the mods.",
        "find_game": "Find", "search_title": "Find a game", "search_hint": "Start typing the game name",
        "search_empty": "Nothing found", "badge_installed": "Installed", "badge_notfound": "Not found",
        "appearance": "Appearance", "theme_dark": "Dark (graphite)", "theme_light": "Light",
        "theme_oled": "OLED (black)", "theme_cyberpunk": "Cyberpunk", "theme_dracula": "Dracula",
        "theme_nord": "Nord", "theme_outlaw": "Outlaw (western)", "pick_archives": "Choose archives…",
    },
    "uk": {
        "detect": "Автопошук",
        "choose_game": "Оберіть гру...",
        "st_no_game": "Спершу оберіть гру зі списку вище",
        "drop_no_game": "Спершу оберіть гру для встановлення модів",
        "drop_no_game_sub": "Натисніть «Знайти» або відкрийте список ігор вище",
        "msg_choose_game": "Спершу оберіть гру зі списку — тоді стане зрозуміло, куди встановлювати моди.",
        "find_game": "Знайти", "search_title": "Пошук гри", "search_hint": "Почніть вводити назву гри",
        "search_empty": "Нічого не знайдено", "badge_installed": "Встановлена", "badge_notfound": "Не знайдена",
        "appearance": "Зовнішній вигляд", "theme_dark": "Темна (графіт)", "theme_light": "Світла",
        "theme_oled": "OLED (чорна)", "theme_cyberpunk": "Cyberpunk", "theme_dracula": "Dracula",
        "theme_nord": "Nord", "theme_outlaw": "Outlaw (вестерн)", "pick_archives": "Обрати архіви…",
    },
    "de": {
        "detect": "Auto-Suche",
        "choose_game": "Spiel wählen...",
        "st_no_game": "Zuerst oben ein Spiel aus der Liste wählen",
        "drop_no_game": "Zuerst ein Spiel für die Mod-Installation wählen",
        "drop_no_game_sub": "Auf „Suchen“ klicken oder oben die Spieleliste öffnen",
        "msg_choose_game": "Bitte zuerst ein Spiel aus der Liste wählen – dann weiß ich, wohin die Mods gehören.",
        "find_game": "Suchen", "search_title": "Spiel suchen", "search_hint": "Spielnamen eingeben",
        "search_empty": "Nichts gefunden", "badge_installed": "Installiert", "badge_notfound": "Nicht gefunden",
        "appearance": "Darstellung", "theme_dark": "Dunkel (Graphit)", "theme_light": "Hell",
        "theme_oled": "OLED (schwarz)", "theme_cyberpunk": "Cyberpunk", "theme_dracula": "Dracula",
        "theme_nord": "Nord", "theme_outlaw": "Outlaw (Western)", "pick_archives": "Archive wählen…",
    },
    "fr": {
        "subtitle": "Installez des mods depuis des archives dans les bons dossiers du jeu",
        "theme_to_light": "☀ Clair", "theme_to_dark": "🌙 Sombre", "language": "Langue",
        "game": "Jeu", "add_game": "＋ Jeu personnalisé", "folder": "Dossier", "browse": "Parcourir…",
        "detect": "Détection auto",
        "st_ok": "✔ Jeu trouvé", "st_nodetect": "⚠ Fichiers du jeu introuvables dans ce dossier",
        "st_nofolder": "✖ Dossier introuvable — indiquez le dossier du jeu",
        "drop_title": "⬇  Déposez ici des archives ou des dossiers de mods",
        "drop_hover": "Relâchez pour installer", "drop_nodnd": "Cliquez pour choisir des archives",
        "drop_sub": "ou cliquez pour parcourir  ·  .zip   .7z   .rar",
        "ready": "Prêt à installer", "installing": "Installation {i}/{n} : {name}",
        "done_status": "Terminé : {ok} sur {n} installé(s)",
        "log": "Journal", "clear": "Effacer", "installed": "Mods installés", "search": "Recherche",
        "delete": "Désinstaller", "refresh": "Actualiser", "open_dir": "Ouvrir le dossier",
        "hint_unmanaged": "* — non installé par ce programme",
        "confirm_delete": "Supprimer les éléments sélectionnés ?\n\n{names}",
        "msg_busy": "Une installation est déjà en cours, veuillez patienter.",
        "msg_folder": "Veuillez d'abord indiquer un dossier de jeu valide.",
        "msg_nodetect": "Aucun fichier de « {game} » dans ce dossier. L'utiliser quand même ?",
        "dlg_archives": "Choisir des archives de mods", "ft_archives": "Archives", "ft_all": "Tous les fichiers",
        "dlg_folder": "Dossier du jeu : {game}",
        "log_skip": "⚠ ignoré (ni archive ni dossier) : {name}",
        "log_update": "↻ mod déjà installé — ancienne version supprimée, réinstallation",
        "log_placed": "✔ {dst} — fichiers : {n}",
        "log_loose": "⚠ structure non reconnue, fichiers répartis par type",
        "log_fallback": "⚠ aucun indice de mod trouvé, copié tel quel → {dst}",
        "log_autodetect_fail": "⚠ La détection automatique n'a pas trouvé le jeu — choisissez le dossier manuellement",
        "err_fomod": "✖ l'archive contient un installateur FOMOD avec options — installez ces mods avec Vortex / Mod Organizer 2",
        "err_layout": "✖ impossible de déterminer où placer les fichiers de cette archive (voir la description du mod)",
        "err_perm": "✖ accès refusé — lancez le programme en tant qu'administrateur",
        "err_empty": "✖ l'archive est vide",
        "err_no_rar": "✖ .rar nécessite 7-Zip (7z.exe) ou UnRAR : installez 7-Zip depuis 7-zip.org ou placez 7z.exe et 7z.dll à côté du programme",
        "err_extract": "✖ échec de l'extraction : {err}",
        "err_format": "✖ format {ext} non pris en charge",
        "log_done_ok": "✔ Terminé : {ok} sur {n} installé(s)", "log_done_part": "⚠ Terminé : {ok} sur {n} installé(s)",
        "log_deleted": "🗑 supprimé : {name}", "log_delete_fail": "✖ impossible de supprimer {name} : {err}",
        "footer_rar_ok": "RAR : ✔ ({tool})", "footer_rar_no": "RAR : ✖ 7-Zip ou UnRAR requis",
        "footer_dnd_no": "glisser-déposer : ✖ (pip install tkinterdnd2)",
        "dlg_title": "Ajouter un jeu personnalisé", "dlg_name": "Nom du jeu", "dlg_mode": "Mode d'installation",
        "mode_folder": "Chaque mod = son propre dossier dans le dossier cible",
        "mode_root": "Copier le contenu selon la structure de l'archive",
        "dlg_target": "Dossier cible (dans le dossier du jeu, peut être vide)",
        "dlg_detect": "Fichier identifiant le jeu (ex. game.exe)",
        "dlg_markers": "Marqueurs de mod, séparés par des virgules (ex. manifest.json, .esp)",
        "dlg_save": "Enregistrer", "dlg_cancel": "Annuler", "dlg_need_name": "Saisissez le nom du jeu.",
        "note_rdr2": "Nécessite Lenny's Mod Loader (dossier lml dans le dossier du jeu).",
        "note_gta5": "Nécessite Script Hook V et un ASI Loader. Les mods du dossier mods (OpenIV) demandent une copie préparée de mods.",
        "note_gtasa": "Nécessite ASI Loader, CLEO et/ou Mod Loader — à installer séparément.",
        "note_cp2077": "Beaucoup de mods nécessitent RED4ext, Cyber Engine Tweaks et ArchiveXL — à installer séparément.",
        "note_bethesda": "Activez les plugins (.esp/.esm/.esl) dans Vortex, MO2 ou Plugins.txt. Les mods avec installateur FOMOD s'installent mieux avec Vortex.",
        "note_witcher3": "Script Merger résout les conflits de scripts ; certains mods demandent des modifications dans bin/config.",
        "note_stardew": "Nécessite SMAPI.", "note_bannerlord": "Activez le module dans le lanceur du jeu.",
        "note_rimworld": "Activez les mods dans le menu Mods du jeu.",
        "note_ue4": "Certains mods nécessitent des chargeurs supplémentaires (ex. UE4SS).",
        "note_ets": "Les mods (.scs) s'activent dans le gestionnaire de mods du jeu.",
        "note_sims4": "Activez les mods et les mods de scripts dans Options du jeu → Autre.",
        "engine_template": "Modèle de moteur", "engine_custom": "Personnalisé",
        "dlg_loose": "Placement par extension (ex. .dll=BepInEx/plugins)",
        "dlg_invalid": "Vérifiez les champs : les chemins doivent être relatifs et sans « .. ».",
        "conflict_title": "Conflit de fichiers",
        "conflict_header": "Attention ! Ce mod va écraser les fichiers suivants du mod \"{name}\" :",
        "conflict_more": "… et {n} autres", "conflict_question": "Poursuivre l'installation ?",
        "btn_overwrite": "Oui, écraser", "btn_skip": "Ignorer / Annuler",
        "log_conflict_skip": "⚠ installation ignorée en raison d'un conflit de fichiers",
        "note_bepinex": "Nécessite BepInEx (à extraire dans le dossier du jeu).",
        "note_melonloader": "Nécessite MelonLoader.",
        "note_re_engine": "Nécessite REFramework (dinput8.dll) dans le dossier du jeu.",
        "note_unreal": "Choisissez le dossier Paks (ou celui contenant ~mods) comme dossier du jeu, ou saisissez le chemin complet dans « Dossier cible ».",
        "choose_game": "Choisir un jeu...",
        "st_no_game": "Choisissez d'abord un jeu dans la liste ci-dessus",
        "drop_no_game": "Choisissez d'abord un jeu pour installer des mods",
        "drop_no_game_sub": "Cliquez sur « Rechercher » ou ouvrez la liste des jeux ci-dessus",
        "msg_choose_game": "Choisissez d'abord un jeu dans la liste : je saurai alors où installer les mods.",
        "find_game": "Rechercher", "search_title": "Rechercher un jeu", "search_hint": "Commencez à saisir le nom du jeu",
        "search_empty": "Aucun résultat", "badge_installed": "Installé", "badge_notfound": "Introuvable",
        "appearance": "Apparence", "theme_dark": "Sombre (graphite)", "theme_light": "Clair",
        "theme_oled": "OLED (noir)", "theme_cyberpunk": "Cyberpunk", "theme_dracula": "Dracula",
        "theme_nord": "Nord", "theme_outlaw": "Outlaw (western)", "pick_archives": "Choisir des archives…",
    },
    "es": {
        "subtitle": "Instala mods desde archivos en las carpetas correctas del juego",
        "theme_to_light": "☀ Claro", "theme_to_dark": "🌙 Oscuro", "language": "Idioma",
        "game": "Juego", "add_game": "＋ Juego propio", "folder": "Carpeta", "browse": "Examinar…",
        "detect": "Autodetectar",
        "st_ok": "✔ Juego encontrado", "st_nodetect": "⚠ No se encontraron archivos del juego en esta carpeta",
        "st_nofolder": "✖ Carpeta no encontrada: indica la carpeta del juego",
        "drop_title": "⬇  Suelta aquí archivos o carpetas de mods",
        "drop_hover": "Suelta para instalar", "drop_nodnd": "Haz clic para elegir archivos",
        "drop_sub": "o haz clic para examinar  ·  .zip   .7z   .rar",
        "ready": "Listo para instalar", "installing": "Instalando {i}/{n}: {name}",
        "done_status": "Listo: {ok} de {n} instalados",
        "log": "Registro", "clear": "Limpiar", "installed": "Mods instalados", "search": "Buscar",
        "delete": "Desinstalar", "refresh": "Actualizar", "open_dir": "Abrir carpeta",
        "hint_unmanaged": "* — no instalado por este programa",
        "confirm_delete": "¿Eliminar los elementos seleccionados?\n\n{names}",
        "msg_busy": "Ya hay una instalación en curso, espera un momento.",
        "msg_folder": "Primero indica una carpeta de juego válida.",
        "msg_nodetect": "No se encontraron archivos de «{game}» en esta carpeta. ¿Usarla de todos modos?",
        "dlg_archives": "Elige archivos de mods", "ft_archives": "Archivos comprimidos", "ft_all": "Todos los archivos",
        "dlg_folder": "Carpeta del juego: {game}",
        "log_skip": "⚠ omitido (no es un archivo ni una carpeta): {name}",
        "log_update": "↻ el mod ya estaba instalado: versión anterior eliminada, reinstalando",
        "log_placed": "✔ {dst} — archivos: {n}",
        "log_loose": "⚠ estructura no reconocida, archivos colocados por tipo",
        "log_fallback": "⚠ no se encontraron marcas de mod, copiado tal cual → {dst}",
        "log_autodetect_fail": "⚠ La detección automática no encontró el juego: elige la carpeta manualmente",
        "err_fomod": "✖ el archivo contiene un instalador FOMOD con opciones: instala estos mods con Vortex / Mod Organizer 2",
        "err_layout": "✖ no se pudo determinar dónde van los archivos de este paquete (consulta la descripción del mod)",
        "err_perm": "✖ acceso denegado: ejecuta el programa como administrador",
        "err_empty": "✖ el archivo está vacío",
        "err_no_rar": "✖ .rar requiere 7-Zip (7z.exe) o UnRAR: instala 7-Zip desde 7-zip.org o coloca 7z.exe y 7z.dll junto al programa",
        "err_extract": "✖ error al extraer: {err}",
        "err_format": "✖ el formato {ext} no es compatible",
        "log_done_ok": "✔ Listo: {ok} de {n} instalados", "log_done_part": "⚠ Listo: {ok} de {n} instalados",
        "log_deleted": "🗑 eliminado: {name}", "log_delete_fail": "✖ no se pudo eliminar {name}: {err}",
        "footer_rar_ok": "RAR: ✔ ({tool})", "footer_rar_no": "RAR: ✖ requiere 7-Zip o UnRAR",
        "footer_dnd_no": "arrastrar y soltar: ✖ (pip install tkinterdnd2)",
        "dlg_title": "Añadir un juego propio", "dlg_name": "Nombre del juego", "dlg_mode": "Modo de instalación",
        "mode_folder": "Cada mod es su propia carpeta dentro de la carpeta de destino",
        "mode_root": "Copiar el contenido según la estructura del archivo",
        "dlg_target": "Carpeta de destino (dentro de la carpeta del juego, puede estar vacía)",
        "dlg_detect": "Archivo que identifica el juego (p. ej. game.exe)",
        "dlg_markers": "Marcas de mod separadas por comas (p. ej. manifest.json, .esp)",
        "dlg_save": "Guardar", "dlg_cancel": "Cancelar", "dlg_need_name": "Introduce el nombre del juego.",
        "note_rdr2": "Requiere Lenny's Mod Loader (carpeta lml dentro de la carpeta del juego).",
        "note_gta5": "Requiere Script Hook V y un ASI Loader. Los mods para la carpeta mods (OpenIV) necesitan una copia preparada de mods.",
        "note_gtasa": "Requiere ASI Loader, CLEO y/o Mod Loader, que se instalan por separado.",
        "note_cp2077": "Muchos mods necesitan RED4ext, Cyber Engine Tweaks y ArchiveXL, que se instalan por separado.",
        "note_bethesda": "Activa los plugins (.esp/.esm/.esl) en Vortex, MO2 o Plugins.txt. Los mods con instalador FOMOD es mejor instalarlos con Vortex.",
        "note_witcher3": "Script Merger resuelve los conflictos de scripts; algunos mods requieren cambios de configuración en bin/config.",
        "note_stardew": "Requiere SMAPI.", "note_bannerlord": "Activa el módulo en el lanzador del juego.",
        "note_rimworld": "Activa los mods en el menú Mods del juego.",
        "note_ue4": "Algunos mods necesitan cargadores adicionales (p. ej. UE4SS).",
        "note_ets": "Los mods (.scs) se activan en el gestor de mods del juego.",
        "note_sims4": "Activa los mods y los mods de script en Opciones del juego → Otros.",
        "engine_template": "Plantilla de motor", "engine_custom": "Personalizado",
        "dlg_loose": "Colocación por extensión (p. ej. .dll=BepInEx/plugins)",
        "dlg_invalid": "Revisa los campos: las rutas deben ser relativas y no contener «..».",
        "conflict_title": "Conflicto de archivos",
        "conflict_header": "¡Atención! Este mod sobrescribirá los siguientes archivos del mod \"{name}\":",
        "conflict_more": "… y {n} más", "conflict_question": "¿Continuar con la instalación?",
        "btn_overwrite": "Sí, sobrescribir", "btn_skip": "Omitir / Cancelar",
        "log_conflict_skip": "⚠ instalación omitida por un conflicto de archivos",
        "note_bepinex": "Requiere BepInEx instalado (extráelo en la carpeta del juego).",
        "note_melonloader": "Requiere MelonLoader instalado.",
        "note_re_engine": "Requiere REFramework (dinput8.dll) en la carpeta del juego.",
        "note_unreal": "Elige la carpeta Paks (o la que contiene ~mods) como carpeta del juego, o escribe la ruta completa en «Carpeta de destino».",
        "choose_game": "Elige un juego...",
        "st_no_game": "Primero elige un juego de la lista de arriba",
        "drop_no_game": "Primero elige un juego para instalar mods",
        "drop_no_game_sub": "Pulsa «Buscar» o abre la lista de juegos de arriba",
        "msg_choose_game": "Primero elige un juego de la lista: así sabré dónde instalar los mods.",
        "find_game": "Buscar", "search_title": "Buscar juego", "search_hint": "Empieza a escribir el nombre del juego",
        "search_empty": "Sin resultados", "badge_installed": "Instalado", "badge_notfound": "No encontrado",
        "appearance": "Apariencia", "theme_dark": "Oscuro (grafito)", "theme_light": "Claro",
        "theme_oled": "OLED (negro)", "theme_cyberpunk": "Cyberpunk", "theme_dracula": "Dracula",
        "theme_nord": "Nord", "theme_outlaw": "Outlaw (western)", "pick_archives": "Elegir archivos…",
    },
    "zh": {
        "subtitle": "将压缩包中的 Mod 安装到游戏的正确文件夹",
        "theme_to_light": "☀ 浅色", "theme_to_dark": "🌙 深色", "language": "语言",
        "game": "游戏", "add_game": "＋ 自定义游戏", "folder": "文件夹", "browse": "浏览…",
        "detect": "自动检测",
        "st_ok": "✔ 已找到游戏", "st_nodetect": "⚠ 此文件夹中未找到游戏文件",
        "st_nofolder": "✖ 文件夹不存在——请选择游戏文件夹",
        "drop_title": "⬇  将 Mod 压缩包或文件夹拖到此处",
        "drop_hover": "松开即可安装", "drop_nodnd": "点击选择压缩包",
        "drop_sub": "或点击浏览  ·  .zip   .7z   .rar",
        "ready": "准备安装", "installing": "正在安装 {i}/{n}：{name}",
        "done_status": "完成：已安装 {ok}/{n}",
        "log": "日志", "clear": "清空", "installed": "已安装的 Mod", "search": "搜索",
        "delete": "卸载", "refresh": "刷新", "open_dir": "打开文件夹",
        "hint_unmanaged": "* — 非本程序安装",
        "confirm_delete": "确定移除所选项目？\n\n{names}",
        "msg_busy": "正在安装中，请稍候。",
        "msg_folder": "请先选择有效的游戏文件夹。",
        "msg_nodetect": "此文件夹中未找到“{game}”的文件。仍要使用吗？",
        "dlg_archives": "选择 Mod 压缩包", "ft_archives": "压缩包", "ft_all": "所有文件",
        "dlg_folder": "游戏文件夹：{game}",
        "log_skip": "⚠ 已跳过（不是压缩包或文件夹）：{name}",
        "log_update": "↻ 该 Mod 已安装——已移除旧版本，正在重新安装",
        "log_placed": "✔ {dst} — 文件数：{n}",
        "log_loose": "⚠ 无法识别结构，已按类型放置文件",
        "log_fallback": "⚠ 未找到 Mod 特征，已原样复制 → {dst}",
        "log_autodetect_fail": "⚠ 自动检测未找到游戏——请手动选择文件夹",
        "err_fomod": "✖ 压缩包包含带选项的 FOMOD 安装器——请使用 Vortex / Mod Organizer 2 安装此类 Mod",
        "err_layout": "✖ 无法确定此压缩包的文件应放在哪里（请查看 Mod 说明）",
        "err_perm": "✖ 访问被拒绝——请以管理员身份运行程序",
        "err_empty": "✖ 压缩包为空",
        "err_no_rar": "✖ .rar 需要 7-Zip (7z.exe) 或 UnRAR：请从 7-zip.org 安装 7-Zip，或将 7z.exe 和 7z.dll 放在程序旁边",
        "err_extract": "✖ 解压失败：{err}",
        "err_format": "✖ 不支持 {ext} 格式",
        "log_done_ok": "✔ 完成：已安装 {ok}/{n}", "log_done_part": "⚠ 完成：已安装 {ok}/{n}",
        "log_deleted": "🗑 已移除：{name}", "log_delete_fail": "✖ 无法移除 {name}：{err}",
        "footer_rar_ok": "RAR：✔（{tool}）", "footer_rar_no": "RAR：✖ 需要 7-Zip 或 UnRAR",
        "footer_dnd_no": "拖放：✖（pip install tkinterdnd2）",
        "dlg_title": "添加自定义游戏", "dlg_name": "游戏名称", "dlg_mode": "安装方式",
        "mode_folder": "每个 Mod 是目标文件夹内的独立文件夹",
        "mode_root": "按压缩包结构复制内容",
        "dlg_target": "目标文件夹（位于游戏文件夹内，可留空）",
        "dlg_detect": "用于识别游戏的文件（如 game.exe）",
        "dlg_markers": "Mod 特征，用逗号分隔（如 manifest.json, .esp）",
        "dlg_save": "保存", "dlg_cancel": "取消", "dlg_need_name": "请输入游戏名称。",
        "note_rdr2": "需要已安装 Lenny's Mod Loader（游戏文件夹内的 lml 文件夹）。",
        "note_gta5": "需要 Script Hook V 和 ASI Loader。mods 文件夹（OpenIV）的 Mod 需要提前准备好 mods 副本。",
        "note_gtasa": "需要 ASI Loader、CLEO 和/或 Mod Loader——需单独安装。",
        "note_cp2077": "许多 Mod 需要 RED4ext、Cyber Engine Tweaks 和 ArchiveXL——需单独安装。",
        "note_bethesda": "请在 Vortex、MO2 或 Plugins.txt 中启用插件（.esp/.esm/.esl）。带 FOMOD 安装器的 Mod 建议用 Vortex 安装。",
        "note_witcher3": "脚本冲突请使用 Script Merger；部分 Mod 需要修改 bin/config 中的配置。",
        "note_stardew": "需要 SMAPI。", "note_bannerlord": "请在游戏启动器中启用该模块。",
        "note_rimworld": "请在游戏内的 Mods 菜单中启用 Mod。",
        "note_ue4": "部分 Mod 需要额外的加载器（如 UE4SS）。",
        "note_ets": "Mod（.scs）需在游戏内的 Mod 管理器中启用。",
        "note_sims4": "请在游戏选项 → 其他 中启用 Mod 和脚本 Mod。",
        "engine_template": "引擎模板", "engine_custom": "自定义",
        "dlg_loose": "按扩展名放置（如 .dll=BepInEx/plugins）",
        "dlg_invalid": "请检查字段：路径必须是相对路径，且不能包含“..”。",
        "conflict_title": "文件冲突",
        "conflict_header": "注意！此 Mod 将覆盖 Mod “{name}” 的以下文件：",
        "conflict_more": "……另有 {n} 个", "conflict_question": "是否继续安装？",
        "btn_overwrite": "是，覆盖", "btn_skip": "跳过 / 取消",
        "log_conflict_skip": "⚠ 因文件冲突已跳过安装",
        "note_bepinex": "需要已安装 BepInEx（解压到游戏文件夹）。",
        "note_melonloader": "需要已安装 MelonLoader。",
        "note_re_engine": "游戏文件夹中需要 REFramework（dinput8.dll）。",
        "note_unreal": "请选择 Paks 文件夹（或包含 ~mods 的文件夹）作为游戏文件夹，或在“目标文件夹”中填写完整路径。",
        "choose_game": "请选择游戏...",
        "st_no_game": "请先从上方列表中选择游戏",
        "drop_no_game": "请先选择要安装 Mod 的游戏",
        "drop_no_game_sub": "点击“查找”或打开上方的游戏列表",
        "msg_choose_game": "请先从列表中选择游戏——这样我才知道 Mod 该装到哪里。",
        "find_game": "查找", "search_title": "查找游戏", "search_hint": "开始输入游戏名称",
        "search_empty": "未找到结果", "badge_installed": "已安装", "badge_notfound": "未找到",
        "appearance": "外观", "theme_dark": "深色（石墨）", "theme_light": "浅色",
        "theme_oled": "OLED（纯黑）", "theme_cyberpunk": "Cyberpunk", "theme_dracula": "Dracula",
        "theme_nord": "Nord", "theme_outlaw": "Outlaw（西部）", "pick_archives": "选择压缩包…",
    },
}
for _lang, _extra in _STR_V32.items():
    STR.setdefault(_lang, {}).update(_extra)

_STR_V20: dict[str, dict[str, str]] = {
    "ru": {
        "btn_search": "Поиск", "settings": "Настройки", "layout_edit": "Макет", "layout_reset": "Сбросить макет",
        "layout_hint": "Режим редактирования макета: двигайте блоки стрелками ⬆ ⬇ ⬅ ➡ и скрывайте их тумблерами",
        "layout_blocks": "Блоки:", "blk_drop": "Зона загрузки", "blk_recent": "Свежие загрузки", "tools": "Инструменты",
        "doc_ok": "Загрузчики на месте", "doc_missing": "Не найдено: {items}", "doc_checking": "Проверка загрузчиков…",
        "doc_title": "Доктор зависимостей",
        "doc_detail_ok": "Все ожидаемые загрузчики найдены:\n{items}",
        "doc_detail_missing": "Не найдены:\n{items}\n\nУстановите их до установки модов, иначе моды могут не работать.",
        "vanilla_mode": "Ванильный режим", "vanilla_on": "Ванильный режим активен — моды отключены",
        "vanilla_confirm": "Отключить все моды этой игры и вернуть ванильные файлы?\nМоды можно вернуть одним нажатием.",
        "vanilla_busy": "Переключаю ванильный режим…",
        "log_vanilla_on": "⚠ Ванильный режим включён: файлов убрано в хранилище — {n}",
        "log_vanilla_off": "✔ Моды возвращены: файлов — {n}",
        "msg_vanilla_blocked": "Включён ванильный режим — моды сейчас отключены. Выключить его, чтобы продолжить?",
        "drop_vanilla": "Ванильный режим активен", "drop_vanilla_sub": "Выключите его, чтобы устанавливать моды",
        "err_vanilla_active": "✖ включён ванильный режим — сначала выключите его",
        "recent_empty": "В «Загрузках» пока нет архивов", "recent_scanning": "Сканирую…", "btn_install": "Установить",
        "set_title": "Настройки", "set_downloads": "Свежие загрузки",
        "set_downloads_d": "Панель с последними архивами из папки «Загрузки»",
        "set_doctor": "Доктор зависимостей", "set_doctor_d": "Проверка загрузчиков модов для выбранной игры",
        "set_vanilla": "Ванильный режим", "set_vanilla_d": "Кнопка, отключающая все моды в один клик",
        "set_autobackup": "Автобэкап сейвов", "set_autobackup_d": "Снимок сохранений перед каждой установкой",
        "set_limit": "Хранить снимков", "set_preview": "Предпросмотр файлов",
        "set_preview_d": "Дерево файлов перед установкой (или удерживайте Shift)",
        "set_conflicts": "Конфликты файлов", "set_conflicts_ask": "Спрашивать",
        "set_conflicts_warn": "Предупреждать в журнале", "set_close": "Готово",
        "cfg_title": "Настройки мода: {name}",
        "cfg_none": "У этого мода не найдено конфигурационных файлов (.ini .cfg .json .xml .toml)",
        "cfg_file": "Файл", "cfg_form": "Быстрые настройки", "cfg_form_empty": "Простых параметров нет — редактируйте текст",
        "cfg_save": "Сохранить", "cfg_saved": "✔ Сохранено: {name} (копия: .bak)",
        "cfg_invalid": "Ошибка синтаксиса: {err}\n\nВсё равно сохранить?", "cfg_btn": "Конфиги",
        "ctx_configs": "Настройки мода (Конфиги)",
        "saves_backup": "Бэкап сейвов", "saves_restore": "Восстановить сейвы",
        "saves_none": "Для этой игры не заданы папки сохранений. Их можно указать при добавлении своей игры.",
        "saves_done": "✔ Бэкап сейвов: {name} (файлов: {n})", "saves_empty": "⚠ Папки сохранений не найдены или пусты",
        "saves_restore_title": "Восстановление сейвов", "saves_restore_btn": "Восстановить выбранный",
        "saves_restore_confirm": "Восстановить сейвы из снимка «{name}»?\nТекущие сейвы будут сохранены отдельным снимком.",
        "saves_restored": "✔ Сейвы восстановлены из «{name}» (файлов: {n})", "saves_no_snaps": "Снимков пока нет",
        "dlg_saves": "Папка сохранений (необязательно)",
        "prev_title": "Предпросмотр установки", "prev_hint": "Снимите галочки с файлов, которые копировать не нужно",
        "prev_install": "Установить", "prev_count": "Выбрано: {n} из {total}",
        "prev_col_src": "Из архива", "prev_col_dest": "Куда ляжет",
        "log_preview_cancel": "⚠ установка отменена", "log_excluded": "ℹ исключено файлов: {n}",
        "pack_export": "Экспорт сборки", "pack_exported": "✔ Сборка сохранена: {path} (модов: {n})",
        "ft_packs": "Сборки модов", "log_pack_import": "↺ импорт сборки для «{game}»: модов — {n}",
        "err_pack_game": "✖ сборка создана для другой игры ({game})",
        "err_pack_bad": "✖ повреждённая или неподдерживаемая сборка",
        "err_pack_empty": "✖ нет установленных модов для экспорта",
        "log_conflict_warn": "⚠ файл «{file}» уже принадлежит моду «{name}»",
    },
    "en": {
        "btn_search": "Search", "settings": "Settings", "layout_edit": "Layout", "layout_reset": "Reset layout",
        "layout_hint": "Layout edit mode: move blocks with the arrows ⬆ ⬇ ⬅ ➡ and hide them with the switches",
        "layout_blocks": "Blocks:", "blk_drop": "Drop zone", "blk_recent": "Recent downloads", "tools": "Tools",
        "doc_ok": "Loaders found", "doc_missing": "Missing: {items}", "doc_checking": "Checking loaders…",
        "doc_title": "Dependency doctor",
        "doc_detail_ok": "All expected loaders were found:\n{items}",
        "doc_detail_missing": "Not found:\n{items}\n\nInstall them before installing mods, otherwise mods may not work.",
        "vanilla_mode": "Vanilla mode", "vanilla_on": "Vanilla mode is on — mods are disabled",
        "vanilla_confirm": "Disable all mods of this game and restore the vanilla files?\nYou can bring the mods back with one click.",
        "vanilla_busy": "Switching vanilla mode…",
        "log_vanilla_on": "⚠ Vanilla mode on: files moved to the stash — {n}",
        "log_vanilla_off": "✔ Mods restored: files — {n}",
        "msg_vanilla_blocked": "Vanilla mode is on — mods are currently disabled. Turn it off to continue?",
        "drop_vanilla": "Vanilla mode is on", "drop_vanilla_sub": "Turn it off to install mods",
        "err_vanilla_active": "✖ vanilla mode is on — turn it off first",
        "recent_empty": "No archives in Downloads yet", "recent_scanning": "Scanning…", "btn_install": "Install",
        "set_title": "Settings", "set_downloads": "Recent downloads",
        "set_downloads_d": "Panel with the latest archives from your Downloads folder",
        "set_doctor": "Dependency doctor", "set_doctor_d": "Checks mod loaders for the selected game",
        "set_vanilla": "Vanilla mode", "set_vanilla_d": "A button that disables all mods in one click",
        "set_autobackup": "Save auto-backup", "set_autobackup_d": "Snapshot your saves before every install",
        "set_limit": "Snapshots to keep", "set_preview": "File preview",
        "set_preview_d": "File tree before installing (or hold Shift)",
        "set_conflicts": "File conflicts", "set_conflicts_ask": "Ask me",
        "set_conflicts_warn": "Warn in the log", "set_close": "Done",
        "cfg_title": "Mod settings: {name}",
        "cfg_none": "No config files (.ini .cfg .json .xml .toml) found for this mod",
        "cfg_file": "File", "cfg_form": "Quick settings", "cfg_form_empty": "No simple parameters — edit the text",
        "cfg_save": "Save", "cfg_saved": "✔ Saved: {name} (backup: .bak)",
        "cfg_invalid": "Syntax error: {err}\n\nSave anyway?", "cfg_btn": "Configs",
        "ctx_configs": "Mod settings (Configs)",
        "saves_backup": "Back up saves", "saves_restore": "Restore saves",
        "saves_none": "No save folders are defined for this game. You can set one when adding a custom game.",
        "saves_done": "✔ Saves backed up: {name} (files: {n})", "saves_empty": "⚠ Save folders not found or empty",
        "saves_restore_title": "Restore saves", "saves_restore_btn": "Restore selected",
        "saves_restore_confirm": "Restore saves from snapshot “{name}”?\nYour current saves will be kept in a separate snapshot.",
        "saves_restored": "✔ Saves restored from “{name}” (files: {n})", "saves_no_snaps": "No snapshots yet",
        "dlg_saves": "Save folder (optional)",
        "prev_title": "Install preview", "prev_hint": "Uncheck files you do not want to copy",
        "prev_install": "Install", "prev_count": "Selected: {n} of {total}",
        "prev_col_src": "In archive", "prev_col_dest": "Destination",
        "log_preview_cancel": "⚠ installation cancelled", "log_excluded": "ℹ files excluded: {n}",
        "pack_export": "Export modpack", "pack_exported": "✔ Modpack saved: {path} (mods: {n})",
        "ft_packs": "Modpacks", "log_pack_import": "↺ importing modpack for “{game}”: mods — {n}",
        "err_pack_game": "✖ the modpack was made for another game ({game})",
        "err_pack_bad": "✖ damaged or unsupported modpack",
        "err_pack_empty": "✖ no installed mods to export",
        "log_conflict_warn": "⚠ file “{file}” already belongs to the mod “{name}”",
    },
    "uk": {
        "btn_search": "Пошук", "settings": "Налаштування", "layout_edit": "Макет", "layout_reset": "Скинути макет",
        "layout_hint": "Режим редагування макета: рухайте блоки стрілками ⬆ ⬇ ⬅ ➡ та ховайте їх перемикачами",
        "layout_blocks": "Блоки:", "blk_drop": "Зона завантаження", "blk_recent": "Свіжі завантаження", "tools": "Інструменти",
        "doc_ok": "Завантажувачі на місці", "doc_missing": "Не знайдено: {items}", "doc_checking": "Перевірка завантажувачів…",
        "doc_title": "Лікар залежностей",
        "doc_detail_ok": "Усі очікувані завантажувачі знайдено:\n{items}",
        "doc_detail_missing": "Не знайдено:\n{items}\n\nВстановіть їх до встановлення модів, інакше моди можуть не працювати.",
        "vanilla_mode": "Ванільний режим", "vanilla_on": "Ванільний режим активний — моди вимкнено",
        "vanilla_confirm": "Вимкнути всі моди цієї гри та повернути ванільні файли?\nМоди можна повернути одним натисканням.",
        "vanilla_busy": "Перемикаю ванільний режим…",
        "log_vanilla_on": "⚠ Ванільний режим увімкнено: файлів перенесено до сховища — {n}",
        "log_vanilla_off": "✔ Моди повернуто: файлів — {n}",
        "msg_vanilla_blocked": "Увімкнено ванільний режим — моди зараз вимкнені. Вимкнути його, щоб продовжити?",
        "drop_vanilla": "Ванільний режим активний", "drop_vanilla_sub": "Вимкніть його, щоб встановлювати моди",
        "err_vanilla_active": "✖ увімкнено ванільний режим — спершу вимкніть його",
        "recent_empty": "У «Завантаженнях» поки немає архівів", "recent_scanning": "Сканую…", "btn_install": "Встановити",
        "set_title": "Налаштування", "set_downloads": "Свіжі завантаження",
        "set_downloads_d": "Панель з останніми архівами з папки «Завантаження»",
        "set_doctor": "Лікар залежностей", "set_doctor_d": "Перевірка завантажувачів модів для вибраної гри",
        "set_vanilla": "Ванільний режим", "set_vanilla_d": "Кнопка, що вимикає всі моди одним кліком",
        "set_autobackup": "Автобекап сейвів", "set_autobackup_d": "Знімок збережень перед кожним встановленням",
        "set_limit": "Зберігати знімків", "set_preview": "Попередній перегляд файлів",
        "set_preview_d": "Дерево файлів перед встановленням (або утримуйте Shift)",
        "set_conflicts": "Конфлікти файлів", "set_conflicts_ask": "Питати",
        "set_conflicts_warn": "Попереджати в журналі", "set_close": "Готово",
        "cfg_title": "Налаштування моду: {name}",
        "cfg_none": "Для цього моду не знайдено конфігураційних файлів (.ini .cfg .json .xml .toml)",
        "cfg_file": "Файл", "cfg_form": "Швидкі налаштування", "cfg_form_empty": "Простих параметрів немає — редагуйте текст",
        "cfg_save": "Зберегти", "cfg_saved": "✔ Збережено: {name} (копія: .bak)",
        "cfg_invalid": "Помилка синтаксису: {err}\n\nВсе одно зберегти?", "cfg_btn": "Конфіги",
        "ctx_configs": "Налаштування моду (Конфіги)",
        "saves_backup": "Бекап сейвів", "saves_restore": "Відновити сейви",
        "saves_none": "Для цієї гри не задано папок збережень. Їх можна вказати під час додавання власної гри.",
        "saves_done": "✔ Бекап сейвів: {name} (файлів: {n})", "saves_empty": "⚠ Папки збережень не знайдено або порожні",
        "saves_restore_title": "Відновлення сейвів", "saves_restore_btn": "Відновити вибраний",
        "saves_restore_confirm": "Відновити сейви зі знімка «{name}»?\nПоточні сейви буде збережено окремим знімком.",
        "saves_restored": "✔ Сейви відновлено зі «{name}» (файлів: {n})", "saves_no_snaps": "Знімків поки немає",
        "dlg_saves": "Папка збережень (необов'язково)",
        "prev_title": "Попередній перегляд встановлення", "prev_hint": "Зніміть позначки з файлів, які копіювати не потрібно",
        "prev_install": "Встановити", "prev_count": "Вибрано: {n} з {total}",
        "prev_col_src": "З архіву", "prev_col_dest": "Куди потрапить",
        "log_preview_cancel": "⚠ встановлення скасовано", "log_excluded": "ℹ виключено файлів: {n}",
        "pack_export": "Експорт збірки", "pack_exported": "✔ Збірку збережено: {path} (модів: {n})",
        "ft_packs": "Збірки модів", "log_pack_import": "↺ імпорт збірки для «{game}»: модів — {n}",
        "err_pack_game": "✖ збірку створено для іншої гри ({game})",
        "err_pack_bad": "✖ пошкоджена або непідтримувана збірка",
        "err_pack_empty": "✖ немає встановлених модів для експорту",
        "log_conflict_warn": "⚠ файл «{file}» уже належить моду «{name}»",
    },
    "de": {
        "btn_search": "Suche", "settings": "Einstellungen", "layout_edit": "Layout", "layout_reset": "Layout zurücksetzen",
        "layout_hint": "Layout-Bearbeitung: Blöcke mit den Pfeilen ⬆ ⬇ ⬅ ➡ verschieben und mit den Schaltern ausblenden",
        "layout_blocks": "Blöcke:", "blk_drop": "Ablagebereich", "blk_recent": "Neue Downloads", "tools": "Werkzeuge",
        "doc_ok": "Loader gefunden", "doc_missing": "Fehlt: {items}", "doc_checking": "Prüfe Loader…",
        "doc_title": "Abhängigkeits-Doktor",
        "doc_detail_ok": "Alle erwarteten Loader wurden gefunden:\n{items}",
        "doc_detail_missing": "Nicht gefunden:\n{items}\n\nBitte vor der Mod-Installation installieren, sonst funktionieren Mods eventuell nicht.",
        "vanilla_mode": "Vanilla-Modus", "vanilla_on": "Vanilla-Modus aktiv – Mods sind deaktiviert",
        "vanilla_confirm": "Alle Mods dieses Spiels deaktivieren und Originaldateien wiederherstellen?\nMit einem Klick lassen sich die Mods zurückholen.",
        "vanilla_busy": "Wechsle Vanilla-Modus…",
        "log_vanilla_on": "⚠ Vanilla-Modus an: Dateien ins Depot verschoben – {n}",
        "log_vanilla_off": "✔ Mods wiederhergestellt: Dateien – {n}",
        "msg_vanilla_blocked": "Der Vanilla-Modus ist aktiv – Mods sind gerade deaktiviert. Ausschalten und fortfahren?",
        "drop_vanilla": "Vanilla-Modus aktiv", "drop_vanilla_sub": "Zum Installieren von Mods ausschalten",
        "err_vanilla_active": "✖ Vanilla-Modus ist aktiv – bitte zuerst ausschalten",
        "recent_empty": "Noch keine Archive in „Downloads“", "recent_scanning": "Suche…", "btn_install": "Installieren",
        "set_title": "Einstellungen", "set_downloads": "Neue Downloads",
        "set_downloads_d": "Leiste mit den neuesten Archiven aus dem Downloads-Ordner",
        "set_doctor": "Abhängigkeits-Doktor", "set_doctor_d": "Prüft Mod-Loader für das gewählte Spiel",
        "set_vanilla": "Vanilla-Modus", "set_vanilla_d": "Schaltfläche, die alle Mods mit einem Klick deaktiviert",
        "set_autobackup": "Spielstand-Auto-Backup", "set_autobackup_d": "Spielstände vor jeder Installation sichern",
        "set_limit": "Snapshots behalten", "set_preview": "Dateivorschau",
        "set_preview_d": "Dateibaum vor der Installation (oder Shift halten)",
        "set_conflicts": "Dateikonflikte", "set_conflicts_ask": "Nachfragen",
        "set_conflicts_warn": "Im Protokoll warnen", "set_close": "Fertig",
        "cfg_title": "Mod-Einstellungen: {name}",
        "cfg_none": "Für diesen Mod wurden keine Konfigurationsdateien (.ini .cfg .json .xml .toml) gefunden",
        "cfg_file": "Datei", "cfg_form": "Schnelleinstellungen", "cfg_form_empty": "Keine einfachen Parameter – Text bearbeiten",
        "cfg_save": "Speichern", "cfg_saved": "✔ Gespeichert: {name} (Kopie: .bak)",
        "cfg_invalid": "Syntaxfehler: {err}\n\nTrotzdem speichern?", "cfg_btn": "Configs",
        "ctx_configs": "Mod-Einstellungen (Configs)",
        "saves_backup": "Spielstände sichern", "saves_restore": "Spielstände wiederherstellen",
        "saves_none": "Für dieses Spiel sind keine Speicherordner definiert. Sie lassen sich beim Hinzufügen eines eigenen Spiels angeben.",
        "saves_done": "✔ Spielstände gesichert: {name} (Dateien: {n})", "saves_empty": "⚠ Speicherordner nicht gefunden oder leer",
        "saves_restore_title": "Spielstände wiederherstellen", "saves_restore_btn": "Auswahl wiederherstellen",
        "saves_restore_confirm": "Spielstände aus Snapshot „{name}“ wiederherstellen?\nDie aktuellen Spielstände werden separat gesichert.",
        "saves_restored": "✔ Spielstände aus „{name}“ wiederhergestellt (Dateien: {n})", "saves_no_snaps": "Noch keine Snapshots",
        "dlg_saves": "Speicherordner (optional)",
        "prev_title": "Installationsvorschau", "prev_hint": "Dateien abwählen, die nicht kopiert werden sollen",
        "prev_install": "Installieren", "prev_count": "Ausgewählt: {n} von {total}",
        "prev_col_src": "Im Archiv", "prev_col_dest": "Ziel",
        "log_preview_cancel": "⚠ Installation abgebrochen", "log_excluded": "ℹ Dateien ausgeschlossen: {n}",
        "pack_export": "Modpack exportieren", "pack_exported": "✔ Modpack gespeichert: {path} (Mods: {n})",
        "ft_packs": "Modpacks", "log_pack_import": "↺ Import des Modpacks für „{game}“: Mods – {n}",
        "err_pack_game": "✖ Das Modpack wurde für ein anderes Spiel erstellt ({game})",
        "err_pack_bad": "✖ beschädigtes oder nicht unterstütztes Modpack",
        "err_pack_empty": "✖ keine installierten Mods zum Exportieren",
        "log_conflict_warn": "⚠ Datei „{file}“ gehört bereits zum Mod „{name}“",
    },
    "fr": {
        "btn_search": "Recherche", "settings": "Paramètres", "layout_edit": "Disposition", "layout_reset": "Réinitialiser la disposition",
        "layout_hint": "Édition de la disposition : déplacez les blocs avec les flèches ⬆ ⬇ ⬅ ➡ et masquez-les avec les interrupteurs",
        "layout_blocks": "Blocs :", "blk_drop": "Zone de dépôt", "blk_recent": "Téléchargements récents", "tools": "Outils",
        "doc_ok": "Chargeurs présents", "doc_missing": "Manquant : {items}", "doc_checking": "Vérification des chargeurs…",
        "doc_title": "Docteur des dépendances",
        "doc_detail_ok": "Tous les chargeurs attendus ont été trouvés :\n{items}",
        "doc_detail_missing": "Introuvables :\n{items}\n\nInstallez-les avant les mods, sinon les mods risquent de ne pas fonctionner.",
        "vanilla_mode": "Mode vanilla", "vanilla_on": "Mode vanilla actif — mods désactivés",
        "vanilla_confirm": "Désactiver tous les mods de ce jeu et restaurer les fichiers d'origine ?\nVous pourrez réactiver les mods en un clic.",
        "vanilla_busy": "Changement de mode vanilla…",
        "log_vanilla_on": "⚠ Mode vanilla activé : fichiers mis en réserve — {n}",
        "log_vanilla_off": "✔ Mods restaurés : fichiers — {n}",
        "msg_vanilla_blocked": "Le mode vanilla est actif — les mods sont désactivés. Le désactiver pour continuer ?",
        "drop_vanilla": "Mode vanilla actif", "drop_vanilla_sub": "Désactivez-le pour installer des mods",
        "err_vanilla_active": "✖ le mode vanilla est actif — désactivez-le d'abord",
        "recent_empty": "Aucune archive dans Téléchargements", "recent_scanning": "Analyse…", "btn_install": "Installer",
        "set_title": "Paramètres", "set_downloads": "Téléchargements récents",
        "set_downloads_d": "Panneau des dernières archives du dossier Téléchargements",
        "set_doctor": "Docteur des dépendances", "set_doctor_d": "Vérifie les chargeurs de mods du jeu choisi",
        "set_vanilla": "Mode vanilla", "set_vanilla_d": "Bouton qui désactive tous les mods en un clic",
        "set_autobackup": "Sauvegarde auto des parties", "set_autobackup_d": "Copie des sauvegardes avant chaque installation",
        "set_limit": "Copies à conserver", "set_preview": "Aperçu des fichiers",
        "set_preview_d": "Arborescence avant l'installation (ou maintenez Maj)",
        "set_conflicts": "Conflits de fichiers", "set_conflicts_ask": "Demander",
        "set_conflicts_warn": "Avertir dans le journal", "set_close": "Terminé",
        "cfg_title": "Paramètres du mod : {name}",
        "cfg_none": "Aucun fichier de configuration (.ini .cfg .json .xml .toml) trouvé pour ce mod",
        "cfg_file": "Fichier", "cfg_form": "Réglages rapides", "cfg_form_empty": "Aucun paramètre simple — modifiez le texte",
        "cfg_save": "Enregistrer", "cfg_saved": "✔ Enregistré : {name} (copie : .bak)",
        "cfg_invalid": "Erreur de syntaxe : {err}\n\nEnregistrer quand même ?", "cfg_btn": "Configs",
        "ctx_configs": "Paramètres du mod (Configs)",
        "saves_backup": "Sauvegarder les parties", "saves_restore": "Restaurer les parties",
        "saves_none": "Aucun dossier de sauvegarde défini pour ce jeu. Vous pouvez l'indiquer en ajoutant un jeu personnalisé.",
        "saves_done": "✔ Parties sauvegardées : {name} (fichiers : {n})", "saves_empty": "⚠ Dossiers de sauvegarde introuvables ou vides",
        "saves_restore_title": "Restaurer les parties", "saves_restore_btn": "Restaurer la sélection",
        "saves_restore_confirm": "Restaurer les parties depuis la copie « {name} » ?\nVos parties actuelles seront conservées dans une copie séparée.",
        "saves_restored": "✔ Parties restaurées depuis « {name} » (fichiers : {n})", "saves_no_snaps": "Aucune copie pour l'instant",
        "dlg_saves": "Dossier de sauvegarde (facultatif)",
        "prev_title": "Aperçu de l'installation", "prev_hint": "Décochez les fichiers à ne pas copier",
        "prev_install": "Installer", "prev_count": "Sélectionnés : {n} sur {total}",
        "prev_col_src": "Dans l'archive", "prev_col_dest": "Destination",
        "log_preview_cancel": "⚠ installation annulée", "log_excluded": "ℹ fichiers exclus : {n}",
        "pack_export": "Exporter le pack", "pack_exported": "✔ Pack enregistré : {path} (mods : {n})",
        "ft_packs": "Packs de mods", "log_pack_import": "↺ import du pack pour « {game} » : mods — {n}",
        "err_pack_game": "✖ ce pack a été créé pour un autre jeu ({game})",
        "err_pack_bad": "✖ pack endommagé ou non pris en charge",
        "err_pack_empty": "✖ aucun mod installé à exporter",
        "log_conflict_warn": "⚠ le fichier « {file} » appartient déjà au mod « {name} »",
    },
    "es": {
        "btn_search": "Buscar", "settings": "Ajustes", "layout_edit": "Diseño", "layout_reset": "Restablecer diseño",
        "layout_hint": "Edición del diseño: mueve los bloques con las flechas ⬆ ⬇ ⬅ ➡ y ocúltalos con los interruptores",
        "layout_blocks": "Bloques:", "blk_drop": "Zona de carga", "blk_recent": "Descargas recientes", "tools": "Herramientas",
        "doc_ok": "Cargadores encontrados", "doc_missing": "Falta: {items}", "doc_checking": "Comprobando cargadores…",
        "doc_title": "Doctor de dependencias",
        "doc_detail_ok": "Se encontraron todos los cargadores esperados:\n{items}",
        "doc_detail_missing": "No encontrados:\n{items}\n\nInstálalos antes de instalar mods; si no, los mods podrían no funcionar.",
        "vanilla_mode": "Modo vanilla", "vanilla_on": "Modo vanilla activo — mods desactivados",
        "vanilla_confirm": "¿Desactivar todos los mods de este juego y restaurar los archivos originales?\nPuedes recuperar los mods con un clic.",
        "vanilla_busy": "Cambiando el modo vanilla…",
        "log_vanilla_on": "⚠ Modo vanilla activado: archivos guardados aparte — {n}",
        "log_vanilla_off": "✔ Mods restaurados: archivos — {n}",
        "msg_vanilla_blocked": "El modo vanilla está activo: los mods están desactivados. ¿Desactivarlo para continuar?",
        "drop_vanilla": "Modo vanilla activo", "drop_vanilla_sub": "Desactívalo para instalar mods",
        "err_vanilla_active": "✖ el modo vanilla está activo: desactívalo primero",
        "recent_empty": "Aún no hay archivos en Descargas", "recent_scanning": "Analizando…", "btn_install": "Instalar",
        "set_title": "Ajustes", "set_downloads": "Descargas recientes",
        "set_downloads_d": "Panel con los últimos archivos de la carpeta Descargas",
        "set_doctor": "Doctor de dependencias", "set_doctor_d": "Comprueba los cargadores de mods del juego elegido",
        "set_vanilla": "Modo vanilla", "set_vanilla_d": "Botón que desactiva todos los mods con un clic",
        "set_autobackup": "Copia automática de partidas", "set_autobackup_d": "Copia de tus partidas antes de cada instalación",
        "set_limit": "Copias a conservar", "set_preview": "Vista previa de archivos",
        "set_preview_d": "Árbol de archivos antes de instalar (o mantén Mayús)",
        "set_conflicts": "Conflictos de archivos", "set_conflicts_ask": "Preguntar",
        "set_conflicts_warn": "Avisar en el registro", "set_close": "Listo",
        "cfg_title": "Ajustes del mod: {name}",
        "cfg_none": "No se encontraron archivos de configuración (.ini .cfg .json .xml .toml) para este mod",
        "cfg_file": "Archivo", "cfg_form": "Ajustes rápidos", "cfg_form_empty": "No hay parámetros simples: edita el texto",
        "cfg_save": "Guardar", "cfg_saved": "✔ Guardado: {name} (copia: .bak)",
        "cfg_invalid": "Error de sintaxis: {err}\n\n¿Guardar de todos modos?", "cfg_btn": "Configs",
        "ctx_configs": "Ajustes del mod (Configs)",
        "saves_backup": "Copia de partidas", "saves_restore": "Restaurar partidas",
        "saves_none": "No hay carpetas de partidas definidas para este juego. Puedes indicarla al añadir un juego propio.",
        "saves_done": "✔ Copia de partidas: {name} (archivos: {n})", "saves_empty": "⚠ Carpetas de partidas no encontradas o vacías",
        "saves_restore_title": "Restaurar partidas", "saves_restore_btn": "Restaurar la selección",
        "saves_restore_confirm": "¿Restaurar las partidas de la copia «{name}»?\nTus partidas actuales se guardarán en una copia aparte.",
        "saves_restored": "✔ Partidas restauradas desde «{name}» (archivos: {n})", "saves_no_snaps": "Aún no hay copias",
        "dlg_saves": "Carpeta de partidas (opcional)",
        "prev_title": "Vista previa de la instalación", "prev_hint": "Desmarca los archivos que no quieras copiar",
        "prev_install": "Instalar", "prev_count": "Seleccionados: {n} de {total}",
        "prev_col_src": "En el archivo", "prev_col_dest": "Destino",
        "log_preview_cancel": "⚠ instalación cancelada", "log_excluded": "ℹ archivos excluidos: {n}",
        "pack_export": "Exportar pack", "pack_exported": "✔ Pack guardado: {path} (mods: {n})",
        "ft_packs": "Packs de mods", "log_pack_import": "↺ importando el pack de «{game}»: mods — {n}",
        "err_pack_game": "✖ el pack se creó para otro juego ({game})",
        "err_pack_bad": "✖ pack dañado o no compatible",
        "err_pack_empty": "✖ no hay mods instalados para exportar",
        "log_conflict_warn": "⚠ el archivo «{file}» ya pertenece al mod «{name}»",
    },
    "zh": {
        "btn_search": "搜索", "settings": "设置", "layout_edit": "布局", "layout_reset": "重置布局",
        "layout_hint": "布局编辑模式：用箭头 ⬆ ⬇ ⬅ ➡ 移动区块，用开关隐藏区块",
        "layout_blocks": "区块：", "blk_drop": "拖放区", "blk_recent": "最近下载", "tools": "工具",
        "doc_ok": "加载器齐全", "doc_missing": "缺少：{items}", "doc_checking": "正在检查加载器…",
        "doc_title": "依赖检查",
        "doc_detail_ok": "已找到所有所需的加载器：\n{items}",
        "doc_detail_missing": "未找到：\n{items}\n\n请在安装 Mod 之前先安装它们，否则 Mod 可能无法工作。",
        "vanilla_mode": "原版模式", "vanilla_on": "原版模式已开启——Mod 已停用",
        "vanilla_confirm": "停用此游戏的所有 Mod 并恢复原版文件？\n一键即可重新启用 Mod。",
        "vanilla_busy": "正在切换原版模式…",
        "log_vanilla_on": "⚠ 原版模式已开启：已移入暂存区的文件数 — {n}",
        "log_vanilla_off": "✔ Mod 已恢复：文件数 — {n}",
        "msg_vanilla_blocked": "原版模式已开启——Mod 当前已停用。是否关闭以继续？",
        "drop_vanilla": "原版模式已开启", "drop_vanilla_sub": "请先关闭它再安装 Mod",
        "err_vanilla_active": "✖ 原版模式已开启——请先关闭",
        "recent_empty": "“下载”文件夹中暂无压缩包", "recent_scanning": "正在扫描…", "btn_install": "安装",
        "set_title": "设置", "set_downloads": "最近下载",
        "set_downloads_d": "显示“下载”文件夹中最新压缩包的面板",
        "set_doctor": "依赖检查", "set_doctor_d": "检查所选游戏的 Mod 加载器",
        "set_vanilla": "原版模式", "set_vanilla_d": "一键停用所有 Mod 的按钮",
        "set_autobackup": "存档自动备份", "set_autobackup_d": "每次安装前备份存档",
        "set_limit": "保留快照数", "set_preview": "文件预览",
        "set_preview_d": "安装前显示文件树（或按住 Shift）",
        "set_conflicts": "文件冲突", "set_conflicts_ask": "询问我",
        "set_conflicts_warn": "仅在日志中警告", "set_close": "完成",
        "cfg_title": "Mod 设置：{name}",
        "cfg_none": "未找到此 Mod 的配置文件（.ini .cfg .json .xml .toml）",
        "cfg_file": "文件", "cfg_form": "快速设置", "cfg_form_empty": "没有简单参数——请直接编辑文本",
        "cfg_save": "保存", "cfg_saved": "✔ 已保存：{name}（备份：.bak）",
        "cfg_invalid": "语法错误：{err}\n\n仍要保存吗？", "cfg_btn": "配置",
        "ctx_configs": "Mod 设置（配置文件）",
        "saves_backup": "备份存档", "saves_restore": "恢复存档",
        "saves_none": "此游戏未设置存档文件夹。添加自定义游戏时可以指定。",
        "saves_done": "✔ 存档已备份：{name}（文件数：{n}）", "saves_empty": "⚠ 未找到存档文件夹或为空",
        "saves_restore_title": "恢复存档", "saves_restore_btn": "恢复所选",
        "saves_restore_confirm": "从快照“{name}”恢复存档？\n当前存档会另存为一个快照。",
        "saves_restored": "✔ 已从“{name}”恢复存档（文件数：{n}）", "saves_no_snaps": "暂无快照",
        "dlg_saves": "存档文件夹（可选）",
        "prev_title": "安装预览", "prev_hint": "取消勾选不需要复制的文件",
        "prev_install": "安装", "prev_count": "已选：{n}/{total}",
        "prev_col_src": "压缩包内", "prev_col_dest": "目标位置",
        "log_preview_cancel": "⚠ 安装已取消", "log_excluded": "ℹ 已排除文件数：{n}",
        "pack_export": "导出整合包", "pack_exported": "✔ 整合包已保存：{path}（Mod 数：{n}）",
        "ft_packs": "Mod 整合包", "log_pack_import": "↺ 正在导入“{game}”的整合包：Mod 数 — {n}",
        "err_pack_game": "✖ 此整合包是为其他游戏创建的（{game}）",
        "err_pack_bad": "✖ 整合包已损坏或不受支持",
        "err_pack_empty": "✖ 没有可导出的已安装 Mod",
        "log_conflict_warn": "⚠ 文件“{file}”已属于 Mod“{name}”",
    },
}
for _lang, _extra in _STR_V20.items():
    STR.setdefault(_lang, {}).update(_extra)

_STR_V21: dict[str, dict[str, str]] = {
    "ru": {"set_log": "Показывать журнал", "set_log_d": "Окно с журналом действий и ошибок",
           "layout_arrange": "Расположение:", "layout_side": "Рядом", "layout_stack": "Друг над другом",
           "layout_swap": "Поменять местами",
           "vanilla_nothing": "Нет модов, установленных через программу, — отключать нечего",
           "vanilla_need_folder": "Сначала выберите игру и укажите её папку",
           "vanilla_error": "Не удалось переключить ванильный режим: {err}",
           "log_vanilla_on": "⚠ Ванильный режим включён — отключено модов: {n}",
           "log_vanilla_off": "✔ Моды возвращены: {n}"},
    "en": {"set_log": "Show log", "set_log_d": "Window with the log of actions and errors",
           "layout_arrange": "Arrangement:", "layout_side": "Side by side", "layout_stack": "Stacked",
           "layout_swap": "Swap panels",
           "vanilla_nothing": "No mods installed through the program — nothing to disable",
           "vanilla_need_folder": "Choose a game and set its folder first",
           "vanilla_error": "Could not switch vanilla mode: {err}",
           "log_vanilla_on": "⚠ Vanilla mode on — mods disabled: {n}",
           "log_vanilla_off": "✔ Mods restored: {n}"},
    "uk": {"set_log": "Показувати журнал", "set_log_d": "Вікно з журналом дій і помилок",
           "layout_arrange": "Розташування:", "layout_side": "Поруч", "layout_stack": "Одне над одним",
           "layout_swap": "Поміняти місцями",
           "vanilla_nothing": "Немає модів, встановлених через програму, — вимикати нічого",
           "vanilla_need_folder": "Спершу оберіть гру та вкажіть її папку",
           "vanilla_error": "Не вдалося перемкнути ванільний режим: {err}",
           "log_vanilla_on": "⚠ Ванільний режим увімкнено — вимкнено модів: {n}",
           "log_vanilla_off": "✔ Моди повернуто: {n}"},
    "de": {"set_log": "Protokoll anzeigen", "set_log_d": "Fenster mit dem Protokoll der Aktionen und Fehler",
           "layout_arrange": "Anordnung:", "layout_side": "Nebeneinander", "layout_stack": "Übereinander",
           "layout_swap": "Tauschen",
           "vanilla_nothing": "Keine über das Programm installierten Mods – nichts zu deaktivieren",
           "vanilla_need_folder": "Zuerst ein Spiel wählen und den Ordner angeben",
           "vanilla_error": "Vanilla-Modus konnte nicht umgeschaltet werden: {err}",
           "log_vanilla_on": "⚠ Vanilla-Modus an – Mods deaktiviert: {n}",
           "log_vanilla_off": "✔ Mods wiederhergestellt: {n}"},
    "fr": {"set_log": "Afficher le journal", "set_log_d": "Fenêtre du journal des actions et des erreurs",
           "layout_arrange": "Disposition :", "layout_side": "Côte à côte", "layout_stack": "Superposés",
           "layout_swap": "Inverser",
           "vanilla_nothing": "Aucun mod installé via le programme — rien à désactiver",
           "vanilla_need_folder": "Choisissez d'abord un jeu et indiquez son dossier",
           "vanilla_error": "Impossible de basculer le mode vanilla : {err}",
           "log_vanilla_on": "⚠ Mode vanilla activé — mods désactivés : {n}",
           "log_vanilla_off": "✔ Mods restaurés : {n}"},
    "es": {"set_log": "Mostrar registro", "set_log_d": "Ventana con el registro de acciones y errores",
           "layout_arrange": "Disposición:", "layout_side": "Lado a lado", "layout_stack": "Apilados",
           "layout_swap": "Intercambiar",
           "vanilla_nothing": "No hay mods instalados con el programa: nada que desactivar",
           "vanilla_need_folder": "Primero elige un juego e indica su carpeta",
           "vanilla_error": "No se pudo cambiar el modo vanilla: {err}",
           "log_vanilla_on": "⚠ Modo vanilla activado — mods desactivados: {n}",
           "log_vanilla_off": "✔ Mods restaurados: {n}"},
    "zh": {"set_log": "显示日志", "set_log_d": "显示操作和错误日志的窗口",
           "layout_arrange": "布局：", "layout_side": "并排", "layout_stack": "上下",
           "layout_swap": "交换位置",
           "vanilla_nothing": "没有通过本程序安装的 Mod——无需停用",
           "vanilla_need_folder": "请先选择游戏并指定其文件夹",
           "vanilla_error": "无法切换原版模式：{err}",
           "log_vanilla_on": "⚠ 原版模式已开启——已停用 Mod：{n}",
           "log_vanilla_off": "✔ Mod 已恢复：{n}"},
}
for _lang, _extra in _STR_V21.items():
    STR.setdefault(_lang, {}).update(_extra)





def load_external_languages() -> None:
    """Подхватывает пользовательские переводы: <код>.json с ключом _name."""
    try:
        for f in LANG_DIR.glob("*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            code = f.stem
            LANG_NAMES[code] = data.pop("_name", code)
            STR.setdefault(code, {}).update({k: str(v) for k, v in data.items()})
    except Exception:
        pass


class I18N:
    lang = "en"


def T(key: str, **kw) -> str:
    s = STR.get(I18N.lang, {}).get(key) or STR["en"].get(key) or STR["ru"].get(key) or key
    if kw:
        try:
            return s.format(**kw)
        except Exception:
            return s
    return s


def default_language() -> str:
    try:
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        return {0x19: "ru", 0x22: "uk", 0x07: "de", 0x0C: "fr", 0x0A: "es", 0x04: "zh"}.get(lid, "en")
    except Exception:
        return "en"


class ModError(Exception):
    def __init__(self, key: str, **kw):
        super().__init__(key)
        self.key, self.kw = key, kw


# ======================================================================= config ==
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_config() -> dict:
    cfg = load_json(CONFIG_FILE, None)
    if cfg is None:
        cfg = {}
        old = load_json(OLD_CONFIG_FILE, {})  # миграция со старой версии
        if old.get("game_dir"):
            cfg["games"] = {"rdr2": {"path": old["game_dir"]}}
            cfg["last_game"] = "rdr2"
        if old.get("theme") in THEMES:
            cfg["theme"] = old["theme"]
    return cfg


# ====================================================================== поиск игр ==
@lru_cache(maxsize=1)
def store_roots() -> list[Path]:
    libs: set[Path] = set()
    roots: set[Path] = set()
    try:
        import winreg
        for hive, key, val in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.add(Path(winreg.QueryValueEx(k, val)[0]))
            except OSError:
                pass
    except ImportError:
        pass
    for r in roots:
        libs.add(r)
        try:
            txt = (r / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
                libs.add(Path(m.group(1).replace("\\\\", "\\")))
        except OSError:
            pass
    res = [l / "steamapps" / "common" for l in libs]
    for letter in "CDEFGH":
        for sub in (r"SteamLibrary", r"Steam", r"Program Files (x86)\Steam", r"Games\Steam"):
            res.append(Path(f"{letter}:\\{sub}") / "steamapps" / "common")
        for sub in (r"Program Files\Epic Games", r"Epic Games", r"GOG Games",
                    r"Program Files (x86)\GOG Galaxy\Games", r"Program Files\Rockstar Games", r"Games"):
            res.append(Path(f"{letter}:\\{sub}"))
    seen, uniq = set(), []
    for p in res:
        if str(p).lower() not in seen:
            seen.add(str(p).lower())
            uniq.append(p)
    return uniq


def is_game_dir(profile: dict, path: Path) -> bool:
    if not path.is_dir():
        return False
    detect = profile.get("detect") or []
    return not detect or any((path / rel).exists() for rel in detect)


def find_game_dir(profile: dict) -> str:
    for raw in profile.get("paths", []):
        p = Path(os.path.expandvars(raw))
        if is_game_dir(profile, p):
            return str(p)
    for root in store_roots():
        for f in profile.get("folders", []):
            p = root / f
            if is_game_dir(profile, p):
                return str(p)
    return ""


# ================================================================== распаковка ==
def _app_dirs() -> list[Path]:
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            dirs.append(Path(sys._MEIPASS))
        dirs.append(Path(sys.executable).parent)
    else:
        dirs.append(Path(__file__).resolve().parent)
    return dirs


def _program_files() -> list[Path]:
    return [Path(v) for e in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432") if (v := os.environ.get(e))]


def find_7z() -> str | None:
    cands = [d / "7z.exe" for d in _app_dirs()] + [pf / "7-Zip" / "7z.exe" for pf in _program_files()]
    for c in cands:
        if c.exists():
            return str(c)
    return shutil.which("7z") or shutil.which("7za")


def find_unrar() -> str | None:
    cands = [d / "UnRAR.exe" for d in _app_dirs()] + [pf / "WinRAR" / "UnRAR.exe" for pf in _program_files()]
    for c in cands:
        if c.exists():
            return str(c)
    return shutil.which("unrar")


def find_tar() -> str | None:
    root = os.environ.get("SystemRoot")
    if root and (Path(root) / "System32" / "tar.exe").exists():
        return str(Path(root) / "System32" / "tar.exe")  # bsdtar (libarchive), Windows 10+
    return shutil.which("bsdtar")


def _run_tool(cmd: list[str], ok_codes=(0,)) -> None:
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode not in ok_codes:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError("; ".join(t.strip() for t in tail) or f"exit code {r.returncode}")


def get_backends() -> list[tuple[str, callable]]:
    res = []
    if z := find_7z():
        res.append(("7-Zip", lambda p, d, z=z: _run_tool([z, "x", "-y", f"-o{d}", str(p)], (0, 1))))
    if u := find_unrar():
        res.append(("UnRAR", lambda p, d, u=u: _run_tool([u, "x", "-y", "-o+", str(p), str(d) + os.sep])))
    if t := find_tar():
        res.append(("tar", lambda p, d, t=t: _run_tool([t, "-xf", str(p), "-C", str(d)])))
    return res


def extract_external(path: Path, dest: Path) -> None:
    backends = get_backends()
    if not backends:
        raise ModError("err_no_rar")
    errors = []
    for name, fn in backends:
        try:
            fn(path, dest)
            return
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise ModError("err_extract", err=" | ".join(errors))


def extract_zip(path: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = info.filename
            if not (info.flag_bits & 0x800) and not name.isascii():  # cp437 -> cp866
                try:
                    name = name.encode("cp437").decode("cp866")
                except Exception:
                    pass
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest_resolved)):
                continue  # zip-slip
            if info.is_dir() or name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as s, open(target, "wb") as d:
                    shutil.copyfileobj(s, d)


def extract_archive(path: Path, dest: Path) -> None:
    ext = path.suffix.lower()
    try:
        if ext == ".zip":
            extract_zip(path, dest)
        elif ext == ".7z":
            py7zr = get_py7zr()
            if py7zr is not None:
                try:
                    with py7zr.SevenZipFile(path, "r") as z:
                        z.extractall(dest)
                    return
                except Exception:
                    pass
            extract_external(path, dest)
        elif ext == ".rar":
            extract_external(path, dest)
        else:
            raise ModError("err_format", ext=ext)
    except zipfile.BadZipFile as e:
        raise ModError("err_extract", err=str(e))


# ============================================================ определение раскладки ==
class Op:
    """Скопировать СОДЕРЖИМОЕ папки src в <игра>/dst."""
    def __init__(self, src: Path, dst: str, skip_top=(), skip_docs=False):
        self.src, self.dst, self.skip_top, self.skip_docs = src, dst, set(skip_top), skip_docs


class Plan:
    def __init__(self):
        self.ops: list[Op] = []
        self.loose: list[tuple[Path, str]] = []
        self.warns: list[tuple[str, dict]] = []
        self.excluded: set[str] = set()  # пути назначения (lower), снятые в предпросмотре


def _join(*parts: str) -> str:
    return "/".join(p.strip("/\\") for p in parts if p and p.strip("/\\"))


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return child != parent
    except ValueError:
        return False


def walk_dirs(root: Path, max_depth: int = 8):
    q = deque([(root, 0)])
    while q:
        d, depth = q.popleft()
        yield d
        if depth < max_depth:
            try:
                subs = sorted(c for c in d.iterdir() if c.is_dir() and c.name.lower() not in JUNK_NAMES)
            except OSError:
                continue
            q.extend((s, depth + 1) for s in subs)


def outermost(root: Path, pred) -> list[Path]:
    found: list[Path] = []
    for d in walk_dirs(root):
        if any(d == f or _is_inside(d, f) for f in found):
            continue
        if pred(d):
            found.append(d)
    return found


def has_marker(d: Path, markers: set[str]) -> bool:
    try:
        for c in d.iterdir():
            if c.name.lower() in markers or (c.is_file() and c.suffix.lower() in markers):
                return True
    except OSError:
        pass
    return False


def plan_folder(p: dict, root: Path, stem: str) -> Plan:
    target = p.get("target", "")
    wr = p.get("wrappers") or {}
    if isinstance(wr, list):
        wr = {w: w for w in wr}
    wl = {k.lower(): v for k, v in wr.items()}
    markers = {m.lower() for m in p.get("markers", [])}
    plan = Plan()

    for d in outermost(root, lambda d: d != root and d.name.lower() in wl):
        plan.ops.append(Op(d, wl[d.name.lower()]))
    if plan.ops:
        return plan

    if markers:
        for d in outermost(root, lambda d: has_marker(d, markers)):
            plan.ops.append(Op(d, _join(target, stem if d == root else d.name)))
        if plan.ops:
            return plan

    kids = [c for c in root.iterdir() if c.name.lower() not in JUNK_NAMES]
    dirs = [c for c in kids if c.is_dir()]
    files = [c for c in kids if c.is_file()]
    if len(dirs) == 1 and not files:
        dst = _join(target, dirs[0].name)
        plan.ops.append(Op(dirs[0], dst))
    else:
        dst = _join(target, stem)
        plan.ops.append(Op(root, dst))
    if markers:
        plan.warns.append(("log_fallback", {"dst": dst}))
    return plan


def plan_root(p: dict, root: Path, stem: str) -> Plan:
    target = p.get("target", "").strip("/\\")
    tparts = [x for x in target.replace("\\", "/").split("/") if x]
    markers = {m.lower() for m in p.get("markers", [])}
    rooted = {r.lower() for r in p.get("rooted", [])}
    if not rooted and len(tparts) == 1:
        rooted = {tparts[0].lower()}
    leaf = tparts[-1].lower() if len(tparts) > 1 else ""
    loose = {k.lower(): v for k, v in (p.get("loose") or {}).items()}
    plan = Plan()

    def classify(d: Path) -> Op | None:
        try:
            kids = {c.name.lower(): c for c in d.iterdir() if c.is_dir()}
        except OSError:
            return None
        if any(n in rooted for n in kids):  # архив уже «от корня игры»
            return Op(d, "", skip_top={"fomod"}, skip_docs=True)
        if leaf and leaf in kids:  # архив содержит папку назначения (например plugins/)
            return Op(kids[leaf], target)
        if has_marker(d, markers):
            return Op(d, target, skip_top={"fomod"}, skip_docs=True)
        return None

    content, op = None, None
    for d in walk_dirs(root):
        op = classify(d)
        if op:
            content = d
            break
    fomods = [d for d in walk_dirs(root) if d.name.lower() == "fomod"]
    if fomods and not all(f.parent == content for f in fomods):
        raise ModError("err_fomod")

    if op is not None:
        plan.ops.append(op)
        return plan

    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix.lower() in loose and not any(x.lower() in JUNK_NAMES for x in f.parts):
            plan.loose.append((f, loose[f.suffix.lower()]))
    if not plan.loose:
        raise ModError("err_layout")
    plan.warns.append(("log_loose", {}))
    return plan


def make_plan(profile: dict, root: Path, stem: str) -> Plan:
    return plan_folder(profile, root, stem) if profile["mode"] == "folder" else plan_root(profile, root, stem)


# =============================================================== установка / учёт ==
_db_lock = threading.Lock()


def db_records(gid: str) -> list[dict]:
    with _db_lock:
        return list(load_json(DB_FILE, {}).get(gid, []))


def db_add(gid: str, rec: dict) -> None:
    with _db_lock:
        db = load_json(DB_FILE, {})
        db.setdefault(gid, []).append(rec)
        save_json(DB_FILE, db)


def db_remove(gid: str, rid: str) -> None:
    with _db_lock:
        db = load_json(DB_FILE, {})
        db[gid] = [r for r in db.get(gid, []) if r["id"] != rid]
        save_json(DB_FILE, db)


def db_update(gid: str, rec: dict) -> None:
    with _db_lock:
        db = load_json(DB_FILE, {})
        db[gid] = [rec if r["id"] == rec["id"] else r for r in db.get(gid, [])]
        save_json(DB_FILE, db)


class Tracker:
    """Копирует файлы, запоминая созданные и делая резервные копии перезаписанных."""
    def __init__(self, game: Path, bdir: Path):
        self.game, self.bdir = game, bdir
        self.files: list[str] = []
        self.backups: list[str] = []
        self.seen: set[str] = set()

    def copy(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        rel = dest.relative_to(self.game).as_posix()
        if rel not in self.seen:
            if dest.exists():
                b = self.bdir / rel
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, b)
                self.backups.append(rel)
            self.files.append(rel)
            self.seen.add(rel)
        shutil.copy2(src, dest)


def _skip_entry(op: Op, rel: Path, path: Path) -> bool:
    low = [x.lower() for x in rel.parts]
    if any(x in JUNK_NAMES for x in low) or low[0] in op.skip_top:
        return True
    return op.skip_docs and len(low) == 1 and path.is_file() and path.suffix.lower() in DOC_EXTS


def iter_op_files(op: Op, exclude=frozenset()):
    """(исходный файл, путь относительно папки игры) для каждого файла, который скопирует op."""
    for path in sorted(op.src.rglob("*")):
        rel = path.relative_to(op.src)
        if path.is_file() and not _skip_entry(op, rel, path):
            dest = _join(op.dst, rel.as_posix())
            if dest.lower() not in exclude:
                yield path, dest


def copy_op(tr: Tracker, op: Op, exclude=frozenset()) -> int:
    dst_root = tr.game / op.dst if op.dst else tr.game
    dst_root.mkdir(parents=True, exist_ok=True)
    n = 0
    for path in sorted(op.src.rglob("*")):
        rel = path.relative_to(op.src)
        if _skip_entry(op, rel, path):
            continue
        dest = dst_root / rel
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        elif _join(op.dst, rel.as_posix()).lower() not in exclude:
            tr.copy(path, dest)
            n += 1
    return n


def plan_dest_files(plan: Plan) -> list[str]:
    out: list[str] = []
    for op in plan.ops:
        out += [rel for _, rel in iter_op_files(op, plan.excluded)]
    out += [d for d in (_join(dst, f.name) for f, dst in plan.loose) if d.lower() not in plan.excluded]
    return out


def plan_entries(plan: Plan, root: Path) -> list[tuple[str, str]]:
    """[(путь внутри архива, куда ляжет файл)] — для предпросмотра перед установкой."""
    def shown(p: Path) -> str:
        try:
            return p.relative_to(root).as_posix()
        except ValueError:
            return p.name
    out = [(shown(src), dest) for op in plan.ops for src, dest in iter_op_files(op)]
    out += [(shown(f), _join(dst, f.name)) for f, dst in plan.loose]
    return out


def find_conflicts(gid: str, game: Path, plan: Plan, exclude_id: str | None = None) -> list[tuple[str, list[str]]]:
    """Файлы плана, которые уже принадлежат ДРУГОМУ установленному моду: [(имя мода, [файлы])]."""
    key = os.path.normcase(str(game))
    owners: dict[str, dict] = {}
    for r in db_records(gid):  # порядок = порядок установки, последний владелец перекрывает
        if r["id"] == exclude_id or os.path.normcase(r.get("game", "")) != key:
            continue
        for rel in r.get("files", []):
            owners[rel.lower()] = r
    found: dict[str, tuple[str, list[str]]] = {}
    for rel in dict.fromkeys(plan_dest_files(plan)):
        r = owners.get(rel.lower())
        if r:
            found.setdefault(r["id"], (r["name"], []))[1].append(rel)
    return list(found.values())


def protected_dirs(profile: dict, game: Path) -> set[Path]:
    prot = {game}
    if profile.get("target"):
        prot.add(game / profile["target"])
    wr = profile.get("wrappers") or {}
    for v in (wr.values() if isinstance(wr, dict) else wr):
        prot.add(game / v)
    return prot


def prune_empty_dirs(game: Path, rels, protected: set[Path]) -> None:
    dirs = sorted({(game / rel).parent for rel in rels}, key=lambda p: len(p.parts), reverse=True)
    for d in dirs:
        cur = d
        while cur not in protected and game in cur.parents and len(cur.relative_to(game).parts) > 1:
            try:
                cur.rmdir()  # удалится только если пустая
            except OSError:
                break
            cur = cur.parent


def uninstall_record(profile: dict, game: Path, rec: dict) -> None:
    """Удаляет мод. Если файл потом перезаписал другой мод, бэкап «передаётся» ему по цепочке:
    vanilla -> A -> B: удаление A оставляет файл B, а B теперь восстановит vanilla."""
    gid = profile["id"]
    recs = _game_records(gid, game)
    ids = [r["id"] for r in recs]
    if rec["id"] in ids:
        rec = recs[ids.index(rec["id"])]  # свежая версия (бэкапы могли поменяться)
        later = recs[ids.index(rec["id"]) + 1:]
    else:
        later = []  # откат неудавшейся установки
    next_owner: dict[str, dict] = {}
    for r in later:
        for rel in r.get("files", []):
            next_owner.setdefault(rel, r)

    bdir = BACKUP_DIR / gid / rec["id"]
    backups = set(rec.get("backups", []))
    dirty: dict[str, dict] = {}
    for rel in rec["files"]:
        nxt = next_owner.get(rel)
        if nxt is not None:
            nb = BACKUP_DIR / gid / nxt["id"] / rel
            nbacks = nxt.setdefault("backups", [])
            try:
                if rel in backups and (bdir / rel).exists():
                    nb.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(bdir / rel, nb)  # следующий мод теперь вернёт оригинал
                    if rel not in nbacks:
                        nbacks.append(rel)
                else:  # оригинала не было: файл создал этот мод, дальше «создателем» становится следующий
                    if nb.exists():
                        nb.unlink()
                    if rel in nbacks:
                        nbacks.remove(rel)
            except OSError:
                pass
            dirty[nxt["id"]] = nxt
            continue
        f = game / rel
        try:
            if rel in backups and (bdir / rel).exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bdir / rel, f)
            elif f.exists():
                f.unlink()
        except OSError:
            pass
    for r in dirty.values():
        db_update(gid, r)
    prune_empty_dirs(game, rec["files"], protected_dirs(profile, game))
    shutil.rmtree(bdir, ignore_errors=True)


def _apply_plan(profile: dict, plan: Plan, game: Path, stem: str, archive_name: str, log,
                confirm=None, settings=None) -> bool:
    """Общая часть установки: конфликты, автобэкап сейвов, копирование с учётом, запись в БД."""
    gid, settings = profile["id"], settings or {}
    if vanilla_state(gid, game):
        raise ModError("err_vanilla_active")
    game_key = os.path.normcase(str(game))
    old = next((r for r in db_records(gid)
                if r.get("archive") == archive_name and os.path.normcase(r.get("game", "")) == game_key), None)

    conflicts = find_conflicts(gid, game, plan, exclude_id=old["id"] if old else None)
    if conflicts:
        if settings.get("conflict_mode") == "warn":  # без диалога: только предупреждение в журнале
            for name, files in conflicts:
                for f in files[:5]:
                    log("   " + T("log_conflict_warn", name=name, file=f))
        elif confirm is not None and not confirm(conflicts):
            log("   " + T("log_conflict_skip"))
            return False

    if settings.get("autobackup_saves"):
        try:
            res = backup_saves(profile, settings.get("save_backup_limit", 5))
        except Exception:
            res = None
        if res:
            log("   " + T("saves_done", name=res[0].name, n=res[1]))

    if old:
        uninstall_record(profile, game, old)
        db_remove(gid, old["id"])
        log("   " + T("log_update"))

    rec = {"id": uuid.uuid4().hex[:10], "name": stem, "archive": archive_name, "game": str(game),
           "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
    tr = Tracker(game, BACKUP_DIR / gid / rec["id"])
    try:
        for op in plan.ops:
            n = copy_op(tr, op, plan.excluded)
            log("   " + T("log_placed", dst=op.dst or "/", n=n))
        for f, dst in plan.loose:
            if _join(dst, f.name).lower() in plan.excluded:
                continue
            tr.copy(f, game / dst / f.name)
            log("   " + T("log_placed", dst=_join(dst, f.name), n=1))
    except Exception:
        rec.update(files=tr.files, backups=tr.backups)
        uninstall_record(profile, game, rec)  # откат частичной установки
        raise

    tparts = [x.lower() for x in profile.get("target", "").split("/") if x]
    dirs = set()
    for rel in tr.files:
        parts = rel.split("/")
        if profile["mode"] == "folder" and len(parts) > len(tparts) and \
                [x.lower() for x in parts[:len(tparts)]] == tparts:
            dirs.add(parts[len(tparts)])
    rec.update(files=tr.files, backups=tr.backups, dirs=sorted(dirs))
    db_add(gid, rec)
    for key, kw in plan.warns:
        log("   " + T(key, **kw))
    return True


def install_source(profile: dict, src: Path, game: Path, log, confirm=None, preview=None, settings=None) -> bool:
    """confirm(conflicts) -> bool при конфликте файлов; preview(entries) -> набор исключённых путей или None (отмена);
    settings — словарь настроек (conflict_mode, autobackup_saves, save_backup_limit)."""
    gid = profile["id"]
    if vanilla_state(gid, game):
        raise ModError("err_vanilla_active")
    if src.is_file() and is_gmpack(src):
        return import_gmpack(profile, src, game, log, confirm, settings)
    with tempfile.TemporaryDirectory(prefix="gmi_", ignore_cleanup_errors=True) as tmp:
        if src.is_dir():
            root, stem = src, src.name
        else:
            root, stem = Path(tmp), src.stem
            extract_archive(src, root)
        if not any(c for c in root.iterdir() if c.name.lower() not in JUNK_NAMES):
            raise ModError("err_empty")

        plan = make_plan(profile, root, stem)  # сначала план — вдруг ошибка, старую версию не трогаем
        if preview is not None:
            excluded = preview(plan_entries(plan, root))
            if excluded is None:
                log("   " + T("log_preview_cancel"))
                return False
            excluded = {x.lower() for x in excluded}
            if excluded:
                plan.excluded |= excluded
                log("   " + T("log_excluded", n=len(excluded)))
            if not plan_dest_files(plan):
                log("   " + T("log_preview_cancel"))
                return False
        return _apply_plan(profile, plan, game, stem, src.name, log, confirm, settings)


# ============================================================ v2.0: логика без GUI ==
def _vanilla_file() -> Path:
    return CONFIG_DIR / "vanilla.json"


def _stash_root() -> Path:
    return CONFIG_DIR / "vanilla_stash"


def _save_backups_root() -> Path:
    return CONFIG_DIR / "save_backups"


def game_hash(game: Path) -> str:
    return hashlib.sha1(os.path.normcase(str(game)).encode("utf-8")).hexdigest()[:12]


def _game_records(gid: str, game: Path) -> list[dict]:
    key = os.path.normcase(str(game))
    return [r for r in db_records(gid) if os.path.normcase(r.get("game", "")) == key]


def mix_color(c1: str, c2: str, t: float) -> str:
    """Смешивает цвета #rrggbb: t=0 -> c1, t=1 -> c2 (для полупрозрачных pill-бейджей)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


# ---- настройки
DEFAULT_SETTINGS = {
    "show_downloads": True, "show_doctor": True, "show_vanilla": True,
    "autobackup_saves": True, "save_backup_limit": 5,
    "preview_tree": False, "conflict_mode": "ask", "show_log": True,
}


def get_settings(cfg: dict) -> dict:
    s = dict(DEFAULT_SETTINGS)
    raw = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    for k, default in DEFAULT_SETTINGS.items():
        if k in raw and isinstance(raw[k], type(default)):
            s[k] = raw[k]
    s["save_backup_limit"] = max(1, min(50, int(s["save_backup_limit"])))
    if s["conflict_mode"] not in ("ask", "warn"):
        s["conflict_mode"] = "ask"
    return s


# ---- Ванильный режим
def vanilla_state(gid: str, game: Path) -> dict | None:
    return load_json(_vanilla_file(), {}).get(gid, {}).get(game_hash(game))


def _vanilla_set(gid: str, game: Path, state: dict | None) -> None:
    data = load_json(_vanilla_file(), {})
    games = data.setdefault(gid, {})
    if state is None:
        games.pop(game_hash(game), None)
    else:
        games[game_hash(game)] = state
    save_json(_vanilla_file(), data)


def vanilla_enable(profile: dict, game: Path) -> int:
    """Прячет все установленные через программу моды (root и folder): текущие файлы -> stash,
    на диск возвращаются ванильные из бэкапов. При ошибке всё откатывается. Возвращает число файлов."""
    gid = profile["id"]
    if vanilla_state(gid, game):
        raise ModError("err_vanilla_active")
    recs = _game_records(gid, game)
    if not recs:
        raise ModError("vanilla_nothing")
    first_owner: dict[str, dict] = {}
    for r in recs:
        for rel in r.get("files", []):
            first_owner.setdefault(rel, r)  # первый владелец хранит ванильную версию
    stash = _stash_root() / gid / game_hash(game)
    stash.mkdir(parents=True, exist_ok=True)
    stashed: list[str] = []
    try:
        for rel, r in first_owner.items():
            f = game / rel
            if f.exists():
                dst = stash / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(dst))
                stashed.append(rel)
            b = BACKUP_DIR / gid / r["id"] / rel
            if rel in r.get("backups", []) and b.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(b, f)
    except Exception:
        for rel in stashed:  # откат: возвращаем моды на место
            src = stash / rel
            if src.exists():
                (game / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, game / rel)
        shutil.rmtree(stash, ignore_errors=True)
        raise
    prune_empty_dirs(game, stashed, protected_dirs(profile, game))
    _vanilla_set(gid, game, {"vanilla_mode": True, "since": datetime.now().strftime("%Y-%m-%d %H:%M"),
                             "files": stashed, "mods": [r["name"] for r in recs]})
    return len(stashed)


def vanilla_disable(profile: dict, game: Path) -> int:
    """Возвращает моды из stash на место (состояние снимается только после успешного возврата)."""
    gid = profile["id"]
    state = vanilla_state(gid, game)
    if not state:
        return 0
    stash = _stash_root() / gid / game_hash(game)
    n = 0
    for rel in state.get("files", []):
        src = stash / rel
        if src.exists():
            (game / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, game / rel)
            n += 1
    shutil.rmtree(stash, ignore_errors=True)
    _vanilla_set(gid, game, None)
    return n


enable_vanilla = vanilla_enable
disable_vanilla = vanilla_disable


# ---- «Свежие загрузки»
def get_downloads_dir() -> Path:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
            p = Path(os.path.expandvars(winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")[0]))
            if p.is_dir():
                return p
    except Exception:
        pass
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Downloads"


def recent_archives(folder: Path, limit: int = 5) -> list[dict]:
    """Только первый уровень папки (scandir, без обхода вглубь), новые архивы первыми."""
    found: list[tuple[float, str, int]] = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.is_file() and os.path.splitext(e.name)[1].lower() in ARCHIVE_EXTS:
                        st = e.stat()
                        found.append((st.st_mtime, e.path, st.st_size))
                except OSError:
                    continue
    except OSError:
        return []
    found.sort(reverse=True)
    return [{"path": Path(p), "mtime": m, "size": s} for m, p, s in found[:limit]]


# ---- «Доктор зависимостей»
def check_deps(profile: dict, game: Path) -> list[tuple[str, bool]]:
    return [(d, (game / d).exists()) for d in profile.get("deps", [])]


# ---- конфиги модов
CONFIG_EXTS = {".ini", ".cfg", ".json", ".xml", ".toml"}


def find_mod_configs(game: Path, files: list[str]) -> list[Path]:
    out = [game / rel for rel in files if os.path.splitext(rel)[1].lower() in CONFIG_EXTS]
    return sorted((p for p in out if p.is_file()), key=lambda p: p.as_posix().lower())


def scan_folder_configs(folder: Path, limit: int = 300) -> list[Path]:
    out: list[Path] = []
    for dirpath, _dirs, names in os.walk(folder):
        for n in names:
            if os.path.splitext(n)[1].lower() in CONFIG_EXTS:
                out.append(Path(dirpath) / n)
                if len(out) >= limit:
                    return sorted(out, key=lambda p: p.as_posix().lower())
    return sorted(out, key=lambda p: p.as_posix().lower())


def read_text_file(path: Path) -> tuple[str, str, str]:
    """(текст с '\\n', исходный перевод строки, кодировка)."""
    raw = path.read_bytes()
    enc = "utf-8-sig" if raw.startswith(codecs.BOM_UTF8) else "utf-8"
    try:
        text = raw.decode(enc)
    except UnicodeDecodeError:
        enc, text = "cp1252", raw.decode("cp1252", errors="replace")
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), nl, enc


def write_text_file(path: Path, text: str, nl: str = "\n", enc: str = "utf-8", backup: bool = True) -> None:
    """Безопасная запись: .bak рядом с оригиналом, запись во временный файл и атомарная замена."""
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".gmi_tmp")
    with open(tmp, "w", encoding=enc, newline="") as f:
        f.write(text.replace("\n", nl))
    os.replace(tmp, path)


_INI_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_INI_KV = re.compile(r"^(\s*)([^\s;#=\[][^=]*?)(\s*=\s*)(.*?)(\s+[;#].*)?\s*$")


def parse_ini(text: str) -> list[dict]:
    rows, section = [], ""
    for i, line in enumerate(text.split("\n")):
        s = _INI_SECTION.match(line)
        if s:
            section = s.group(1)
            continue
        m = _INI_KV.match(line)
        if m:
            val = m.group(4)
            rows.append({"line": i, "section": section, "key": m.group(2).strip(), "value": val,
                         "bool": val.strip().lower() in ("true", "false")})
    return rows


def apply_ini_edits(text: str, edits: dict[int, str]) -> str:
    lines = text.split("\n")
    for i, new in edits.items():
        m = _INI_KV.match(lines[i]) if 0 <= i < len(lines) else None
        if m:
            lines[i] = lines[i][:m.start(4)] + new + lines[i][m.end(4):]
    return "\n".join(lines)


def validate_config_text(path: Path, text: str) -> str | None:
    """Текст ошибки синтаксиса или None."""
    ext = path.suffix.lower()
    try:
        if ext == ".json":
            json.loads(text)
        elif ext == ".xml":
            import xml.etree.ElementTree as ET
            ET.fromstring(text.encode("utf-8"))
        elif ext == ".toml":
            try:
                import tomllib
            except ImportError:
                return None
            tomllib.loads(text)
    except Exception as e:
        return str(e)
    return None


# ---- «Защита сохранений»
def _expand_save_templates(templates: list[str]) -> list[Path]:
    out: list[Path] = []
    for t in templates:
        variants = [t, t.replace("%USERPROFILE%\\Documents", "%OneDrive%\\Documents")]
        for v in variants:
            e = os.path.expandvars(v)
            if "%" in e:
                continue
            p = Path(e)
            if p.is_dir() and p not in out:
                out.append(p)
    return out


def save_dirs(profile: dict) -> list[Path]:
    return _expand_save_templates(profile.get("saves", []))


def list_save_backups(gid: str) -> list[Path]:
    root = _save_backups_root() / gid
    return sorted(root.glob("*.zip"), key=lambda p: p.name, reverse=True) if root.is_dir() else []


def rotate_save_backups(gid: str, limit: int) -> None:
    for old in list_save_backups(gid)[max(1, limit):]:
        try:
            old.unlink()
        except OSError:
            pass


def backup_saves(profile: dict, limit: int | None = 5, label: str = "") -> tuple[Path, int] | None:
    dirs = save_dirs(profile)
    if not dirs:
        return None
    dest = _save_backups_root() / profile["id"]
    dest.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + (f"-{label}" if label else "") + ".zip"
    path, n = dest / name, 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for idx, d in enumerate(dirs):
            for f in d.rglob("*"):
                if f.is_file():
                    z.write(f, f"{idx}/{f.relative_to(d).as_posix()}")
                    n += 1
        z.writestr("manifest.json", json.dumps({"sources": [str(d) for d in dirs]}, ensure_ascii=False))
    if n == 0:
        path.unlink(missing_ok=True)
        return None
    if limit:
        rotate_save_backups(profile["id"], limit)
    return path, n


def restore_save_backup(profile: dict, snapshot: Path, limit: int | None = 5) -> int:
    """Сначала снимок текущих сейвов (чтобы восстановление можно было отменить), затем распаковка."""
    current = {str(d): d for d in save_dirs(profile)}
    backup_saves(profile, limit=None, label="prerestore")
    n = 0
    with zipfile.ZipFile(snapshot) as z:
        sources = json.loads(z.read("manifest.json"))["sources"]
        for info in z.infolist():
            if info.is_dir() or info.filename == "manifest.json":
                continue
            idx, _, rel = info.filename.partition("/")
            if not idx.isdigit() or int(idx) >= len(sources) or not rel or not _safe_rel(rel):
                continue
            base = current.get(sources[int(idx)])
            if base is None:  # восстанавливаем только в папки, известные профилю
                continue
            target = base / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as s, open(target, "wb") as d:
                shutil.copyfileobj(s, d)
            n += 1
    if limit:
        rotate_save_backups(profile["id"], limit)
    return n


# ---- макет интерфейса (чистая логика): нижняя часть = «Установленные моды» + «Журнал»
LAYOUT_ARRANGE = ("side", "stack")  # рядом / друг над другом


def default_layout() -> dict:
    return {"arrange": "side", "swap": False, "sash": None}


def normalize_layout(layout) -> dict:
    """Чинит макет из config.json (в том числе старого формата v2.0 с «rows» — он просто сбрасывается)."""
    if not isinstance(layout, dict):
        return default_layout()
    arrange = layout.get("arrange") if layout.get("arrange") in LAYOUT_ARRANGE else "side"
    sash = layout.get("sash")
    sash = float(sash) if isinstance(sash, (int, float)) and not isinstance(sash, bool) and 0.1 <= sash <= 0.9 else None
    return {"arrange": arrange, "swap": layout.get("swap") is True, "sash": sash}


def set_arrangement(layout: dict, arrange: str) -> dict:
    if arrange not in LAYOUT_ARRANGE or arrange == layout["arrange"]:
        return layout
    return {"arrange": arrange, "swap": layout["swap"], "sash": None}


def swap_panels(layout: dict) -> dict:
    return {"arrange": layout["arrange"], "swap": not layout["swap"], "sash": None}


def bottom_order(layout: dict) -> tuple[str, str]:
    return ("log", "mods") if layout["swap"] else ("mods", "log")


def default_sash(layout: dict) -> float:
    """Доля первой панели: «Установленные моды» получают больше места, где бы они ни стояли."""
    return 0.4 if layout["swap"] else 0.6


def initial_game(cfg: dict, profiles: dict) -> str | None:
    """Игра при запуске: только сохранённая пользователем; иначе None («Выберите игру...»)."""
    last = cfg.get("last_game")
    return last if isinstance(last, str) and last in profiles else None


# ---- сборки модов (.gmpack.zip)
def is_gmpack(path: Path) -> bool:
    n = path.name.lower()
    if n.endswith(PACK_SUFFIX):
        return True
    if n.endswith(".zip") and path.is_file():
        try:
            with zipfile.ZipFile(path) as z:
                return "gmpack.json" in z.namelist()
        except (zipfile.BadZipFile, OSError):
            return False
    return False


def is_supported_source(path: Path) -> bool:
    return path.is_dir() or (path.is_file() and (path.suffix.lower() in ARCHIVE_EXTS or is_gmpack(path)))


def _rec_file_source(game: Path, gid: str, rel: str, later: list[dict]) -> Path:
    """Версия файла, принадлежащая моду: у следующего владельца это его бэкап, иначе файл на диске."""
    for nxt in later:
        if rel in nxt.get("files", []):
            b = BACKUP_DIR / gid / nxt["id"] / rel
            if rel in nxt.get("backups", []) and b.exists():
                return b
            break
    return game / rel


def export_gmpack(profile: dict, game: Path, out_path: Path) -> int:
    gid = profile["id"]
    if vanilla_state(gid, game):
        raise ModError("err_vanilla_active")
    recs = _game_records(gid, game)
    if not recs:
        raise ModError("err_pack_empty")
    mods = []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for i, r in enumerate(recs):
            files = []
            for rel in r.get("files", []):
                src = _rec_file_source(game, gid, rel, recs[i + 1:])
                if src.is_file():
                    z.write(src, f"mods/{i}/{rel}")
                    files.append(rel)
            mods.append({"name": r["name"], "archive": r.get("archive", r["name"]),
                         "dirs": r.get("dirs", []), "date": r.get("date", ""), "files": files})
        z.writestr("gmpack.json", json.dumps({
            "format": 1, "gid": gid, "game_name": profile["name"], "app_version": APP_VERSION,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"), "mods": mods}, ensure_ascii=False, indent=2))
    return len(mods)


def import_gmpack(profile: dict, pack: Path, game: Path, log, confirm=None, settings=None) -> bool:
    gid = profile["id"]
    if vanilla_state(gid, game):
        raise ModError("err_vanilla_active")
    try:
        z = zipfile.ZipFile(pack)
        manifest = json.loads(z.read("gmpack.json"))
        mods = manifest["mods"]
        assert manifest.get("format") == 1 and isinstance(mods, list)
    except Exception:
        raise ModError("err_pack_bad")
    with z:
        if manifest.get("gid") != gid:
            raise ModError("err_pack_game", game=manifest.get("game_name") or manifest.get("gid"))
        log(T("log_pack_import", game=profile["name"], n=len(mods)))
        ok = 0
        with tempfile.TemporaryDirectory(prefix="gmi_pack_", ignore_cleanup_errors=True) as tmp:
            for i, mod in enumerate(mods):
                root = Path(tmp) / str(i)
                prefix = f"mods/{i}/"
                for info in z.infolist():
                    rel = info.filename[len(prefix):]
                    if info.is_dir() or not info.filename.startswith(prefix) or not rel or not _safe_rel(rel):
                        continue
                    target = root / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as s, open(target, "wb") as d:
                        shutil.copyfileobj(s, d)
                log(f"▶ {mod.get('name', i)}")
                plan = Plan()
                if root.is_dir():
                    plan.ops.append(Op(root, ""))
                if plan.ops and _apply_plan(profile, plan, game, str(mod.get("name", i)),
                                            str(mod.get("archive") or mod.get("name", i)), log, confirm, settings):
                    ok += 1
        return ok == len(mods)


# ===================================================================== windows ==
def set_titlebar_dark(root: tk.Misc, dark: bool) -> None:
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


# ========================================================================= GUI ==
_ACTIVE_THEME = ["dark"]  # текущая тема для Canvas-компонентов (Pill, ToggleSwitch)


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


if tk is not None:
    import tkinter.font as tkfont

    class Pill(tk.Canvas):
        """Цветной pill-бейдж на Canvas (kind: ok/warn/err/info/muted/solid)."""
        def __init__(self, parent, text="", kind="muted", command=None, surface="surface"):
            super().__init__(parent, height=26, highlightthickness=0, bd=0)
            self._text, self._kind, self._surface = text, kind, surface
            self._font = tkfont.Font(family=FONT, size=9, weight="bold")
            if command:
                self.configure(cursor="hand2")
                self.bind("<Button-1>", lambda e: command())
            self.draw()

        def set(self, text: str, kind: str | None = None):
            self._text = text
            if kind:
                self._kind = kind
            self.draw()

        def draw(self):
            c = THEMES[_ACTIVE_THEME[0]]
            color = {"ok": c["ok"], "warn": c["warn"], "err": c["err"], "info": c["accent2"],
                     "muted": c["muted"], "solid": c["on_accent"]}.get(self._kind, c["muted"])
            fill = c["accent"] if self._kind == "solid" else mix_color(color, c[self._surface], 0.82)
            w = self._font.measure(self._text) + 24
            self.delete("all")
            self.configure(width=w, bg=c[self._surface])
            round_rect(self, 1, 2, w - 1, 24, 11, fill=fill, outline="" if self._kind == "solid" else mix_color(color, c[self._surface], 0.55))
            self.create_text(w / 2, 13, text=self._text, fill=color, font=self._font)

    class ToggleSwitch(tk.Canvas):
        """Переключатель (toggle switch) на Canvas."""
        def __init__(self, parent, value=False, command=None, surface="surface"):
            super().__init__(parent, width=46, height=26, highlightthickness=0, bd=0, cursor="hand2")
            self.value, self.command, self._surface = bool(value), command, surface
            self.bind("<Button-1>", self._toggle)
            self.draw()

        def set(self, value: bool):
            self.value = bool(value)
            self.draw()

        def _toggle(self, _e=None):
            self.value = not self.value
            self.draw()
            if self.command:
                self.command(self.value)

        def draw(self):
            c = THEMES[_ACTIVE_THEME[0]]
            self.delete("all")
            self.configure(bg=c[self._surface])
            round_rect(self, 2, 3, 44, 23, 10, fill=c["accent"] if self.value else c["hover"],
                       outline=c["accent"] if self.value else c["border"])
            x = 33 if self.value else 13
            self.create_oval(x - 8, 5, x + 8, 21, fill=c["on_accent"] if self.value else c["fg"], outline="")


class App:
    def __init__(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        load_external_languages()
        self.cfg = load_config()
        I18N.lang = self.cfg.get("lang") if self.cfg.get("lang") in STR else default_language()
        self.theme = self.cfg.get("theme", "dark")
        if self.theme not in THEMES:
            self.theme = "dark"
        _ACTIVE_THEME[0] = self.theme
        self.profiles = load_profiles()
        self.gid: str | None = initial_game(self.cfg, self.profiles)  # None = «Выберите игру...»
        self.settings = get_settings(self.cfg)
        self.layout = normalize_layout(self.cfg.get("layout"))

        load_dnd()
        self.root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry(self.cfg.get("geometry", "1180x860"))
        self.root.minsize(960, 700)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Control-o>", lambda e: self.pick_archives())
        self.root.bind("<Control-f>", lambda e: self.open_game_search())
        for k in ("<KeyPress-Shift_L>", "<KeyPress-Shift_R>"):
            self.root.bind_all(k, lambda e: setattr(self, "_shift", True))
        for k in ("<KeyRelease-Shift_L>", "<KeyRelease-Shift_R>"):
            self.root.bind_all(k, lambda e: setattr(self, "_shift", False))

        self.style = ttk.Style(self.root)
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self._shift = False
        self._timers: dict[str, str] = {}
        self.drop_hover = False
        self.layout_edit = False
        self.log_lines: list[str] = []
        self.items: list[dict] = []
        self.shown: list[dict] = []
        self.recent_items: list[dict] = []
        self.recent_loaded = False
        self.installed_cache: dict[str, bool] = {}
        self.bottom_paned = None
        self.tab = "drop"
        self._drop_key = None
        self._drop_after = None
        self.vanilla_on = False
        self._note_full = ""
        self.hint_label = None
        self.log_text = self.listbox = self.drop = self.badge = self.progress = None
        self.recent_frame = self.footer = None
        self._backends: list[str] | None = None
        self._deps: tuple | None = None

        self.game_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value=T("ready"))
        self.count_var = tk.StringVar(value="0")

        self.apply_styles()
        self._build_ui()
        self._recolor()
        self.load_game_path(autodetect=True)
        self.root.after(100, self._poll_queue)
        threading.Thread(target=self._startup_worker, daemon=True).start()

    # ---------------------------------------------------------------- helpers
    @property
    def profile(self) -> dict | None:
        return self.profiles.get(self.gid) if self.gid else None

    def game_path(self) -> Path | None:
        p = self.game_var.get().strip().strip('"')
        return Path(p) if p and Path(p).is_dir() else None

    def _need_game(self) -> bool:
        if self.profile is None:
            messagebox.showinfo(APP_NAME, T("msg_choose_game"))
            return False
        return True

    def _debounce(self, key: str, ms: int, fn):
        old = self._timers.pop(key, None)
        if old:
            self.root.after_cancel(old)
        self._timers[key] = self.root.after(ms, lambda: (self._timers.pop(key, None), fn()))

    def save_cfg(self):
        save_json(CONFIG_FILE, self.cfg)

    def save_cfg_soon(self):
        self._debounce("cfg", 600, self.save_cfg)

    def on_close(self):
        self._capture_sashes()
        for t in list(self._timers.values()):
            try:
                self.root.after_cancel(t)
            except Exception:
                pass
        self.cfg["geometry"] = self.root.geometry()
        self.cfg["layout"] = self.layout
        self.cfg["settings"] = self.settings
        self.save_cfg()
        self.root.destroy()

    def _startup_worker(self):
        """Тяжёлое при запуске — в фоне, окно уже на экране."""
        try:
            store_roots()
        except Exception:
            pass
        self.q.put(("footer", [b[0] for b in get_backends()]))

    def _footer_text(self) -> str:
        if self._backends is None:
            rar = "RAR: …"
        else:
            rar = T("footer_rar_ok", tool=self._backends[0]) if self._backends else T("footer_rar_no")
        parts = [rar]
        if not TkinterDnD:
            parts.append(T("footer_dnd_no"))
        parts.append(f"v{APP_VERSION}")
        return "   ·   ".join(parts)

    # ----------------------------------------------------------------- styles
    def apply_styles(self):
        c, s = THEMES[self.theme], self.style
        _ACTIVE_THEME[0] = self.theme
        s.theme_use("clam")
        self.root.configure(bg=c["bg"])
        s.configure(".", background=c["bg"], foreground=c["fg"], bordercolor=c["border"],
                    darkcolor=c["bg"], lightcolor=c["bg"], troughcolor=c["surface2"],
                    focuscolor=c["bg"], font=(FONT, 10))
        s.configure("TFrame", background=c["bg"])
        s.configure("Card.TFrame", background=c["surface"])
        s.configure("Panel.TFrame", background=c["surface"], relief="solid", borderwidth=1,
                    bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"])
        s.configure("TPanedwindow", background=c["bg"])
        s.configure("Sash", sashthickness=12, gripcount=3, background=c["bg"], bordercolor=c["bg"],
                    lightcolor=c["muted"], darkcolor=c["muted"])
        s.configure("TLabel", background=c["bg"], foreground=c["fg"])
        s.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"])
        s.configure("Title.TLabel", background=c["bg"], foreground=c["fg"], font=(FONT, 21, "bold"))
        s.configure("Card.TLabel", background=c["surface"], foreground=c["fg"])
        s.configure("CardTitle.TLabel", background=c["surface"], foreground=c["fg"], font=(FONT, 11, "bold"))
        s.configure("CardMuted.TLabel", background=c["surface"], foreground=c["muted"])
        s.configure("TRadiobutton", background=c["bg"], foreground=c["fg"])
        s.map("TRadiobutton", background=[("active", c["bg"])], indicatorcolor=[("selected", c["accent"])])
        s.configure("TCheckbutton", background=c["surface"], foreground=c["fg"])

        for name, bg, fg, hov, pad, font in (
            ("TButton", c["surface2"], c["fg"], c["hover"], (16, 8), (FONT, 10, "bold")),
            ("Accent.TButton", c["accent"], c["on_accent"], c["accent_hover"], (16, 8), (FONT, 10, "bold")),
            ("Danger.TButton", c["surface2"], c["err"], c["hover"], (16, 8), (FONT, 10, "bold")),
            ("Soft.TButton", c["surface2"], c["accent2"], c["hover"], (14, 7), (FONT, 10, "bold")),
            ("Mini.TButton", c["surface2"], c["fg"], c["hover"], (7, 3), (FONT, 10)),
            ("MiniAccent.TButton", c["accent"], c["on_accent"], c["accent_hover"], (12, 4), (FONT, 9, "bold")),
            ("Tool.TButton", c["surface2"], c["fg"], c["hover"], (10, 6), (FONT, 10, "bold")),
            ("ToolDanger.TButton", c["surface2"], c["err"], c["hover"], (10, 6), (FONT, 10, "bold")),
            ("ToolSoft.TButton", c["surface2"], c["accent2"], c["hover"], (10, 6), (FONT, 10, "bold")),
        ):
            s.configure(name, background=bg, foreground=fg, bordercolor=bg, lightcolor=bg, darkcolor=bg,
                        padding=pad, relief="flat", focusthickness=0, font=font)
            s.map(name, background=[("disabled", c["surface"]), ("pressed", hov), ("active", hov)],
                  foreground=[("disabled", c["muted"])], bordercolor=[("active", hov)],
                  lightcolor=[("active", hov)], darkcolor=[("active", hov)])

        s.configure("TEntry", fieldbackground=c["surface2"], foreground=c["fg"], insertcolor=c["fg"],
                    bordercolor=c["border"], lightcolor=c["surface2"], darkcolor=c["surface2"], padding=7)
        s.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])],
              darkcolor=[("focus", c["accent"])], fieldbackground=[("disabled", c["surface"])],
              foreground=[("disabled", c["muted"])])
        s.configure("TCombobox", fieldbackground=c["surface2"], background=c["surface2"], foreground=c["fg"],
                    arrowcolor=c["muted"], bordercolor=c["border"], lightcolor=c["surface2"],
                    darkcolor=c["surface2"], padding=7)
        s.map("TCombobox", fieldbackground=[("readonly", c["surface2"])],
              foreground=[("readonly", c["fg"])], background=[("active", c["hover"])],
              selectbackground=[("readonly", c["surface2"])], selectforeground=[("readonly", c["fg"])],
              bordercolor=[("focus", c["accent"])])
        s.configure("TSpinbox", fieldbackground=c["surface2"], foreground=c["fg"], arrowcolor=c["muted"],
                    bordercolor=c["border"], lightcolor=c["surface2"], darkcolor=c["surface2"],
                    background=c["surface2"], padding=5)
        self.root.option_add("*TCombobox*Listbox.background", c["surface2"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", c["on_accent"])
        self.root.option_add("*TCombobox*Listbox.font", "{%s} 10" % FONT)

        s.configure("Vertical.TScrollbar", background=c["hover"], troughcolor=c["surface"],
                    bordercolor=c["surface"], lightcolor=c["hover"], darkcolor=c["hover"],
                    arrowcolor=c["muted"], relief="flat")
        s.map("Vertical.TScrollbar", background=[("active", c["muted"])])
        s.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["surface2"],
                    bordercolor=c["bg"], lightcolor=c["accent"], darkcolor=c["accent"], thickness=8)
        s.configure("Treeview", background=c["surface2"], fieldbackground=c["surface2"], foreground=c["fg"],
                    bordercolor=c["surface2"], lightcolor=c["surface2"], darkcolor=c["surface2"],
                    rowheight=26, relief="flat")
        s.map("Treeview", background=[("selected", c["accent"])], foreground=[("selected", c["on_accent"])])
        s.configure("Treeview.Heading", background=c["surface"], foreground=c["muted"], relief="flat",
                    font=(FONT, 9, "bold"), bordercolor=c["surface"], lightcolor=c["surface"], darkcolor=c["surface"])
        s.map("Treeview.Heading", background=[("active", c["hover"])])

    def _recolor(self):
        c = THEMES[self.theme]
        self._recolor_blocks()
        self.root.after(60, lambda: set_titlebar_dark(self.root, c["is_dark"]))

    def _recolor_blocks(self):
        c = THEMES[self.theme]
        if self.log_text is not None:
            self.log_text.configure(bg=c["surface2"], fg=c["fg"], insertbackground=c["fg"],
                                    selectbackground=c["accent"], selectforeground=c["on_accent"])
            self.log_text.tag_configure("head", foreground=c["accent2"], font=("Consolas", 10, "bold"))
            self.log_text.tag_configure("ok", foreground=c["ok"])
            self.log_text.tag_configure("warn", foreground=c["warn"])
            self.log_text.tag_configure("err", foreground=c["err"])
        if self.listbox is not None:
            self.listbox.configure(bg=c["surface2"], fg=c["fg"], selectbackground=c["accent"],
                                   selectforeground=c["on_accent"])
        self.draw_drop()

    @staticmethod
    def _round_rect(cv, x1, y1, x2, y2, r, **kw):
        return round_rect(cv, x1, y1, x2, y2, r, **kw)

    def _menu(self) -> "tk.Menu":
        c = THEMES[self.theme]
        return tk.Menu(self.root, tearoff=0, bg=c["surface"], fg=c["fg"], activebackground=c["accent"],
                       activeforeground=c["on_accent"], selectcolor=c["accent2"], bd=0, relief="flat",
                       font=(FONT, 10))

    def _popup(self, menu, widget):
        try:
            menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height() + 4)
        finally:
            menu.grab_release()

    def open_theme_menu(self):
        menu, var = self._menu(), tk.StringVar(value=self.theme)
        for name in THEME_ORDER:
            menu.add_radiobutton(label="  " + T("theme_" + name), variable=var, value=name,
                                 command=lambda n=name: self.set_theme(n))
        self._popup(menu, self.theme_btn)

    def open_language_menu(self):
        menu, var = self._menu(), tk.StringVar(value=I18N.lang)
        for code, name in LANG_NAMES.items():
            menu.add_radiobutton(label="  " + name, variable=var, value=code,
                                 command=lambda c=code: self.set_language(c))
        self._popup(menu, self.lang_btn)

    def set_theme(self, name: str):
        if name in THEMES and name != self.theme:
            self.theme = name
            self.cfg["theme"] = name
            self.save_cfg()
            self.rebuild()

    def set_language(self, code: str):
        if code != I18N.lang:
            I18N.lang = code
            self.cfg["lang"] = code
            self.save_cfg()
            self.status_var.set(T("ready"))
            self.rebuild()

    def rebuild(self):
        """Полная перестройка — только при смене темы или языка."""
        self._capture_sashes()
        for w in self.root.winfo_children():
            w.destroy()
        self.log_text = self.listbox = self.drop = self.badge = self.progress = None
        self.recent_frame = None
        self.apply_styles()
        self._build_ui()
        self._recolor()
        self.update_game_status()
        self.refresh_items()
        self.refresh_deps()

    # --------------------------------------------------------------------- ui
    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=(18, 12, 18, 8))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)  # вся «лишняя» высота — нижним панелям

        # --- шапка: название + бейдж версии | компактные кнопки
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        Pill(header, f"v{APP_VERSION}", "solid", surface="bg").pack(side="left", padx=12, pady=(6, 0))
        right = ttk.Frame(header)
        right.pack(side="right")
        self.edit_switch = ToggleSwitch(right, value=self.layout_edit, command=self.set_layout_edit, surface="bg")
        self.edit_switch.pack(side="right", padx=(10, 0))
        ttk.Label(right, text=T("layout_edit"), style="Muted.TLabel").pack(side="right", padx=(14, 4))
        self.lang_btn = ttk.Button(right, text="🌐 " + T("language"), style="Soft.TButton", command=self.open_language_menu)
        self.lang_btn.pack(side="right", padx=4)
        self.theme_btn = ttk.Button(right, text="🎨 " + T("appearance"), style="Soft.TButton", command=self.open_theme_menu)
        self.theme_btn.pack(side="right", padx=4)
        ttk.Button(right, text="⚙ " + T("settings"), style="Soft.TButton", command=self.open_settings).pack(side="right", padx=4)

        # --- верхний ряд: карточка игры | компактная зона загрузки (вкладки «Загрузка / Свежие»)
        top = ttk.Frame(outer)
        top.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, minsize=400)
        card = ttk.Frame(top, style="Panel.TFrame", padding=(14, 12))
        card.grid(row=0, column=0, sticky="nsew")
        self._build_game_card(card)
        tcard = ttk.Frame(top, style="Panel.TFrame", padding=(12, 10))
        tcard.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        self._build_top_card(tcard)

        # --- панель режима макета (скрыта, пока режим не включён)
        self._build_edit_bar(outer)

        # --- нижняя часть: «Установленные моды» и «Журнал»
        self.bottom_host = ttk.Frame(outer)
        self.bottom_host.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        self.bottom_host.rowconfigure(0, weight=1)
        self.bottom_host.columnconfigure(0, weight=1)
        self.build_bottom()

        self.footer = ttk.Label(outer, text=self._footer_text(), style="Muted.TLabel")
        self.footer.grid(row=4, column=0, sticky="w", pady=(8, 0))

    def _build_game_card(self, card):
        card.columnconfigure(1, weight=1)
        card.columnconfigure(0, minsize=64)
        ttk.Label(card, text=T("game"), style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.ids = list(self.profiles.keys())
        self.name_to_id = {self.profiles[i]["name"]: i for i in self.ids}
        self.game_combo = ttk.Combobox(card, values=[self.profiles[i]["name"] for i in self.ids],
                                       state="readonly", height=16)
        self.game_combo.set(self.profile["name"] if self.profile else T("choose_game"))
        self.game_combo.grid(row=0, column=1, sticky="ew", padx=(10, 8))
        self.game_combo.bind("<<ComboboxSelected>>", self.on_game_selected)
        # колесо мыши над списком не должно «листать» игры (иначе выбирается случайная)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.game_combo.bind(seq, lambda e: "break")
        ttk.Button(card, text=T("btn_search") + " 🔍", style="Soft.TButton",
                   command=self.open_game_search).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(card, text=T("add_game"), command=self.add_game_dialog).grid(row=0, column=3)

        ttk.Label(card, text=T("folder"), style="CardTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.path_entry = ttk.Entry(card, textvariable=self.game_var)
        self.path_entry.grid(row=1, column=1, sticky="ew", padx=(10, 8), pady=(10, 0))
        self.path_entry.bind("<KeyRelease>", lambda e: self._debounce("path", 250, self.apply_path_edit))
        self.browse_btn = ttk.Button(card, text=T("browse"), command=self.pick_game_dir)
        self.browse_btn.grid(row=1, column=2, padx=(0, 8), pady=(10, 0))
        self.detect_btn = ttk.Button(card, text=T("detect"), command=self.autodetect)
        self.detect_btn.grid(row=1, column=3, pady=(10, 0))

        # статус | доктор зависимостей | заметка (одна строка с «…»)
        info = ttk.Frame(card, style="Card.TFrame")
        info.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        info.columnconfigure(2, weight=1)
        self.status_pill = Pill(info, "", "muted")
        self.status_pill.grid(row=0, column=0)
        self.doctor_pill = Pill(info, T("doc_checking"), "muted", command=self.show_deps_detail)
        self.doctor_pill.grid(row=0, column=1, padx=8)
        self.note_label = ttk.Label(info, style="CardMuted.TLabel", cursor="hand2", anchor="w")
        self.note_label.grid(row=0, column=2, sticky="ew", padx=(6, 0))
        self.note_label.bind("<Configure>", lambda e: self.root.after_idle(self._fit_note))
        self.note_label.bind("<Button-1>", lambda e: self._note_full and messagebox.showinfo(APP_NAME, self._note_full))

        # ванильный режим | инструменты
        self.strip = ttk.Frame(card, style="Card.TFrame")
        self.strip.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        self.strip.columnconfigure(1, weight=1)
        self.vanilla_box = ttk.Frame(self.strip, style="Card.TFrame")
        self.vanilla_box.grid(row=0, column=0)
        self.vanilla_switch = ToggleSwitch(self.vanilla_box, value=False, command=self.toggle_vanilla)
        self.vanilla_switch.pack(side="left")
        ttk.Label(self.vanilla_box, text=T("vanilla_mode"), style="Card.TLabel").pack(side="left", padx=8)
        self.vanilla_pill = Pill(self.vanilla_box, T("vanilla_on"), "warn")
        self.tools_btn = ttk.Button(self.strip, text="⋯ " + T("tools"), style="Soft.TButton", command=self.open_tools_menu)
        self.tools_btn.grid(row=0, column=2)

    def _set_note(self, text: str):
        self._note_full = text
        self._fit_note()

    def _fit_note(self):
        lbl = getattr(self, "note_label", None)
        if lbl is None or not lbl.winfo_exists():
            return
        text, w = self._note_full, lbl.winfo_width()
        if not text or w < 60:
            lbl.configure(text=text if w < 60 else "")
            return
        font = getattr(self, "_note_font", None) or tkfont.Font(family=FONT, size=10)
        self._note_font = font
        if font.measure(text) > w:
            lo, hi = 1, len(text)
            while lo < hi:  # бинарный поиск длины, которая влезает
                mid = (lo + hi + 1) // 2
                lo, hi = (mid, hi) if font.measure(text[:mid] + "…") <= w else (lo, mid - 1)
            text = text[:lo] + "…"
        if lbl.cget("text") != text:
            lbl.configure(text=text)

    def _build_top_card(self, tc):
        tc.columnconfigure(0, weight=1)
        head = ttk.Frame(tc, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        self.tab_btns = {}
        for name, key in (("drop", "blk_drop"), ("recent", "blk_recent")):
            b = ttk.Button(head, text=T(key), command=lambda n=name: self._switch_tab(n))
            b.pack(side="left", padx=(0, 6))
            self.tab_btns[name] = b
        self.recent_refresh_btn = ttk.Button(head, text="⟳ " + T("refresh"), style="Mini.TButton", command=self.refresh_recents)

        self.stack = ttk.Frame(tc, style="Card.TFrame", height=112)  # фиксированная высота: вкладки не «прыгают»
        self.stack.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.stack.grid_propagate(False)
        self.stack.columnconfigure(0, weight=1)
        self.stack.rowconfigure(0, weight=1)

        self.drop_page = ttk.Frame(self.stack, style="Card.TFrame")
        self.drop_page.grid(row=0, column=0, sticky="nsew")
        self.drop_page.columnconfigure(0, weight=1)
        self.drop_page.rowconfigure(0, weight=1)
        self.drop = tk.Canvas(self.drop_page, height=84, highlightthickness=0, bd=0)
        self.drop.grid(row=0, column=0, sticky="nsew")
        self.drop.bind("<Configure>", self._on_drop_configure)
        self.drop.bind("<Button-1>", lambda e: self.pick_archives())
        self.drop.bind("<Enter>", lambda e: self._set_hover(True))
        self.drop.bind("<Leave>", lambda e: self._set_hover(False))
        if TkinterDnD:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<DropEnter>>", self.on_drop_enter)
            self.drop.dnd_bind("<<DropLeave>>", self.on_drop_leave)
            self.drop.dnd_bind("<<Drop>>", self.on_drop)
        pf = ttk.Frame(self.drop_page, style="Card.TFrame")
        pf.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        pf.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(pf, mode="determinate", maximum=1, value=0)
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(pf, textvariable=self.status_var, style="CardMuted.TLabel", width=26, anchor="e").grid(
            row=0, column=1, padx=(8, 0))

        self.recent_frame = ttk.Frame(self.stack, style="Card.TFrame")
        self.recent_frame.grid(row=0, column=0, sticky="nsew")
        self.recent_frame.columnconfigure(0, weight=1)
        self.apply_top_tabs()

    def apply_top_tabs(self):
        """Показывает/скрывает вкладку «Свежие загрузки» (настройка) без перестройки окна."""
        allowed = self.settings["show_downloads"]
        if allowed:
            self.tab_btns["recent"].pack(side="left", padx=(0, 6))
        else:
            self.tab_btns["recent"].pack_forget()
        self._switch_tab(self.tab if allowed else "drop")

    def _switch_tab(self, name: str):
        self.tab = name
        for n, b in self.tab_btns.items():
            b.configure(style="MiniAccent.TButton" if n == name else "Mini.TButton")
        if name == "recent":
            self.drop_page.grid_remove()
            self.recent_frame.grid()
            self.recent_refresh_btn.pack(side="right")
            self._render_recents()
            if not self.recent_loaded:
                self.refresh_recents()
        else:
            self.recent_frame.grid_remove()
            self.recent_refresh_btn.pack_forget()
            self.drop_page.grid()
            self.draw_drop()

    def _build_edit_bar(self, outer):
        bar = ttk.Frame(outer, style="Panel.TFrame", padding=(14, 8))
        bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.edit_bar = bar
        ttk.Label(bar, text=T("layout_arrange"), style="CardMuted.TLabel").pack(side="left")
        self.arr_btns = {}
        for key, label in (("side", "▥  " + T("layout_side")), ("stack", "▤  " + T("layout_stack"))):
            b = ttk.Button(bar, text=label, command=lambda k=key: self.set_arrange(k))
            b.pack(side="left", padx=(8, 0))
            self.arr_btns[key] = b
        ttk.Button(bar, text="⇄  " + T("layout_swap"), style="Soft.TButton", command=self.swap_ui).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text=T("layout_reset"), command=self.reset_layout).pack(side="right")
        self.edit_switches = {}
        for name, label_key, setting in (("recent", "blk_recent", "show_downloads"), ("log", "log", "show_log")):
            sw = ToggleSwitch(bar, value=self.settings[setting], command=lambda v, s=setting: self.set_setting(s, v))
            sw.pack(side="right", padx=(2, 14))
            ttk.Label(bar, text=T(label_key), style="Card.TLabel").pack(side="right")
            self.edit_switches[name] = sw
        self._update_arr_buttons()
        if not self.layout_edit:
            bar.grid_remove()

    def _update_arr_buttons(self):
        for key, b in getattr(self, "arr_btns", {}).items():
            b.configure(style="MiniAccent.TButton" if key == self.layout["arrange"] else "Mini.TButton")

    # ------------------------------------------------ макет: нижние панели (моды | журнал)
    def _bottom_min(self, name: str) -> int:
        side = self.layout["arrange"] == "side"
        return {"mods": 300 if side else 200, "log": 220 if side else 160}[name]

    def _sync_edit_switch(self, name: str, value: bool):
        sw = getattr(self, "edit_switches", {}).get(name)
        if sw is not None and sw.value != bool(value):
            sw.set(bool(value))

    def _capture_sashes(self):
        pw = self.bottom_paned
        if pw is None:
            return
        try:
            side = self.layout["arrange"] == "side"
            size = pw.winfo_width() if side else pw.winfo_height()
            x, y = pw.sash_coord(0)
            if size > 100:
                self.layout["sash"] = round((x if side else y) / size, 3)
        except Exception:
            pass

    def _restore_sashes(self, tries: int = 0):
        pw = self.bottom_paned
        if pw is None:
            return
        try:
            side = self.layout["arrange"] == "side"
            size = pw.winfo_width() if side else pw.winfo_height()
            if size < 100 and tries < 10:
                self.root.after(80, lambda: self._restore_sashes(tries + 1))
                return
            pos = int(size * (self.layout.get("sash") or default_sash(self.layout)))
            pw.sash_place(0, pos, 0) if side else pw.sash_place(0, 0, pos)
        except Exception:
            pass

    def build_bottom(self):
        """Перестраивает только нижнюю область (две панели) — шапка и верхний ряд не затрагиваются."""
        self._capture_sashes()
        for w in self.bottom_host.winfo_children():
            w.destroy()
        self.log_text = self.listbox = self.badge = None
        self.hint_label = None
        self.bottom_paned = None
        c = THEMES[self.theme]
        if not self.settings["show_log"]:  # журнал скрыт -> «Установленные моды» занимают всё место
            self._make_panel(self.bottom_host, "mods").grid(row=0, column=0, sticky="nsew")
        else:
            side = self.layout["arrange"] == "side"
            pw = tk.PanedWindow(self.bottom_host, orient="horizontal" if side else "vertical", sashwidth=10,
                                sashrelief="flat", bd=0, bg=c["bg"], opaqueresize=True, showhandle=False)
            pw.grid(row=0, column=0, sticky="nsew")
            for name in bottom_order(self.layout):
                pw.add(self._make_panel(pw, name), minsize=self._bottom_min(name), stretch="always")
            pw.bind("<ButtonRelease-1>", lambda e: (self._capture_sashes(), self._save_layout_soon()))
            self.bottom_paned = pw
            self.root.after(60, self._restore_sashes)
        self._recolor_blocks()
        for line in self.log_lines:
            self._write_log(line)
        self._fill_list()
        self._set_hint(T("hint_unmanaged") if (self.profile and self.profile["mode"] == "folder") else "")

    def _make_panel(self, parent, name: str):
        card = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        head = ttk.Frame(card, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(head, text=T("installed" if name == "mods" else "log"), style="CardTitle.TLabel").pack(side="left")
        body = ttk.Frame(card, style="Card.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        (self._fill_mods if name == "mods" else self._fill_log)(body, head)
        return card

    def _fill_log(self, body, head):
        body.rowconfigure(0, weight=1)
        ttk.Button(head, text=T("clear"), style="Mini.TButton", command=self.clear_log).pack(side="right")
        self.log_text = tk.Text(body, wrap="word", state="disabled", relief="flat", bd=0, height=4,
                                highlightthickness=0, padx=12, pady=10, font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=sb.set)

    def _fill_mods(self, body, head):
        body.rowconfigure(1, weight=1)
        self.badge = Pill(head, self.count_var.get() or "0", "solid")
        self.badge.pack(side="left", padx=10)
        self.hint_label = ttk.Label(head, text="", style="CardMuted.TLabel")
        self.hint_label.pack(side="right")
        sf = ttk.Frame(body, style="Card.TFrame")
        sf.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        sf.columnconfigure(1, weight=1)
        ttk.Label(sf, text=T("search"), style="CardMuted.TLabel").grid(row=0, column=0, padx=(0, 8))
        se = ttk.Entry(sf, textvariable=self.search_var)
        se.grid(row=0, column=1, sticky="ew")
        se.bind("<KeyRelease>", lambda e: self._debounce("modsearch", 250, self._fill_list))
        self.listbox = tk.Listbox(body, selectmode="extended", activestyle="none", relief="flat", bd=0,
                                  highlightthickness=0, font=(FONT, 10), height=4)
        self.listbox.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.bind("<Delete>", lambda e: self.delete_selected())
        self.listbox.bind("<Double-Button-1>", lambda e: self.open_selected())
        self.listbox.bind("<Button-3>", self._mods_context_menu)

        # кнопки сами перестраиваются (1 ряд / 2×2 / колонка) и никогда не обрезаются
        bf = ttk.Frame(body, style="Card.TFrame")
        bf.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        specs = (("🗑 " + T("delete"), "ToolDanger.TButton", self.delete_selected),
                 ("⟳ " + T("refresh"), "Tool.TButton", self.refresh_items),
                 ("📂 " + T("open_dir"), "Tool.TButton", self.open_target),
                 ("⚙ " + T("cfg_btn"), "ToolSoft.TButton", self.open_configs_for_selected))
        buttons = [ttk.Button(bf, text=t, style=s, command=cmd) for t, s, cmd in specs]
        mode = {"cols": 0}

        def reflow(_e=None):
            w = bf.winfo_width()
            need = [b.winfo_reqwidth() + 8 for b in buttons]
            cols = 4 if w >= sum(need) else (2 if w >= 2 * max(need) else 1)
            if cols == mode["cols"]:
                return
            mode["cols"] = cols
            for b in buttons:
                b.grid_forget()
            for i, b in enumerate(buttons):
                b.grid(row=i // cols, column=i % cols, sticky="ew", padx=3, pady=3)
            for i in range(4):
                bf.columnconfigure(i, weight=1 if i < cols else 0)
        bf.bind("<Configure>", reflow)
        bf.after_idle(reflow)

    def set_layout_edit(self, value: bool):
        self.layout_edit = bool(value)
        if self.layout_edit:
            self.edit_bar.grid()
        else:
            self.edit_bar.grid_remove()

    def set_arrange(self, arrange: str):
        self.layout = set_arrangement(self.layout, arrange)
        self._layout_changed()

    def swap_ui(self):
        self.layout = swap_panels(self.layout)
        self._layout_changed()

    def reset_layout(self):
        self.layout = default_layout()
        for key in ("show_log", "show_downloads"):
            self.settings[key] = True
        for name in ("log", "recent"):
            self._sync_edit_switch(name, True)
        self.apply_top_tabs()
        self._layout_changed()

    def _save_layout_soon(self):
        self.cfg["layout"], self.cfg["settings"] = self.layout, self.settings
        self.save_cfg_soon()

    def _layout_changed(self):
        self._save_layout_soon()
        self._update_arr_buttons()
        self.build_bottom()

    # ---------------------------------------------------------- зона загрузки (Canvas)
    def _on_drop_configure(self, _event=None):
        """Перерисовка откладывается и склеивается: при движении границ Canvas не рисуется на каждый пиксель."""
        if self._drop_after is None:
            self._drop_after = self.root.after_idle(self._drop_idle)

    def _drop_idle(self):
        self._drop_after = None
        self.draw_drop()

    def draw_drop(self, force: bool = False):
        cv = self.drop
        if cv is None or not cv.winfo_exists():
            return
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 40 or h < 40:
            return
        vanilla = self._vanilla_active()
        key = (w, h, self.drop_hover, self.profile is not None, vanilla, self.theme, I18N.lang, bool(TkinterDnD))
        if not force and key == self._drop_key:  # размер и состояние не менялись — ничего не рисуем
            return
        self._drop_key = key
        c = THEMES[self.theme]
        cv.delete("all")
        ready = self.profile is not None and not vanilla
        hover = self.drop_hover and ready
        cv.configure(bg=c["surface"], cursor="hand2" if ready else "arrow")
        outline = (c["accent2"] if hover else c["accent"]) if ready else c["border"]
        self._round_rect(cv, 3, 3, w - 3, h - 3, 16, fill=c["surface2"] if hover else c["surface"],
                         outline=outline, dash=(8, 5), width=3 if hover else 2)
        if vanilla:
            icon, title, sub = "⏸", T("drop_vanilla"), T("drop_vanilla_sub")
        elif self.profile is None:
            icon, title, sub = "🎮", T("drop_no_game"), T("drop_no_game_sub")
        else:
            icon = "⬇"
            title = T("drop_hover") if hover else (
                T("drop_title").replace("⬇", "").strip() if TkinterDnD else T("drop_nodnd"))
            sub = T("drop_sub")
        cv.create_text(w / 2, h * 0.24, text=icon, font=(FONT, 15),
                       fill=c["accent2"] if hover else (c["accent"] if ready else c["muted"]))
        cv.create_text(w / 2, h * 0.52, text=title, font=(FONT, 11, "bold"), fill=c["fg"] if ready else c["muted"])
        cv.create_text(w / 2, h * 0.78, text=sub, font=(FONT, 9), fill=c["muted"])

    def _set_hover(self, value: bool):
        if self.drop_hover != value:
            self.drop_hover = value
            self.draw_drop()

    # ----------------------------------------------------------------- журнал
    def log(self, msg: str):
        self.q.put(("log", msg))

    def _write_log(self, msg: str):
        if self.log_text is None:
            return
        tag = {"▶": "head", "✔": "ok", "⚠": "warn", "✖": "err", "🗑": "warn", "↻": "warn", "↺": "warn"}.get(msg.lstrip()[:1])
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n", (tag,) if tag else ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_lines.clear()
        if self.log_text is not None:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log_lines.append(payload)
                    self._write_log(payload)
                elif kind == "progress":
                    done, total, name = payload
                    if self.progress is not None:
                        self.progress.configure(maximum=max(total, 1), value=done)
                    if done < total:
                        short = name if len(name) <= 18 else name[:15] + "…"
                        self.status_var.set(T("installing", i=done + 1, n=total, name=short))
                elif kind in ("confirm", "preview"):
                    data, ev, box = payload
                    try:
                        box["ok"] = self.show_conflict_dialog(data) if kind == "confirm" else self.show_preview_dialog(data)
                    finally:
                        ev.set()
                elif kind == "done":
                    ok, total = payload
                    self.busy = False
                    self.status_var.set(T("done_status", ok=ok, n=total))
                    self.refresh_items()
                    self.refresh_deps()
                elif kind == "footer":
                    self._backends = payload
                    if self.footer is not None:
                        self.footer.configure(text=self._footer_text())
                elif kind == "recents":
                    self.recent_items, self.recent_loaded = payload, True
                    self._render_recents()
                elif kind == "deps":
                    gid, game_str, results = payload
                    gp = self.game_path()
                    if gid == self.gid and gp and str(gp) == game_str:
                        self._deps = results
                        self._apply_deps_ui()
                elif kind == "autodetected":
                    gid, found, manual = payload
                    if gid == self.gid:
                        if found and not self.game_var.get():
                            self.game_var.set(found)
                            self.remember_path()
                            self.installed_cache[gid] = True
                            self.update_game_status()
                            self.refresh_items()
                            self.refresh_deps()
                        elif not found:
                            self.update_game_status()
                            if manual:
                                self.log(T("log_autodetect_fail"))
                elif kind == "vanilla_error":
                    messagebox.showerror(APP_NAME, payload)
                elif kind == "vanilla_done":
                    self.busy = False
                    self.status_var.set(T("ready"))
                    self.update_vanilla_ui()
                    self.refresh_items()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _ask_main(self, kind: str, data):
        """Вызывается из рабочего потока: показать диалог в GUI-потоке и дождаться ответа."""
        ev, box = threading.Event(), {"ok": None}
        self.q.put((kind, (data, ev, box)))
        ev.wait()
        return box["ok"]

    def confirm_conflicts(self, conflicts) -> bool:
        return bool(self._ask_main("confirm", conflicts))

    def preview_files(self, entries):
        return self._ask_main("preview", entries)

    # ------------------------------------------------------------- выбор игры
    def on_game_selected(self, event=None):
        gid = self.name_to_id.get(self.game_combo.get())
        if gid:
            self.select_game(gid)

    def select_game(self, gid: str):
        if gid not in self.profiles:
            return
        self.gid = gid
        self.game_combo.set(self.profile["name"])
        self.game_combo.selection_clear()
        self.cfg["last_game"] = gid
        self.save_cfg_soon()
        self.load_game_path(autodetect=True)

    def open_game_search(self, event=None):
        """Компактное окно поиска игры с бейджами «Установлена / Не найдена»."""
        old = getattr(self, "_search_dlg", None)
        if old is not None and old.winfo_exists():
            old.lift()
            old.focus_force()
            return
        c = THEMES[self.theme]
        dlg = tk.Toplevel(self.root)
        self._search_dlg = dlg
        dlg.title(T("search_title"))
        dlg.configure(bg=c["bg"])
        dlg.transient(self.root)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=T("search_hint"), style="Muted.TLabel").pack(anchor="w")
        var = tk.StringVar()
        entry = ttk.Entry(frm, textvariable=var, width=48)
        entry.pack(fill="x", pady=(6, 10))
        box = ttk.Frame(frm, style="Panel.TFrame", padding=2)
        box.pack(fill="both", expand=True)
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        lb = tk.Listbox(box, height=13, activestyle="none", relief="flat", bd=0, highlightthickness=0,
                        font=(FONT, 11), bg=c["surface2"], fg=c["fg"], selectbackground=c["accent"],
                        selectforeground=c["on_accent"], exportselection=False)
        lb.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, orient="vertical", command=lb.yview)
        sb.grid(row=0, column=1, sticky="ns")
        lb.configure(yscrollcommand=sb.set)
        shown: list[str] = []

        def badge(gid: str) -> str:
            v = self.installed_cache.get(gid)
            return "…" if v is None else (T("badge_installed") if v else T("badge_notfound"))

        def fill(keep: str | None = None):
            q = var.get().strip().lower()
            shown[:] = [g for g in self.ids if q in self.profiles[g]["name"].lower()]
            lb.delete(0, "end")
            for i, g in enumerate(shown):
                lb.insert("end", f"  {self.profiles[g]['name']}    [{badge(g)}]")
                v = self.installed_cache.get(g)
                lb.itemconfig(i, foreground=c["ok"] if v else (c["muted"] if v is False else c["fg"]))
            if not shown:
                lb.insert("end", "  " + T("search_empty"))
                lb.itemconfig(0, foreground=c["muted"])
                return
            idx = shown.index(keep) if keep in shown else 0
            lb.selection_set(idx)
            lb.activate(idx)
            lb.see(idx)

        def current() -> str | None:
            sel = lb.curselection()
            return shown[sel[0]] if sel and shown else None

        def choose(event=None):
            gid = current()
            if gid:
                dlg.destroy()
                self.select_game(gid)
            return "break"

        def move(delta: int):
            if not shown:
                return "break"
            sel = lb.curselection()
            idx = max(0, min(len(shown) - 1, (sel[0] if sel else 0) + delta))
            lb.selection_clear(0, "end")
            lb.selection_set(idx)
            lb.activate(idx)
            lb.see(idx)
            return "break"

        entry.bind("<KeyRelease>", lambda e: self._debounce("gsearch", 250, fill) if e.keysym not in
                   ("Up", "Down", "Return", "KP_Enter", "Escape") else None)
        entry.bind("<Down>", lambda e: move(1))
        entry.bind("<Up>", lambda e: move(-1))
        entry.bind("<Return>", choose)
        lb.bind("<Return>", choose)
        lb.bind("<Double-Button-1>", choose)
        dlg.bind("<Escape>", lambda e: dlg.destroy())

        todo = [g for g in self.ids if g not in self.installed_cache]  # проверка в фоне — окно открывается сразу
        results: queue.Queue = queue.Queue()
        if todo:
            threading.Thread(target=self._scan_installed, args=(todo, results), daemon=True).start()

        def poll():
            if not dlg.winfo_exists():
                return
            changed = False
            try:
                while True:
                    g, ok = results.get_nowait()
                    self.installed_cache[g] = ok
                    changed = True
            except queue.Empty:
                pass
            if changed:
                fill(keep=current())
            if any(g not in self.installed_cache for g in todo):
                dlg.after(150, poll)

        fill()
        poll()
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        dlg.geometry(f"+{max(x, 0)}+{self.root.winfo_rooty() + 90}")
        self.root.after(80, lambda: set_titlebar_dark(dlg, c["is_dark"]))
        dlg.grab_set()
        entry.focus_force()

    def _scan_installed(self, gids: list[str], out: queue.Queue):
        for gid in gids:
            prof = self.profiles[gid]
            saved = self.cfg.get("games", {}).get(gid, {}).get("path", "")
            ok = bool(saved and is_game_dir(prof, Path(saved))) or bool(find_game_dir(prof))
            out.put((gid, ok))

    def load_game_path(self, autodetect: bool = False):
        prof = self.profile
        if prof is None:
            self.game_var.set("")
        else:
            saved = self.cfg.get("games", {}).get(self.gid, {}).get("path", "")
            if saved and Path(saved).is_dir():
                self.game_var.set(saved)
            else:
                self.game_var.set("")
                if autodetect:
                    self._autodetect_async(self.gid)
            if self.game_var.get():
                self.installed_cache[self.gid] = True
        self.update_game_status()
        if prof is not None and not self.game_var.get() and autodetect:
            self.status_pill.set(T("recent_scanning"), "muted")
        self.refresh_items()
        self.refresh_deps()

    def _autodetect_async(self, gid: str, manual: bool = False):
        prof = dict(self.profiles[gid])

        def work():
            self.q.put(("autodetected", (gid, find_game_dir(prof), manual)))
        threading.Thread(target=work, daemon=True).start()

    def remember_path(self):
        if not self.gid:
            return
        p = self.game_var.get().strip().strip('"')
        self.cfg.setdefault("games", {}).setdefault(self.gid, {})["path"] = p
        self.save_cfg_soon()

    def apply_path_edit(self):
        if self.game_path():
            self.remember_path()
        self.update_game_status()
        self.refresh_items()
        self.refresh_deps()

    def pick_game_dir(self):
        if not self._need_game():
            return
        p = filedialog.askdirectory(title=T("dlg_folder", game=self.profile["name"]))
        if p:
            self.game_var.set(os.path.normpath(p))
            self.apply_path_edit()

    def autodetect(self):
        if self._need_game():
            self.status_pill.set(T("recent_scanning"), "muted")
            self._autodetect_async(self.gid, manual=True)

    def _set_path_enabled(self, enabled: bool):
        flag = ["!disabled"] if enabled else ["disabled"]
        for w in (self.path_entry, self.browse_btn, self.detect_btn):
            w.state(flag)

    def update_game_status(self):
        prof = self.profile
        if prof is None:
            self._set_path_enabled(False)
            self.status_pill.set(T("st_no_game"), "muted")
            self._set_note("")
            self.strip.grid_remove()
            self._set_hint("")
            self.vanilla_on = False
            self.draw_drop()
            return
        self._set_path_enabled(True)
        gp = self.game_path()
        if gp and is_game_dir(prof, gp):
            self.status_pill.set(T("st_ok"), "ok")
        elif gp:
            self.status_pill.set(T("st_nodetect"), "warn")
        else:
            self.status_pill.set(T("st_nofolder"), "err")
        note = prof.get("note", "")
        self._set_note(("ℹ " + T(note)) if note else "")
        self._set_hint(T("hint_unmanaged") if prof["mode"] == "folder" else "")
        self.strip.grid()
        self._apply_strip_visibility()
        self.update_vanilla_ui()

    def _set_hint(self, text: str):
        hl = getattr(self, "hint_label", None)
        if hl is not None and hl.winfo_exists():
            hl.configure(text=text)

    def _apply_strip_visibility(self):
        if self.settings["show_vanilla"]:
            self.vanilla_box.grid()
        else:
            self.vanilla_box.grid_remove()
        self._apply_deps_ui()

    # ---------------------------------------------------- доктор зависимостей
    def refresh_deps(self):
        prof, gp = self.profile, self.game_path()
        self._deps = None
        if not (prof and gp and prof.get("deps")):
            self._apply_deps_ui()
            return
        self._apply_deps_ui()

        def work(p=dict(prof), g=gp):
            self.q.put(("deps", (p["id"], str(g), check_deps(p, g))))
        threading.Thread(target=work, daemon=True).start()

    def _apply_deps_ui(self):
        prof, gp = self.profile, self.game_path()
        if not (self.settings["show_doctor"] and prof and gp and prof.get("deps")):
            self.doctor_pill.grid_remove()
            return
        self.doctor_pill.grid()
        if self._deps is None:
            self.doctor_pill.set(T("doc_checking"), "muted")
            return
        missing = [d for d, ok in self._deps if not ok]
        if missing:
            shown = missing[0] + (f" +{len(missing) - 1}" if len(missing) > 1 else "")
            shown = shown if len(shown) <= 24 else shown[:22] + "…"
            self.doctor_pill.set(T("doc_missing", items=shown), "warn")
        else:
            self.doctor_pill.set(T("doc_ok"), "ok")

    def show_deps_detail(self):
        if self._deps is None:
            return
        missing = [d for d, ok in self._deps if not ok]
        if missing:
            messagebox.showwarning(T("doc_title"), T("doc_detail_missing", items="\n".join("• " + m for m in missing)))
        else:
            messagebox.showinfo(T("doc_title"), T("doc_detail_ok", items="\n".join("• " + d for d, _ in self._deps)))

    # ----------------------------------------------------------- ванильный режим
    def _vanilla_active(self) -> bool:
        return bool(getattr(self, "vanilla_on", False))

    def update_vanilla_ui(self):
        """Тумблер всегда показывает реальное сохранённое состояние для (игра, папка)."""
        prof, gp = self.profile, self.game_path()
        self.vanilla_on = bool(prof and gp and vanilla_state(prof["id"], gp))
        self.vanilla_switch.set(self.vanilla_on)
        if self.vanilla_on:
            self.vanilla_pill.pack(side="left", padx=8)
        else:
            self.vanilla_pill.pack_forget()
        if prof and gp:  # дублируем флаг в config.json
            flags = self.cfg.setdefault("vanilla", {}).setdefault(prof["id"], {})
            if flags.get(game_hash(gp)) != self.vanilla_on:
                flags[game_hash(gp)] = self.vanilla_on
                self.save_cfg_soon()
        self.draw_drop()

    def toggle_vanilla(self, want: bool):
        prof, gp = self.profile, self.game_path()
        if prof is None or gp is None:
            messagebox.showinfo(APP_NAME, T("vanilla_need_folder"))
            self.vanilla_switch.set(self._vanilla_active())
            return
        if self.busy:
            messagebox.showinfo(APP_NAME, T("msg_busy"))
            self.vanilla_switch.set(self._vanilla_active())
            return
        if want:
            if not _game_records(prof["id"], gp):
                messagebox.showinfo(APP_NAME, T("vanilla_nothing"))
                self.vanilla_switch.set(False)
                return
            if not messagebox.askyesno(APP_NAME, T("vanilla_confirm")):
                self.vanilla_switch.set(False)
                return
        self.busy = True
        self.status_var.set(T("vanilla_busy"))
        threading.Thread(target=self._vanilla_worker, args=(dict(prof), gp, want), daemon=True).start()

    def _vanilla_worker(self, prof: dict, gp: Path, want: bool):
        try:
            if want:
                vanilla_enable(prof, gp)
                mods = len((vanilla_state(prof["id"], gp) or {}).get("mods", []))
                self.log(T("log_vanilla_on", n=mods))
            else:
                mods = len((vanilla_state(prof["id"], gp) or {}).get("mods", []))
                vanilla_disable(prof, gp)
                self.log(T("log_vanilla_off", n=mods))
        except ModError as e:
            msg = T(e.key, **e.kw)
            self.log("⚠ " + msg.lstrip("✖⚠ "))
            self.q.put(("vanilla_error", msg.lstrip("✖⚠ ")))
        except Exception as e:
            self.log(f"✖ {e}")
            self.q.put(("vanilla_error", T("vanilla_error", err=e)))
        finally:
            self.q.put(("vanilla_done", None))  # тумблер синхронизируется с реальным состоянием

    # ------------------------------------------------------------ свежие загрузки
    def refresh_recents(self):
        if not self.settings["show_downloads"]:
            return
        self.recent_loaded = False
        self._render_recents()

        def work():
            self.q.put(("recents", recent_archives(get_downloads_dir(), 4)))
        threading.Thread(target=work, daemon=True).start()

    def _render_recents(self):
        fr = self.recent_frame
        if fr is None or not fr.winfo_exists():
            return
        for w in fr.winfo_children():
            w.destroy()
        if not self.recent_loaded or not self.recent_items:
            ttk.Label(fr, text=T("recent_empty") if self.recent_loaded else T("recent_scanning"),
                      style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", pady=4)
            return
        for i, it in enumerate(self.recent_items):  # компактные строки: до 4 штук в 112 px
            row = ttk.Frame(fr, style="Card.TFrame")
            row.grid(row=i, column=0, sticky="ew", pady=1)
            row.columnconfigure(0, weight=1)
            name = it["path"].name
            ttk.Label(row, text=name if len(name) <= 30 else name[:27] + "…", style="Card.TLabel").grid(row=0, column=0, sticky="w")
            info = f"{it['size'] / 1048576:.0f} MB · {datetime.fromtimestamp(it['mtime']).strftime('%d.%m %H:%M')}"
            ttk.Label(row, text=info, style="CardMuted.TLabel").grid(row=0, column=1, padx=8)
            ttk.Button(row, text=T("btn_install"), style="MiniAccent.TButton",
                       command=lambda p=it["path"]: self.start_install([p])).grid(row=0, column=2)

    # ------------------------------------------------------------ установка
    def pick_archives(self):
        if not self._need_game():
            return
        files = filedialog.askopenfilenames(
            title=T("dlg_archives"),
            filetypes=[(T("ft_archives"), "*.zip *.7z *.rar *.gmpack.zip"), (T("ft_all"), "*.*")])
        if files:
            self.start_install([Path(f) for f in files])

    def on_drop_enter(self, event):
        self._set_hover(True)
        return event.action

    def on_drop_leave(self, event):
        self._set_hover(False)
        return event.action

    def on_drop(self, event):
        self._set_hover(False)
        if self._need_game():
            self.start_install([Path(f) for f in self.root.tk.splitlist(event.data)])
        return event.action

    def start_install(self, files: list[Path]):
        if not self._need_game():
            return
        if self._vanilla_active():
            if messagebox.askyesno(APP_NAME, T("msg_vanilla_blocked")):
                self.vanilla_switch.set(False)
                self.toggle_vanilla(False)
            return
        if self.busy:
            messagebox.showinfo(APP_NAME, T("msg_busy"))
            return
        valid = []
        for f in files:
            if is_supported_source(f):
                valid.append(f)
            else:
                self.log(T("log_skip", name=f.name))
        if not valid:
            return
        game = self.game_path()
        if not game:
            messagebox.showerror(APP_NAME, T("msg_folder"))
            return
        if not is_game_dir(self.profile, game) and \
                not messagebox.askyesno(APP_NAME, T("msg_nodetect", game=self.profile["name"])):
            return
        self.remember_path()
        use_preview = self.settings["preview_tree"] or self._shift
        self.busy = True
        if self.progress is not None:
            self.progress.configure(maximum=len(valid), value=0)
        threading.Thread(target=self._worker, args=(dict(self.profile), valid, game, use_preview,
                                                    dict(self.settings)), daemon=True).start()

    def _worker(self, profile: dict, files: list[Path], game: Path, use_preview: bool, settings: dict):
        ok, total = 0, len(files)
        for i, f in enumerate(files):
            self.q.put(("progress", (i, total, f.name)))
            self.log(f"▶ {f.name}")
            try:
                if install_source(profile, f, game, self.log, self.confirm_conflicts,
                                  self.preview_files if use_preview else None, settings):
                    ok += 1
            except ModError as e:
                self.log("   " + T(e.key, **e.kw))
            except PermissionError:
                self.log("   " + T("err_perm"))
            except Exception as e:
                self.log(f"   ✖ {e}")
        self.q.put(("progress", (total, total, "")))
        self.log(T("log_done_ok" if ok == total else "log_done_part", ok=ok, n=total))
        self.log("")
        self.q.put(("done", (ok, total)))

    # --------------------------------------------------------- установленные моды
    def refresh_items(self):
        items: list[dict] = []
        prof, gp = self.profile, self.game_path()
        if prof and gp:
            key = os.path.normcase(str(gp))
            known: set[str] = set()
            for r in db_records(self.gid):
                if os.path.normcase(r.get("game", "")) == key:
                    items.append({"label": r["name"], "kind": "rec", "ref": r})
                    known |= {d.lower() for d in r.get("dirs", [])}
            target = prof.get("target", "")
            if prof["mode"] == "folder" and target and (gp / target).is_dir():
                for d in sorted((x for x in (gp / target).iterdir() if x.is_dir()), key=lambda x: x.name.lower()):
                    if d.name.lower() not in known:
                        items.append({"label": d.name + " *", "kind": "dir", "ref": d})
        items.sort(key=lambda it: it["label"].lower())
        self.items = items
        self._fill_list()

    def _fill_list(self):
        q = self.search_var.get().strip().lower()
        self.shown = [it for it in self.items if q in it["label"].lower()]
        total = len(self.items)
        self.count_var.set(f"{len(self.shown)}/{total}" if q else str(total))
        if self.badge is not None and self.badge.winfo_exists():
            self.badge.set(self.count_var.get())
        if self.listbox is None or not self.listbox.winfo_exists():
            return
        self.listbox.delete(0, "end")
        muted = THEMES[self.theme]["muted"]
        for i, it in enumerate(self.shown):
            self.listbox.insert("end", "  " + it["label"])
            if it["kind"] == "dir":
                self.listbox.itemconfig(i, foreground=muted)

    def _selected(self) -> list[dict]:
        if self.listbox is None:
            return []
        return [self.shown[i] for i in self.listbox.curselection()]

    def delete_selected(self):
        sel, gp, prof = self._selected(), self.game_path(), self.profile
        if not sel or not gp or prof is None:
            return
        if self._vanilla_active():
            messagebox.showinfo(APP_NAME, T("err_vanilla_active").lstrip("✖ "))
            return
        if not messagebox.askyesno(APP_NAME, T("confirm_delete", names="\n".join(s["label"] for s in sel))):
            return
        for it in sel:
            try:
                if it["kind"] == "rec":
                    uninstall_record(prof, gp, it["ref"])
                    db_remove(self.gid, it["ref"]["id"])
                else:
                    shutil.rmtree(it["ref"])
                self.log(T("log_deleted", name=it["label"].rstrip(" *")))
            except Exception as e:
                self.log(T("log_delete_fail", name=it["label"], err=e))
        self.refresh_items()

    def open_selected(self):
        sel, gp, prof = self._selected(), self.game_path(), self.profile
        if not sel or not gp or prof is None:
            return
        it = sel[0]
        if it["kind"] == "dir":
            os.startfile(it["ref"])  # Windows only
        else:
            target = gp / prof.get("target", "")
            dirs = it["ref"].get("dirs", [])
            os.startfile(target / dirs[0] if dirs and (target / dirs[0]).is_dir() else target)

    def open_target(self):
        gp, prof = self.game_path(), self.profile
        if gp and prof:
            t = gp / prof.get("target", "")
            os.startfile(t if t.is_dir() else gp)

    def _mods_context_menu(self, event):
        idx = self.listbox.nearest(event.y)
        if idx < 0 or idx >= len(self.shown):
            return
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(idx)
        menu = self._menu()
        menu.add_command(label=T("ctx_configs"), command=self.open_configs_for_selected)
        menu.add_command(label=T("open_dir"), command=self.open_selected)
        menu.add_separator()
        menu.add_command(label=T("delete"), command=self.delete_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------ инструменты
    def open_tools_menu(self):
        menu = self._menu()
        menu.add_command(label=T("saves_backup"), command=self.backup_saves_now)
        menu.add_command(label=T("saves_restore"), command=self.open_restore_saves)
        menu.add_separator()
        menu.add_command(label=T("pack_export"), command=self.export_pack)
        self._popup(menu, self.tools_btn)

    def backup_saves_now(self):
        prof = self.profile
        if not self._need_game():
            return
        if not prof.get("saves"):
            messagebox.showinfo(APP_NAME, T("saves_none"))
            return
        limit = self.settings["save_backup_limit"]

        def work(p=dict(prof)):
            res = backup_saves(p, limit)
            self.log(T("saves_done", name=res[0].name, n=res[1]) if res else T("saves_empty"))
        threading.Thread(target=work, daemon=True).start()

    def open_restore_saves(self):
        prof = self.profile
        if not self._need_game():
            return
        if not prof.get("saves"):
            messagebox.showinfo(APP_NAME, T("saves_none"))
            return
        c = THEMES[self.theme]
        dlg = tk.Toplevel(self.root)
        dlg.title(T("saves_restore_title"))
        dlg.configure(bg=c["bg"])
        dlg.transient(self.root)
        frm = ttk.Frame(dlg, padding=18)
        frm.pack(fill="both", expand=True)
        snaps = list_save_backups(prof["id"])
        lb = tk.Listbox(frm, height=10, width=62, activestyle="none", relief="flat", bd=0, highlightthickness=0,
                        font=(FONT, 10), bg=c["surface2"], fg=c["fg"], selectbackground=c["accent"],
                        selectforeground=c["on_accent"])
        lb.pack(fill="both", expand=True)
        for p in snaps:
            lb.insert("end", f"  {p.stem}    {p.stat().st_size / 1048576:.1f} MB")
        if not snaps:
            lb.insert("end", "  " + T("saves_no_snaps"))
        else:
            lb.selection_set(0)

        def restore():
            sel = lb.curselection()
            if not snaps or not sel:
                return
            snap = snaps[sel[0]]
            if not messagebox.askyesno(APP_NAME, T("saves_restore_confirm", name=snap.stem), parent=dlg):
                return
            dlg.destroy()
            limit = self.settings["save_backup_limit"]

            def work(p=dict(prof)):
                try:
                    self.log(T("saves_restored", name=snap.stem, n=restore_save_backup(p, snap, limit)))
                except Exception as e:
                    self.log(f"✖ {e}")
            threading.Thread(target=work, daemon=True).start()

        bf = ttk.Frame(frm)
        bf.pack(fill="x", pady=(14, 0))
        ttk.Button(bf, text=T("dlg_cancel"), command=dlg.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(bf, text=T("saves_restore_btn"), style="Accent.TButton", command=restore).pack(side="right")
        self.root.after(80, lambda: set_titlebar_dark(dlg, c["is_dark"]))
        dlg.grab_set()

    def export_pack(self):
        prof, gp = self.profile, self.game_path()
        if not self._need_game():
            return
        if not gp or not _game_records(prof["id"], gp):
            messagebox.showinfo(APP_NAME, T("err_pack_empty").lstrip("✖ "))
            return
        safe = re.sub(r'[\\/:*?"<>|]+', "_", prof["name"]).strip() or prof["id"]
        out = filedialog.asksaveasfilename(
            title=T("pack_export"), defaultextension=PACK_SUFFIX, initialfile=f"{safe}_modpack{PACK_SUFFIX}",
            filetypes=[(T("ft_packs"), "*" + PACK_SUFFIX)])
        if not out:
            return

        def work(p=dict(prof), g=gp, o=Path(out)):
            try:
                self.log(T("pack_exported", path=str(o), n=export_gmpack(p, g, o)))
            except ModError as e:
                self.log(T(e.key, **e.kw))
            except Exception as e:
                self.log(f"✖ {e}")
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- настройки
    def open_settings(self):
        old = getattr(self, "_settings_dlg", None)
        if old is not None and old.winfo_exists():
            old.lift()
            return
        c = THEMES[self.theme]
        dlg = tk.Toplevel(self.root)
        self._settings_dlg = dlg
        dlg.title(T("set_title"))
        dlg.configure(bg=c["bg"])
        dlg.transient(self.root)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=22)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)
        ttk.Label(frm, text="⚙ " + T("set_title"), style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        rows = (("show_downloads", "set_downloads", "set_downloads_d"), ("show_log", "set_log", "set_log_d"),
                ("show_doctor", "set_doctor", "set_doctor_d"),
                ("show_vanilla", "set_vanilla", "set_vanilla_d"), ("autobackup_saves", "set_autobackup", "set_autobackup_d"),
                ("preview_tree", "set_preview", "set_preview_d"))
        r = 1
        for key, title, desc in rows:
            box = ttk.Frame(frm)
            box.grid(row=r, column=0, sticky="w", pady=7)
            ttk.Label(box, text=T(title), font=(FONT, 11, "bold")).pack(anchor="w")
            ttk.Label(box, text=T(desc), style="Muted.TLabel").pack(anchor="w")
            ToggleSwitch(frm, value=self.settings[key], surface="bg",
                         command=lambda v, k=key: self.set_setting(k, v)).grid(row=r, column=1, padx=(30, 0))
            r += 1
        lim = ttk.Frame(frm)
        lim.grid(row=r, column=0, sticky="w", pady=7)
        ttk.Label(lim, text=T("set_limit"), font=(FONT, 11, "bold")).pack(anchor="w")
        var = tk.IntVar(value=self.settings["save_backup_limit"])

        def apply_limit(*_):
            try:
                self.set_setting("save_backup_limit", max(1, min(50, int(var.get()))))
            except (tk.TclError, ValueError):
                pass
        spin = ttk.Spinbox(frm, from_=1, to=50, width=5, textvariable=var, command=apply_limit)
        spin.grid(row=r, column=1, padx=(30, 0))
        spin.bind("<FocusOut>", apply_limit)
        spin.bind("<Return>", apply_limit)
        r += 1
        cm = ttk.Frame(frm)
        cm.grid(row=r, column=0, columnspan=2, sticky="w", pady=(12, 4))
        ttk.Label(cm, text=T("set_conflicts"), font=(FONT, 11, "bold")).pack(anchor="w")
        mode = tk.StringVar(value=self.settings["conflict_mode"])
        for value, label in (("ask", "set_conflicts_ask"), ("warn", "set_conflicts_warn")):
            ttk.Radiobutton(cm, text=T(label), variable=mode, value=value,
                            command=lambda: self.set_setting("conflict_mode", mode.get())).pack(anchor="w", pady=2)
        r += 1
        ttk.Button(frm, text=T("set_close"), style="Accent.TButton", command=dlg.destroy).grid(
            row=r, column=0, columnspan=2, sticky="e", pady=(18, 0))
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        dlg.geometry(f"+{max(x, 0)}+{self.root.winfo_rooty() + 60}")
        self.root.after(80, lambda: set_titlebar_dark(dlg, c["is_dark"]))
        dlg.grab_set()

    def set_setting(self, key: str, value):
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        self.cfg["settings"] = self.settings
        self.save_cfg_soon()
        if key == "show_downloads":
            self.apply_top_tabs()
            self._sync_edit_switch("recent", value)
        elif key == "show_log":
            self.build_bottom()
            self._sync_edit_switch("log", value)
        elif key == "show_doctor":
            self._apply_deps_ui()
        elif key == "show_vanilla":
            self._apply_strip_visibility()

    # ------------------------------------------------------ предпросмотр установки
    def show_preview_dialog(self, entries) -> set[str] | None:
        """Дерево файлов с галочками: что и куда ляжет. None = отмена, иначе множество исключённых путей."""
        c = THEMES[self.theme]
        dlg = tk.Toplevel(self.root)
        dlg.title(T("prev_title"))
        dlg.configure(bg=c["bg"])
        dlg.transient(self.root)
        dlg.geometry("820x560")
        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill="both", expand=True)
        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)
        ttk.Label(frm, text=T("prev_hint"), style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        tree = ttk.Treeview(frm, columns=("src",), show="tree headings", selectmode="browse")
        tree.heading("#0", text=T("prev_col_dest"), anchor="w")
        tree.heading("src", text=T("prev_col_src"), anchor="w")
        tree.column("#0", width=430)
        tree.column("src", width=300)
        tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frm, orient="vertical", command=tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=sb.set)

        meta: dict[str, dict] = {}
        dirs: dict[str, str] = {}
        for src, dest in sorted(entries, key=lambda e: e[1].lower()):
            parts, parent = dest.split("/"), ""
            for depth in range(len(parts) - 1):
                key = "/".join(parts[:depth + 1])
                if key not in dirs:
                    iid = tree.insert(parent, "end", text="", open=True)
                    dirs[key] = iid
                    meta[iid] = {"kind": "dir", "name": parts[depth], "parent": parent, "children": []}
                    if parent:
                        meta[parent]["children"].append(iid)
                parent = dirs[key]
            iid = tree.insert(parent, "end", text="", values=(src,))
            meta[iid] = {"kind": "file", "name": parts[-1], "dest": dest, "on": True, "parent": parent}
            if parent:
                meta[parent]["children"].append(iid)
        leaves = [m for m in meta.values() if m["kind"] == "file"]
        count_var = tk.StringVar()

        def leaves_of(iid):
            m = meta[iid]
            return [m] if m["kind"] == "file" else [x for ch in m["children"] for x in leaves_of(ch)]

        def mark(iid):
            m = meta[iid]
            if m["kind"] == "file":
                sym = "☑" if m["on"] else "☐"
            else:
                states = [x["on"] for x in leaves_of(iid)]
                sym = "☑" if all(states) else ("☐" if not any(states) else "◪")
            tree.item(iid, text=f"{sym}  {m['name']}")

        def refresh_all():
            for iid in meta:
                mark(iid)
            count_var.set(T("prev_count", n=sum(1 for x in leaves if x["on"]), total=len(leaves)))

        def toggle(iid):
            if not iid:
                return
            target = not all(x["on"] for x in leaves_of(iid))
            for x in leaves_of(iid):
                x["on"] = target
            refresh_all()

        tree.bind("<Button-1>", lambda e: toggle(tree.identify_row(e.y)) if tree.identify_region(e.x, e.y) in ("tree", "cell") else None)
        tree.bind("<space>", lambda e: toggle(tree.focus()))
        refresh_all()

        result = {"v": None}

        def ok():
            result["v"] = {x["dest"] for x in leaves if not x["on"]}
            dlg.destroy()

        bf = ttk.Frame(frm)
        bf.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Label(bf, textvariable=count_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(bf, text=T("dlg_cancel"), command=dlg.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(bf, text=T("prev_install"), style="Accent.TButton", command=ok).pack(side="right")
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        self.root.after(80, lambda: set_titlebar_dark(dlg, c["is_dark"]))
        dlg.grab_set()
        dlg.wait_window()
        return result["v"]

    # ------------------------------------------------------------ редактор конфигов
    _HL_RULES = {
        "ini": [("comment", r"(?m)^\s*[;#].*$"), ("section", r"(?m)^\s*\[[^\]\n]+\]"),
                ("key", r"(?m)^\s*[^\s;#=\[][^=\n]*?(?=\s*=)"), ("bool", r"(?i)\b(?:true|false)\b")],
        "json": [("key", r'"(?:[^"\\\n]|\\.)*"(?=\s*:)'), ("string", r'"(?:[^"\\\n]|\\.)*"'),
                 ("number", r"-?\b\d+(?:\.\d+)?\b"), ("bool", r"\b(?:true|false|null)\b")],
        "xml": [("comment", r"(?s)<!--.*?-->"), ("tag", r"</?[\w:.-]+|/?>"), ("string", r'"[^"\n]*"')],
    }

    def open_configs_for_selected(self):
        sel, gp = self._selected(), self.game_path()
        if not sel or not gp:
            return
        it = sel[0]
        files = find_mod_configs(gp, it["ref"].get("files", [])) if it["kind"] == "rec" else scan_folder_configs(it["ref"])
        base = gp if it["kind"] == "rec" else it["ref"].parent
        self.open_config_editor(files, base, it["label"].rstrip(" *"))

    def open_config_editor(self, files: list[Path], base: Path, title: str):
        if not files:
            messagebox.showinfo(APP_NAME, T("cfg_none"))
            return
        c = THEMES[self.theme]
        dlg = tk.Toplevel(self.root)
        dlg.title(T("cfg_title", name=title))
        dlg.configure(bg=c["bg"])
        dlg.transient(self.root)
        dlg.geometry("1020x640")
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)
        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)

        def label_of(p: Path) -> str:
            try:
                return p.relative_to(base).as_posix()
            except ValueError:
                return p.name
        top = ttk.Frame(frm)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text=T("cfg_file")).grid(row=0, column=0, padx=(0, 10))
        combo = ttk.Combobox(top, values=[label_of(p) for p in files], state="readonly")
        combo.grid(row=0, column=1, sticky="ew")
        status = ttk.Label(top, text="", style="Muted.TLabel")
        status.grid(row=0, column=3, padx=12)

        pan = ttk.PanedWindow(frm, orient="horizontal")
        pan.grid(row=1, column=0, sticky="nsew")
        left = ttk.Frame(pan, style="Panel.TFrame", padding=10)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text=T("cfg_form"), style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        canvas = tk.Canvas(left, highlightthickness=0, bd=0, bg=c["surface"])
        canvas.grid(row=1, column=0, sticky="nsew")
        vs = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        vs.grid(row=1, column=1, sticky="ns")
        canvas.configure(yscrollcommand=vs.set)
        form = ttk.Frame(canvas, style="Card.TFrame")
        win = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        right = ttk.Frame(pan, style="Panel.TFrame", padding=10)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        editor = tk.Text(right, wrap="none", undo=True, relief="flat", bd=0, highlightthickness=0, padx=10, pady=8,
                         font=("Consolas", 10), bg=c["surface2"], fg=c["fg"], insertbackground=c["fg"],
                         selectbackground=c["accent"], selectforeground=c["on_accent"])
        editor.grid(row=0, column=0, sticky="nsew")
        ys = ttk.Scrollbar(right, orient="vertical", command=editor.yview)
        ys.grid(row=0, column=1, sticky="ns")
        xs = ttk.Scrollbar(right, orient="horizontal", command=editor.xview)
        xs.grid(row=1, column=0, sticky="ew")
        editor.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        pan.add(left, weight=2)
        pan.add(right, weight=3)
        for tag, color in (("string", c["ok"]), ("number", c["warn"]), ("bool", c["err"]), ("comment", c["muted"]),
                           ("section", c["accent2"]), ("key", c["accent"]), ("tag", c["accent2"])):
            editor.tag_configure(tag, foreground=color)

        state = {"path": None, "nl": "\n", "enc": "utf-8"}

        def highlight():
            for tag in ("string", "number", "bool", "comment", "section", "key", "tag"):
                editor.tag_remove(tag, "1.0", "end")
            ext = state["path"].suffix.lower() if state["path"] else ""
            group = "ini" if ext in (".ini", ".cfg", ".toml") else ("json" if ext == ".json" else ("xml" if ext == ".xml" else None))
            text = editor.get("1.0", "end-1c")
            if not group or len(text) > 300_000:
                return
            for tag, rx in self._HL_RULES[group]:
                for m in re.finditer(rx, text):
                    editor.tag_add(tag, f"1.0+{m.start()}c", f"1.0+{m.end()}c")

        def apply_edit(line: int, value: str):
            text = editor.get("1.0", "end-1c")
            new = apply_ini_edits(text, {line: value})
            if new != text:
                y = editor.yview()[0]
                editor.delete("1.0", "end")
                editor.insert("1.0", new)
                editor.yview_moveto(y)
                highlight()

        def build_form():
            for w in form.winfo_children():
                w.destroy()
            ext = state["path"].suffix.lower()
            rows = parse_ini(editor.get("1.0", "end-1c")) if ext in (".ini", ".cfg") else []
            if not rows:
                ttk.Label(form, text=T("cfg_form_empty"), style="CardMuted.TLabel").grid(row=0, column=0, sticky="w")
                return
            form.columnconfigure(0, weight=1)
            for i, rw in enumerate(rows[:250]):
                name = f"{rw['section']} › {rw['key']}" if rw["section"] else rw["key"]
                ttk.Label(form, text=name, style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=4, padx=(0, 10))
                if rw["bool"]:
                    ToggleSwitch(form, value=rw["value"].strip().lower() == "true",
                                 command=lambda v, ln=rw["line"]: apply_edit(ln, "true" if v else "false")).grid(row=i, column=1, pady=2)
                else:
                    var = tk.StringVar(value=rw["value"])
                    e = ttk.Entry(form, textvariable=var, width=22)
                    e.grid(row=i, column=1, pady=2)
                    e.bind("<FocusOut>", lambda ev, v=var, ln=rw["line"]: apply_edit(ln, v.get()))
                    e.bind("<Return>", lambda ev, v=var, ln=rw["line"]: apply_edit(ln, v.get()))

        def load(_e=None):
            p = files[combo.current()]
            text, nl, enc = read_text_file(p)
            state.update(path=p, nl=nl, enc=enc)
            editor.delete("1.0", "end")
            editor.insert("1.0", text)
            editor.edit_reset()
            status.configure(text="")
            highlight()
            build_form()

        def save():
            p = state["path"]
            text = editor.get("1.0", "end-1c")
            err = validate_config_text(p, text)
            if err and not messagebox.askyesno(APP_NAME, T("cfg_invalid", err=err), parent=dlg):
                return
            try:
                write_text_file(p, text, state["nl"], state["enc"])
            except OSError as e:
                messagebox.showerror(APP_NAME, str(e), parent=dlg)
                return
            status.configure(text=T("cfg_saved", name=p.name))
            self.log(T("cfg_saved", name=p.name))

        editor.bind("<KeyRelease>", lambda e: self._debounce("hl", 250, highlight))
        combo.bind("<<ComboboxSelected>>", load)
        bf = ttk.Frame(frm)
        bf.grid(row=2, column=0, sticky="e", pady=(12, 0))
        ttk.Button(bf, text=T("set_close"), command=dlg.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(bf, text=T("cfg_save"), style="Accent.TButton", command=save).pack(side="right")
        combo.current(0)
        load()
        self.root.after(80, lambda: set_titlebar_dark(dlg, c["is_dark"]))
        dlg.grab_set()

    # ------------------------------------------------------------- конфликты
    def show_conflict_dialog(self, conflicts) -> bool:
        c = THEMES[self.theme]
        lines: list[str] = []
        for name, files in conflicts:
            lines.append(T("conflict_header", name=name))
            lines += [f"- {f}" for f in files[:15]]
            if len(files) > 15:
                lines.append(T("conflict_more", n=len(files) - 15))
            lines.append("")
        lines.append(T("conflict_question"))

        dlg = tk.Toplevel(self.root)
        dlg.title(T("conflict_title"))
        dlg.configure(bg=c["bg"])
        dlg.transient(self.root)
        dlg.minsize(520, 260)
        result = {"ok": False}
        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill="both", expand=True)
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        txt = tk.Text(frm, wrap="word", relief="flat", bd=0, highlightthickness=0, padx=10, pady=8,
                      height=min(len(lines) + 1, 18), width=70, font=(FONT, 10),
                      bg=c["surface2"], fg=c["fg"])
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")
        txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frm, orient="vertical", command=txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        txt.configure(yscrollcommand=sb.set)

        def answer(ok: bool):
            result["ok"] = ok
            dlg.destroy()

        bf = ttk.Frame(frm)
        bf.grid(row=1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(bf, text=T("btn_skip"), command=lambda: answer(False)).pack(side="left", padx=(0, 8))
        ttk.Button(bf, text=T("btn_overwrite"), style="Accent.TButton", command=lambda: answer(True)).pack(side="left")
        dlg.protocol("WM_DELETE_WINDOW", lambda: answer(False))
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        dlg.geometry(f"+{max(x, 0)}+{self.root.winfo_rooty() + 80}")
        self.root.after(80, lambda: set_titlebar_dark(dlg, THEMES[self.theme]["is_dark"]))
        dlg.grab_set()
        dlg.wait_window()
        return result["ok"]

    # ---------------------------------------------------------- своя игра
    def add_game_dialog(self):
        c = THEMES[self.theme]
        dlg = tk.Toplevel(self.root)
        dlg.title(T("dlg_title"))
        dlg.configure(bg=c["bg"])
        dlg.transient(self.root)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=18)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)
        name, mode = tk.StringVar(), tk.StringVar(value="folder")
        target, detect, markers, loose = (tk.StringVar() for _ in range(4))
        engine_names = [T("engine_custom")] + [ENGINE_LABELS[k] for k in ENGINE_ORDER[1:]]

        ttk.Label(frm, text=T("dlg_name")).grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=name, width=58).grid(row=1, column=0, sticky="ew", pady=(2, 10))
        ttk.Label(frm, text=T("engine_template")).grid(row=2, column=0, sticky="w")
        engine_combo = ttk.Combobox(frm, values=engine_names, state="readonly")
        engine_combo.set(engine_names[0])
        engine_combo.grid(row=3, column=0, sticky="ew", pady=(2, 10))
        ttk.Label(frm, text=T("dlg_mode")).grid(row=4, column=0, sticky="w")
        ttk.Radiobutton(frm, text=T("mode_folder"), variable=mode, value="folder").grid(row=5, column=0, sticky="w")
        ttk.Radiobutton(frm, text=T("mode_root"), variable=mode, value="root").grid(row=6, column=0, sticky="w", pady=(0, 10))
        row = 7
        saves = tk.StringVar()
        for label, var in ((T("dlg_target"), target), (T("dlg_loose"), loose),
                           (T("dlg_markers"), markers), (T("dlg_detect"), detect), (T("dlg_saves"), saves)):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(frm, textvariable=var, width=58).grid(row=row + 1, column=0, sticky="ew", pady=(2, 10))
            row += 2

        def engine_key() -> str:
            return ENGINE_ORDER[engine_names.index(engine_combo.get())]

        def on_engine(event=None):
            preset = ENGINE_PRESETS.get(engine_key())
            if preset:  # шаблон заполняет поля, дальше их можно править руками
                mode.set(preset["mode"])
                target.set(preset["target"])
                markers.set(", ".join(preset["markers"]))
                loose.set(format_loose(preset["loose"]))

        engine_combo.bind("<<ComboboxSelected>>", on_engine)

        def save():
            n = name.get().strip()
            if not n:
                messagebox.showerror(APP_NAME, T("dlg_need_name"), parent=dlg)
                return
            slug = re.sub(r"[^a-z0-9]+", "_", n.lower()).strip("_") or uuid.uuid4().hex[:8]
            tgt = target.get().strip().replace("\\", "/").strip("/")
            prof = {"id": "custom_" + slug, "name": n, "mode": mode.get(), "target": tgt,
                    "detect": [detect.get().strip()] if detect.get().strip() else [],
                    "markers": [m.strip() for m in markers.get().split(",") if m.strip()],
                    "loose": parse_loose(loose.get()), "folders": []}
            if saves.get().strip():
                prof["saves"] = [saves.get().strip()]
            if engine_key() != "custom":
                prof["engine"] = engine_key()
            if mode.get() == "folder" and tgt and "/" not in tgt:
                prof["wrappers"] = [tgt]
            prof = sanitize_profile(prof)
            if prof is None:
                messagebox.showerror(APP_NAME, T("dlg_invalid"), parent=dlg)
                return
            save_custom_profile(prof)
            self.profiles = load_profiles()
            self.gid = prof["id"]
            self.cfg["last_game"] = self.gid
            dlg.destroy()
            self.rebuild()
            self.load_game_path()

        bf = ttk.Frame(frm)
        bf.grid(row=row, column=0, sticky="e")
        ttk.Button(bf, text=T("dlg_cancel"), command=dlg.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(bf, text=T("dlg_save"), style="Accent.TButton", command=save).pack(side="left")
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_rooty() + 60
        dlg.geometry(f"+{max(x, 0)}+{y}")
        dlg.grab_set()
        self.root.after(80, lambda: set_titlebar_dark(dlg, THEMES[self.theme]["is_dark"]))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if tk is None:
        sys.exit("tkinter is not available — install Python with Tcl/Tk support")
    App().run()
