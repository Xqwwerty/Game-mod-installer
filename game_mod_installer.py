"""
Game Mod Installer  (v3.2)
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

import ctypes
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

try:
    import py7zr
except ImportError:
    py7zr = None

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except ImportError:
    TkinterDnD = None
    DND_FILES = None

APP_NAME = "Game Mod Installer"
APP_VERSION = "3.2"
FONT = "Segoe UI"
ARCHIVE_EXTS = {".zip", ".7z", ".rar"}

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
    for key in ("detect", "folders", "markers", "rooted", "paths"):
        v = p.get(key, [])
        if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            return None
    if not all(_safe_rel(x) for x in p.get("detect", []) + p.get("folders", [])):
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


def iter_op_files(op: Op):
    """(исходный файл, путь относительно папки игры) для каждого файла, который скопирует op."""
    for path in sorted(op.src.rglob("*")):
        rel = path.relative_to(op.src)
        if path.is_file() and not _skip_entry(op, rel, path):
            yield path, _join(op.dst, rel.as_posix())


def copy_op(tr: Tracker, op: Op) -> int:
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
        else:
            tr.copy(path, dest)
            n += 1
    return n


def plan_dest_files(plan: Plan) -> list[str]:
    out: list[str] = []
    for op in plan.ops:
        out += [rel for _, rel in iter_op_files(op)]
    out += [_join(dst, f.name) for f, dst in plan.loose]
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


def uninstall_record(profile: dict, game: Path, rec: dict) -> None:
    """Удаляет мод. Если файл потом перезаписал другой мод, бэкап «передаётся» ему по цепочке:
    vanilla -> A -> B: удаление A оставляет файл B, а B теперь восстановит vanilla."""
    gid = profile["id"]
    key = os.path.normcase(str(game))
    recs = [r for r in db_records(gid) if os.path.normcase(r.get("game", "")) == key]
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

    prot = protected_dirs(profile, game)
    dirs = sorted({(game / rel).parent for rel in rec["files"]}, key=lambda p: len(p.parts), reverse=True)
    for d in dirs:
        cur = d
        while cur not in prot and game in cur.parents and len(cur.relative_to(game).parts) > 1:
            try:
                cur.rmdir()  # удалится только если пустая
            except OSError:
                break
            cur = cur.parent
    shutil.rmtree(bdir, ignore_errors=True)


def install_source(profile: dict, src: Path, game: Path, log, confirm=None) -> bool:
    """confirm(conflicts) -> bool: вызывается при конфликте файлов с другим модом; False = пропустить."""
    gid = profile["id"]
    with tempfile.TemporaryDirectory(prefix="gmi_", ignore_cleanup_errors=True) as tmp:
        if src.is_dir():
            root, stem = src, src.name
        else:
            root, stem = Path(tmp), src.stem
            extract_archive(src, root)
        if not any(c for c in root.iterdir() if c.name.lower() not in JUNK_NAMES):
            raise ModError("err_empty")

        plan = make_plan(profile, root, stem)  # сначала план — вдруг ошибка, старую версию не трогаем

        game_key = os.path.normcase(str(game))
        old = next((r for r in db_records(gid)
                    if r.get("archive") == src.name and os.path.normcase(r.get("game", "")) == game_key), None)

        conflicts = find_conflicts(gid, game, plan, exclude_id=old["id"] if old else None)
        if conflicts and confirm is not None and not confirm(conflicts):
            log("   " + T("log_conflict_skip"))
            return False

        if old:
            uninstall_record(profile, game, old)
            db_remove(gid, old["id"])
            log("   " + T("log_update"))

        rec = {"id": uuid.uuid4().hex[:10], "name": stem, "archive": src.name, "game": str(game),
               "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
        tr = Tracker(game, BACKUP_DIR / gid / rec["id"])
        try:
            for op in plan.ops:
                n = copy_op(tr, op)
                log("   " + T("log_placed", dst=op.dst or "/", n=n))
            for f, dst in plan.loose:
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
        self.profiles = load_profiles()
        last = self.cfg.get("last_game")
        self.gid: str | None = last if last in self.profiles else None  # None = «игра не выбрана»

        self.root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry(self.cfg.get("geometry", "1100x800"))
        self.root.minsize(900, 660)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Control-o>", lambda e: self.pick_archives())
        self.root.bind("<Control-f>", lambda e: self.open_game_search())

        self.style = ttk.Style(self.root)
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.drop_hover = False
        self.log_lines: list[str] = []
        self.items: list[dict] = []
        self.shown: list[dict] = []
        self.installed_cache: dict[str, bool] = {}
        self.paned = None

        self.game_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value=T("ready"))
        self.count_var = tk.StringVar(value="0")

        self.apply_styles()
        self._build_ui()
        self._recolor()
        self.load_game_path(autodetect=True)
        self.root.after(150, self._restore_sash)
        self.root.after(100, self._poll_queue)

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

    def _remember_sash(self):
        try:
            w = self.paned.winfo_width()
            if w > 100:
                self.cfg["sash_ratio"] = round(self.paned.sashpos(0) / w, 3)
        except Exception:
            pass

    def _restore_sash(self, tries: int = 0):
        ratio = self.cfg.get("sash_ratio")
        if not ratio or self.paned is None:
            return
        try:
            w = self.paned.winfo_width()
            if w < 100 and tries < 10:
                self.root.after(100, lambda: self._restore_sash(tries + 1))
                return
            self.paned.sashpos(0, int(w * float(ratio)))
        except Exception:
            pass

    def on_close(self):
        self._remember_sash()
        self.cfg["geometry"] = self.root.geometry()
        save_json(CONFIG_FILE, self.cfg)
        self.root.destroy()

    def save_cfg(self):
        save_json(CONFIG_FILE, self.cfg)

    # ----------------------------------------------------------------- styles
    def apply_styles(self):
        c, s = THEMES[self.theme], self.style
        s.theme_use("clam")
        self.root.configure(bg=c["bg"])
        s.configure(".", background=c["bg"], foreground=c["fg"], bordercolor=c["border"],
                    darkcolor=c["bg"], lightcolor=c["bg"], troughcolor=c["surface2"],
                    focuscolor=c["bg"], font=(FONT, 10))
        s.configure("TFrame", background=c["bg"])
        s.configure("Card.TFrame", background=c["surface"])  # внутренние блоки карточек, без рамки
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
        s.configure("Ok.Card.TLabel", foreground=c["ok"])
        s.configure("Warn.Card.TLabel", foreground=c["warn"])
        s.configure("Err.Card.TLabel", foreground=c["err"])
        s.configure("TRadiobutton", background=c["bg"], foreground=c["fg"])
        s.map("TRadiobutton", background=[("active", c["bg"])], indicatorcolor=[("selected", c["accent"])])

        for name, bg, fg, hov in (
            ("TButton", c["surface2"], c["fg"], c["hover"]),
            ("Accent.TButton", c["accent"], c["on_accent"], c["accent_hover"]),
            ("Danger.TButton", c["surface2"], c["err"], c["hover"]),
            ("Soft.TButton", c["surface2"], c["accent2"], c["hover"]),
        ):
            s.configure(name, background=bg, foreground=fg, bordercolor=bg, lightcolor=bg, darkcolor=bg,
                        padding=(16, 8), relief="flat", focusthickness=0, font=(FONT, 10, "bold"))
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

    def _recolor(self):
        c = THEMES[self.theme]
        self.log_text.configure(bg=c["surface2"], fg=c["fg"], insertbackground=c["fg"],
                                selectbackground=c["accent"], selectforeground=c["on_accent"])
        self.log_text.tag_configure("head", foreground=c["accent2"], font=("Consolas", 10, "bold"))
        self.log_text.tag_configure("ok", foreground=c["ok"])
        self.log_text.tag_configure("warn", foreground=c["warn"])
        self.log_text.tag_configure("err", foreground=c["err"])
        self.listbox.configure(bg=c["surface2"], fg=c["fg"], selectbackground=c["accent"],
                               selectforeground=c["on_accent"])
        self.draw_drop()
        self._draw_badge()
        self.root.after(60, lambda: set_titlebar_dark(self.root, THEMES[self.theme]["is_dark"]))

    @staticmethod
    def _round_rect(cv, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    def open_theme_menu(self):
        c = THEMES[self.theme]
        menu = tk.Menu(self.root, tearoff=0, bg=c["surface"], fg=c["fg"], activebackground=c["accent"],
                       activeforeground=c["on_accent"], selectcolor=c["accent2"], bd=0, relief="flat",
                       font=(FONT, 10))
        var = tk.StringVar(value=self.theme)
        for name in THEME_ORDER:
            menu.add_radiobutton(label="  " + T("theme_" + name), variable=var, value=name,
                                 command=lambda n=name: self.set_theme(n))
        btn = self.theme_btn
        try:
            menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height() + 4)
        finally:
            menu.grab_release()

    def set_theme(self, name: str):
        if name not in THEMES or name == self.theme:
            return
        self.theme = name
        self.cfg["theme"] = name
        self.save_cfg()
        self.rebuild()

    def change_language(self, event=None):
        name = self.lang_combo.get()
        code = next((k for k, v in LANG_NAMES.items() if v == name), "en")
        I18N.lang = code
        self.cfg["lang"] = code
        self.save_cfg()
        self.status_var.set(T("ready"))
        self.rebuild()

    def rebuild(self):
        self._remember_sash()
        for w in self.root.winfo_children():
            w.destroy()
        self.apply_styles()
        self._build_ui()
        self._recolor()
        for line in self.log_lines:
            self._write_log(line)
        self.update_game_status()
        self.refresh_items()
        self.root.after(150, self._restore_sash)

    # --------------------------------------------------------------------- ui
    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=(22, 18, 22, 14))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)

        # --- шапка
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=T("subtitle"), style="Muted.TLabel").pack(side="left", padx=16, pady=(9, 0))
        self.theme_btn = ttk.Button(header, text="🎨  " + T("appearance"), style="Soft.TButton",
                                    command=self.open_theme_menu)
        self.theme_btn.pack(side="right")
        self.lang_combo = ttk.Combobox(header, values=list(LANG_NAMES.values()), state="readonly", width=12)
        self.lang_combo.set(LANG_NAMES.get(I18N.lang, "English"))
        self.lang_combo.pack(side="right", padx=10)
        self.lang_combo.bind("<<ComboboxSelected>>", self.change_language)

        # --- игра и папка
        card = ttk.Frame(outer, style="Panel.TFrame", padding=18)
        card.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        card.columnconfigure(1, weight=1)
        card.columnconfigure(0, minsize=72)
        ttk.Label(card, text=T("game"), style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.ids = list(self.profiles.keys())
        self.name_to_id = {self.profiles[i]["name"]: i for i in self.ids}
        self.game_combo = ttk.Combobox(card, values=self._all_names(), state="readonly", height=16)
        self.game_combo.set(self.profile["name"] if self.profile else T("choose_game"))
        self.game_combo.grid(row=0, column=1, sticky="ew", padx=(10, 8))
        self.game_combo.bind("<<ComboboxSelected>>", self.on_game_selected)
        ttk.Button(card, text="🔍  " + T("find_game"), style="Soft.TButton",
                   command=self.open_game_search).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(card, text=T("add_game"), command=self.add_game_dialog).grid(row=0, column=3)

        ttk.Label(card, text=T("folder"), style="CardTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.path_entry = ttk.Entry(card, textvariable=self.game_var)
        self.path_entry.grid(row=1, column=1, sticky="ew", padx=(10, 8), pady=(12, 0))
        self.path_entry.bind("<KeyRelease>", lambda e: self.on_path_edited())
        self.browse_btn = ttk.Button(card, text=T("browse"), command=self.pick_game_dir)
        self.browse_btn.grid(row=1, column=2, padx=(0, 8), pady=(12, 0))
        self.detect_btn = ttk.Button(card, text=T("detect"), command=self.autodetect)
        self.detect_btn.grid(row=1, column=3, pady=(12, 0))
        self.game_status = ttk.Label(card, style="CardMuted.TLabel")
        self.game_status.grid(row=2, column=0, columnspan=4, sticky="w", pady=(10, 0))
        self.note_label = ttk.Label(card, style="CardMuted.TLabel", justify="left")
        self.note_label.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
        card.bind("<Configure>", lambda e: self.note_label.configure(wraplength=max(e.width - 60, 200)))

        # --- зона перетаскивания
        self.drop = tk.Canvas(outer, height=132, highlightthickness=0, bd=0)
        self.drop.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        self.drop.bind("<Configure>", lambda e: self.draw_drop())
        self.drop.bind("<Button-1>", lambda e: self.pick_archives())
        self.drop.bind("<Enter>", lambda e: self._set_hover(True))
        self.drop.bind("<Leave>", lambda e: self._set_hover(False))
        if TkinterDnD:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<DropEnter>>", self.on_drop_enter)
            self.drop.dnd_bind("<<DropLeave>>", self.on_drop_leave)
            self.drop.dnd_bind("<<Drop>>", self.on_drop)

        # --- прогресс + главное действие
        pf = ttk.Frame(outer)
        pf.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        pf.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(pf, mode="determinate", maximum=1, value=0)
        self.progress.grid(row=0, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(pf, textvariable=self.status_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Button(pf, text=T("pick_archives"), style="Accent.TButton", command=self.pick_archives).grid(
            row=0, column=1, rowspan=2, padx=(14, 0))

        # --- журнал | разделитель | установленные моды
        self.paned = ttk.PanedWindow(outer, orient="horizontal")
        self.paned.grid(row=4, column=0, sticky="nsew", pady=(12, 0))

        log_card = ttk.Frame(self.paned, style="Panel.TFrame", padding=14)
        log_card.rowconfigure(1, weight=1)
        log_card.columnconfigure(0, weight=1)
        lh = ttk.Frame(log_card, style="Card.TFrame")
        lh.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Label(lh, text=T("log"), style="CardTitle.TLabel").pack(side="left")
        ttk.Button(lh, text=T("clear"), command=self.clear_log).pack(side="right")
        self.log_text = tk.Text(log_card, wrap="word", state="disabled", relief="flat", bd=0,
                                highlightthickness=0, padx=12, pady=10, font=("Consolas", 10))
        self.log_text.grid(row=1, column=0, sticky="nsew")
        lsb = ttk.Scrollbar(log_card, orient="vertical", command=self.log_text.yview)
        lsb.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=lsb.set)

        mods = ttk.Frame(self.paned, style="Panel.TFrame", padding=14)
        mods.rowconfigure(2, weight=1)
        mods.columnconfigure(0, weight=1)
        mh = ttk.Frame(mods, style="Card.TFrame")
        mh.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(mh, text=T("installed"), style="CardTitle.TLabel").pack(side="left")
        self.badge = tk.Canvas(mh, height=22, width=30, highlightthickness=0, bd=0)
        self.badge.pack(side="left", padx=10)
        sf = ttk.Frame(mods, style="Card.TFrame")
        sf.grid(row=1, column=0, columnspan=2, sticky="ew", pady=10)
        sf.columnconfigure(1, weight=1)
        ttk.Label(sf, text=T("search"), style="CardMuted.TLabel").grid(row=0, column=0, padx=(0, 8))
        se = ttk.Entry(sf, textvariable=self.search_var)
        se.grid(row=0, column=1, sticky="ew")
        se.bind("<KeyRelease>", lambda e: self._fill_list())
        self.listbox = tk.Listbox(mods, selectmode="extended", activestyle="none", relief="flat", bd=0,
                                  highlightthickness=0, font=(FONT, 10))
        self.listbox.grid(row=2, column=0, sticky="nsew")
        msb = ttk.Scrollbar(mods, orient="vertical", command=self.listbox.yview)
        msb.grid(row=2, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=msb.set)
        self.listbox.bind("<Delete>", lambda e: self.delete_selected())
        self.listbox.bind("<Double-Button-1>", lambda e: self.open_selected())
        self.hint_label = ttk.Label(mods, text="", style="CardMuted.TLabel")
        self.hint_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        bf2 = ttk.Frame(mods, style="Card.TFrame")
        bf2.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(bf2, text=T("delete"), style="Danger.TButton", command=self.delete_selected).pack(side="left")
        ttk.Button(bf2, text=T("refresh"), command=self.refresh_items).pack(side="left", padx=8)
        ttk.Button(bf2, text=T("open_dir"), command=self.open_target).pack(side="left")

        self.paned.add(log_card, weight=3)
        self.paned.add(mods, weight=2)

        # --- подвал
        self.footer = ttk.Label(outer, style="Muted.TLabel")
        self.footer.grid(row=5, column=0, sticky="w", pady=(10, 0))
        backends = get_backends()
        parts = [T("footer_rar_ok", tool=backends[0][0]) if backends else T("footer_rar_no")]
        if not TkinterDnD:
            parts.append(T("footer_dnd_no"))
        parts.append(f"v{APP_VERSION}")
        self.footer.configure(text="   ·   ".join(parts))

    def _draw_badge(self):
        c, cv = THEMES[self.theme], self.badge
        cv.delete("all")
        text = self.count_var.get() or "0"
        w = max(30, 14 + 8 * len(text))
        cv.configure(width=w, bg=c["surface"])
        self._round_rect(cv, 1, 1, w - 1, 21, 10, fill=c["accent"], outline="")
        cv.create_text(w / 2, 11, text=text, fill=c["on_accent"], font=(FONT, 9, "bold"))

    def draw_drop(self):
        c, cv = THEMES[self.theme], self.drop
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 40:
            return
        ready = self.profile is not None
        hover = self.drop_hover and ready
        cv.configure(bg=c["bg"], cursor="hand2" if ready else "arrow")
        outline = (c["accent2"] if hover else c["accent"]) if ready else c["border"]
        self._round_rect(cv, 4, 4, w - 4, h - 4, 22, fill=c["surface2"] if hover else c["surface"],
                         outline=outline, dash=(9, 6), width=3 if hover else 2)
        if ready:
            title = T("drop_hover") if hover else (
                T("drop_title").replace("⬇", "").strip() if TkinterDnD else T("drop_nodnd"))
            sub = T("drop_sub")
        else:
            title, sub = T("drop_no_game"), T("drop_no_game_sub")
        cv.create_text(w / 2, h * 0.27, text="⬇" if ready else "🎮", font=(FONT, 20),
                       fill=c["accent2"] if hover else (c["accent"] if ready else c["muted"]))
        cv.create_text(w / 2, h * 0.55, text=title, font=(FONT, 14, "bold"),
                       fill=c["fg"] if ready else c["muted"])
        cv.create_text(w / 2, h * 0.79, text=sub, font=(FONT, 10), fill=c["muted"])

    def _set_hover(self, value: bool):
        self.drop_hover = value
        self.draw_drop()

    # ----------------------------------------------------------------- журнал
    def log(self, msg: str):
        self.q.put(("log", msg))

    def _write_log(self, msg: str):
        tag = {"▶": "head", "✔": "ok", "⚠": "warn", "✖": "err", "🗑": "warn", "↻": "warn"}.get(msg.lstrip()[:1])
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n", (tag,) if tag else ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_lines.clear()
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
                    self.progress.configure(maximum=max(total, 1), value=done)
                    if done < total:
                        self.status_var.set(T("installing", i=done + 1, n=total, name=name))
                elif kind == "confirm":
                    conflicts, ev, box = payload
                    try:
                        box["ok"] = self.show_conflict_dialog(conflicts)
                    finally:
                        ev.set()
                elif kind == "done":
                    ok, total = payload
                    self.busy = False
                    self.status_var.set(T("done_status", ok=ok, n=total))
                    self.refresh_items()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    # ------------------------------------------------------------- выбор игры
    def _all_names(self) -> list[str]:
        return [self.profiles[i]["name"] for i in self.ids]

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
        self.save_cfg()
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

        entry.bind("<KeyRelease>", lambda e: fill() if e.keysym not in
                   ("Up", "Down", "Return", "KP_Enter", "Escape") else None)
        entry.bind("<Down>", lambda e: move(1))
        entry.bind("<Up>", lambda e: move(-1))
        entry.bind("<Return>", choose)
        lb.bind("<Return>", choose)
        lb.bind("<Double-Button-1>", choose)
        dlg.bind("<Escape>", lambda e: dlg.destroy())

        # статусы «установлена» считаем в фоне, чтобы окно открывалось мгновенно
        todo = [g for g in self.ids if g not in self.installed_cache]
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
                dlg.after(120, poll)

        fill()
        poll()
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        dlg.geometry(f"+{max(x, 0)}+{self.root.winfo_rooty() + 90}")
        self.root.after(80, lambda: set_titlebar_dark(dlg, THEMES[self.theme]["is_dark"]))
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
            elif autodetect:
                self.game_var.set(find_game_dir(prof))
                if self.game_var.get():
                    self.remember_path()
            else:
                self.game_var.set("")
            if self.game_var.get():
                self.installed_cache[self.gid] = True
        self.update_game_status()
        self.refresh_items()
        self.draw_drop()

    def remember_path(self):
        if not self.gid:
            return
        p = self.game_var.get().strip().strip('"')
        self.cfg.setdefault("games", {}).setdefault(self.gid, {})["path"] = p
        self.save_cfg()

    def on_path_edited(self):
        if self.game_path():
            self.remember_path()
        self.update_game_status()
        self.refresh_items()

    def pick_game_dir(self):
        if not self._need_game():
            return
        p = filedialog.askdirectory(title=T("dlg_folder", game=self.profile["name"]))
        if p:
            self.game_var.set(os.path.normpath(p))
            self.remember_path()
            self.update_game_status()
            self.refresh_items()

    def autodetect(self):
        if not self._need_game():
            return
        found = find_game_dir(self.profile)
        if found:
            self.game_var.set(found)
            self.remember_path()
            self.installed_cache[self.gid] = True
        else:
            self.log(T("log_autodetect_fail"))
        self.update_game_status()
        self.refresh_items()

    def _set_path_enabled(self, enabled: bool):
        flag = ["!disabled"] if enabled else ["disabled"]
        for w in (self.path_entry, self.browse_btn, self.detect_btn):
            w.state(flag)

    def update_game_status(self):
        prof = self.profile
        if prof is None:
            self._set_path_enabled(False)
            self.game_status.configure(text=T("st_no_game"), style="CardMuted.TLabel")
            self.note_label.configure(text="")
            self.hint_label.configure(text="")
            return
        self._set_path_enabled(True)
        gp = self.game_path()
        if gp and is_game_dir(prof, gp):
            self.game_status.configure(text=T("st_ok"), style="Ok.Card.TLabel")
        elif gp:
            self.game_status.configure(text=T("st_nodetect"), style="Warn.Card.TLabel")
        else:
            self.game_status.configure(text=T("st_nofolder"), style="Err.Card.TLabel")
        note = prof.get("note", "")
        self.note_label.configure(text=("ℹ " + T(note)) if note else "")
        self.hint_label.configure(text=T("hint_unmanaged") if prof["mode"] == "folder" else "")

    # ------------------------------------------------------------ установка
    def pick_archives(self):
        if not self._need_game():
            return
        files = filedialog.askopenfilenames(
            title=T("dlg_archives"),
            filetypes=[(T("ft_archives"), "*.zip *.7z *.rar"), (T("ft_all"), "*.*")])
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
        if self.busy:
            messagebox.showinfo(APP_NAME, T("msg_busy"))
            return
        valid = []
        for f in files:
            if f.is_dir() or (f.is_file() and f.suffix.lower() in ARCHIVE_EXTS):
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
        self.busy = True
        self.progress.configure(maximum=len(valid), value=0)
        threading.Thread(target=self._worker, args=(dict(self.profile), valid, game), daemon=True).start()

    def _worker(self, profile: dict, files: list[Path], game: Path):
        ok, total = 0, len(files)
        for i, f in enumerate(files):
            self.q.put(("progress", (i, total, f.name)))
            self.log(f"▶ {f.name}")
            try:
                if install_source(profile, f, game, self.log, self.confirm_conflicts):
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
        self.listbox.delete(0, "end")
        muted = THEMES[self.theme]["muted"]
        for i, it in enumerate(self.shown):
            self.listbox.insert("end", "  " + it["label"])
            if it["kind"] == "dir":
                self.listbox.itemconfig(i, foreground=muted)
        total = len(self.items)
        self.count_var.set(f"{len(self.shown)}/{total}" if q else str(total))
        self._draw_badge()

    def _selected(self) -> list[dict]:
        return [self.shown[i] for i in self.listbox.curselection()]

    def delete_selected(self):
        sel, gp, prof = self._selected(), self.game_path(), self.profile
        if not sel or not gp or prof is None:
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

    # ------------------------------------------------------------- конфликты
    def confirm_conflicts(self, conflicts) -> bool:
        """Вызывается из рабочего потока: показывает диалог в GUI-потоке и ждёт ответ."""
        ev, box = threading.Event(), {"ok": False}
        self.q.put(("confirm", (conflicts, ev, box)))
        ev.wait()
        return box["ok"]

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
        for label, var in ((T("dlg_target"), target), (T("dlg_loose"), loose),
                           (T("dlg_markers"), markers), (T("dlg_detect"), detect)):
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
