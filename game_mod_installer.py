"""
Game Mod Installer  (v3)
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
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

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
APP_VERSION = "3.0"
FONT = "Segoe UI"
ARCHIVE_EXTS = {".zip", ".7z", ".rar"}

APPDATA = Path(os.environ.get("APPDATA", Path.home()))
CONFIG_DIR = APPDATA / "GameModInstaller"
CONFIG_FILE = CONFIG_DIR / "config.json"
DB_FILE = CONFIG_DIR / "installs.json"
GAMES_FILE = CONFIG_DIR / "games.json"
BACKUP_DIR = CONFIG_DIR / "backups"
LANG_DIR = CONFIG_DIR / "lang"
OLD_CONFIG_FILE = APPDATA / "RDR2ModInstaller" / "config.json"  # от v1/v2

DOC_EXTS = {".txt", ".md", ".pdf", ".url", ".rtf", ".docx", ".html", ".htm", ".jpg", ".jpeg", ".png", ".nfo"}
COMPANION_EXTS = {".ini", ".cfg", ".xml", ".json", ".toml", ".dat"}
JUNK_NAMES = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}

THEMES = {
    "dark": {
        "bg": "#1e1f22", "surface": "#2b2d31", "surface2": "#383a40", "hover": "#45484f",
        "fg": "#e3e5e8", "muted": "#9aa0a6", "accent": "#5865f2", "accent_hover": "#6d78f7",
        "border": "#4a4d55", "ok": "#57d28f", "warn": "#f0b232", "err": "#f26d6d",
    },
    "light": {
        "bg": "#f0f1f4", "surface": "#ffffff", "surface2": "#e8eaee", "hover": "#d9dce2",
        "fg": "#1f2328", "muted": "#6b7280", "accent": "#4f5bd5", "accent_hover": "#3f4ab8",
        "border": "#b8bec9", "ok": "#1a7f4b", "warn": "#b7791f", "err": "#c0392b",
    },
}

# =============================================================== профили игр ==
BETHESDA_MARKERS = [
    "meshes", "textures", "sound", "music", "scripts", "interface", "strings", "shaders", "seq",
    "grass", "lodsettings", "distantlod", "video", "facegen", "calientetools", "fonts",
    "materials", "geometries", "menus",
    "skse", "f4se", "nvse", "obse", "fose", "sfse", "mwse", "skyproc patchers",
    ".esp", ".esm", ".esl", ".bsa", ".ba2",
]


def _bethesda(gid, name, exe, folders, target="Data"):
    return dict(id=gid, name=name, mode="root", detect=exe, folders=folders, target=target,
                markers=BETHESDA_MARKERS, note="note_bethesda")


BUILTIN_GAMES: list[dict] = [
    dict(id="rdr2", name="Red Dead Redemption 2", mode="folder", detect=["RDR2.exe"],
         folders=["Red Dead Redemption 2", "RedDeadRedemption2"], target="lml", wrappers=["lml"],
         markers=["install.xml"], note="note_rdr2"),
    dict(id="gta5", name="Grand Theft Auto V", mode="root", detect=["GTA5.exe", "GTA5_Enhanced.exe"],
         folders=["Grand Theft Auto V", "Grand Theft Auto V Enhanced", "GTAV"], target="",
         markers=["scripts", "mods", "x64", "update", "plugins", ".asi"],
         loose={".asi": "", ".cs": "scripts", ".vb": "scripts"}, note="note_gta5"),
    dict(id="gtasa", name="Grand Theft Auto: San Andreas", mode="root",
         detect=["gta_sa.exe", "gta-sa.exe"], folders=["Grand Theft Auto San Andreas"], target="",
         markers=["cleo", "modloader", "models", "data", "text", "audio", "anim", ".asi"],
         loose={".asi": "", ".cs": "cleo"}, note="note_gtasa"),
    dict(id="cp2077", name="Cyberpunk 2077", mode="root", detect=["bin/x64/Cyberpunk2077.exe"],
         folders=["Cyberpunk 2077"], target="",
         markers=["archive", "bin", "r6", "red4ext", "engine", "mods"],
         loose={".archive": "archive/pc/mod", ".xl": "archive/pc/mod", ".reds": "r6/scripts",
                ".yaml": "r6/tweaks", ".yml": "r6/tweaks"},
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
         detect=["bin/x64/witcher3.exe", "bin/x64_dx12/witcher3.exe"],
         folders=["The Witcher 3", "The Witcher 3 Wild Hunt", "The Witcher 3 Wild Hunt GOTY",
                  "TheWitcher3WildHunt"],
         target="mods", wrappers=["mods", "dlc"], prefix_targets={"dlc": "dlc"},
         markers=["content"], note="note_witcher3"),
    dict(id="stardew", name="Stardew Valley", mode="folder", detect=["Stardew Valley.exe"],
         folders=["Stardew Valley"], target="Mods", wrappers=["Mods"],
         markers=["manifest.json"], note="note_stardew"),
    dict(id="bannerlord", name="Mount & Blade II: Bannerlord", mode="folder",
         detect=["bin/Win64_Shipping_Client/Bannerlord.exe"],
         folders=["Mount & Blade II Bannerlord"], target="Modules", wrappers=["Modules"],
         markers=["SubModule.xml"], note="note_bannerlord"),
    dict(id="kcd", name="Kingdom Come: Deliverance", mode="folder",
         detect=["Bin/Win64/KingdomCome.exe"],
         folders=["KingdomComeDeliverance", "Kingdom Come Deliverance"],
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
         detect=["Phoenix/Binaries/Win64/HogwartsLegacy.exe"],
         folders=["Hogwarts Legacy", "HogwartsLegacy"],
         target="Phoenix/Content/Paks", rooted=["phoenix"], markers=["~mods"],
         loose={".pak": "Phoenix/Content/Paks/~mods", ".utoc": "Phoenix/Content/Paks/~mods",
                ".ucas": "Phoenix/Content/Paks/~mods"}, note="note_ue4"),
    dict(id="palworld", name="Palworld", mode="root",
         detect=["Palworld.exe", "Palworld-WinGDK-Shipping.exe"],
         folders=["Palworld"], target="Pal/Content/Paks", rooted=["pal"],
         markers=["~mods", "LogicMods"],
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


def load_profiles() -> dict[str, dict]:
    profiles = {p["id"]: dict(p) for p in BUILTIN_GAMES}
    try:
        extra = json.loads(GAMES_FILE.read_text(encoding="utf-8"))
        for p in extra:
            if isinstance(p, dict) and p.get("id") and p.get("name") and p.get("mode") in ("folder", "root"):
                p = dict(p)
                p["custom"] = True
                profiles[p["id"]] = p
    except Exception:
        pass
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
        "log_sims4_depth": "⚠ файлы .ts4script вложены слишком глубоко — в The Sims 4 они работают максимум на 1 подпапку глубже Mods",
        "log_autodetect_fail": "⚠ Автопоиск не нашёл игру — укажи папку вручную",
        "err_fomod": "✖ в архиве FOMOD-установщик с выбором опций — такие моды ставь через Vortex / Mod Organizer 2",
        "err_layout": "✖ не удалось определить, куда класть файлы из этого архива (проверь описание мода)",
        "err_perm": "✖ нет доступа к папке — запусти программу от имени администратора",
        "err_empty": "✖ архив пустой",
        "err_no_rar": "✖ для .rar нужен 7-Zip (7z.exe) или UnRAR: установи 7-Zip с 7-zip.org или положи 7z.exe и 7z.dll рядом с программой",
        "err_no_7z": "✖ для .7z нужен 7-Zip (7z.exe) или py7zr: установи 7-Zip с 7-zip.org или положи 7z.exe и 7z.dll рядом с программой",
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
        "log_sims4_depth": "⚠ .ts4script files are nested too deep — The Sims 4 only loads scripts up to 1 subfolder inside Mods",
        "log_autodetect_fail": "⚠ Auto-detect did not find the game — choose the folder manually",
        "err_fomod": "✖ the archive contains a FOMOD installer with options — install such mods with Vortex / Mod Organizer 2",
        "err_layout": "✖ could not work out where this archive's files belong (check the mod description)",
        "err_perm": "✖ access denied — run the program as administrator",
        "err_empty": "✖ the archive is empty",
        "err_no_rar": "✖ .rar needs 7-Zip (7z.exe) or UnRAR: install 7-Zip from 7-zip.org or put 7z.exe and 7z.dll next to the program",
        "err_no_7z": "✖ .7z needs 7-Zip (7z.exe) or py7zr: install 7-Zip from 7-zip.org or put 7z.exe and 7z.dll next to the program",
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
        "log_sims4_depth": "⚠ файли .ts4script вкладені занадто глибоко — у The Sims 4 вони працюють максимум на 1 підпапку глибше Mods",
        "log_autodetect_fail": "⚠ Автопошук не знайшов гру — вкажи папку вручну",
        "err_fomod": "✖ в архіві FOMOD-інсталятор з вибором опцій — такі моди став через Vortex / Mod Organizer 2",
        "err_layout": "✖ не вдалося визначити, куди класти файли з цього архіву (перевір опис мода)",
        "err_perm": "✖ немає доступу до папки — запусти програму від імені адміністратора",
        "err_empty": "✖ архів порожній",
        "err_no_rar": "✖ для .rar потрібен 7-Zip (7z.exe) або UnRAR: встанови 7-Zip з 7-zip.org або поклади 7z.exe і 7z.dll поруч із програмою",
        "err_no_7z": "✖ для .7z потрібен 7-Zip (7z.exe) або py7zr: встанови 7-Zip з 7-zip.org або поклади 7z.exe і 7z.dll поруч із програмою",
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
        "log_sims4_depth": "⚠ .ts4script-Dateien sind zu tief verschachtelt – Die Sims 4 lädt Skripte maximal 1 Unterordner tief in Mods",
        "log_autodetect_fail": "⚠ Automatische Suche erfolglos – Ordner manuell wählen",
        "err_fomod": "✖ Das Archiv enthält einen FOMOD-Installer mit Optionen – solche Mods bitte mit Vortex / Mod Organizer 2 installieren",
        "err_layout": "✖ Zielordner für die Dateien dieses Archivs konnte nicht bestimmt werden (Modbeschreibung prüfen)",
        "err_perm": "✖ Kein Zugriff auf den Ordner – Programm als Administrator starten",
        "err_empty": "✖ Archiv ist leer",
        "err_no_rar": "✖ Für .rar wird 7-Zip (7z.exe) oder UnRAR benötigt: 7-Zip von 7-zip.org installieren oder 7z.exe und 7z.dll neben das Programm legen",
        "err_no_7z": "✖ Für .7z wird 7-Zip (7z.exe) oder py7zr benötigt: 7-Zip von 7-zip.org installieren oder 7z.exe und 7z.dll neben das Programm legen",
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
LANG_NAMES = {"ru": "Русский", "en": "English", "uk": "Українська", "de": "Deutsch"}


def load_external_languages() -> None:
    """Подхватывает пользовательские переводы: <код>.json с ключом _name."""
    try:
        files = list(LANG_DIR.glob("*.json"))
    except Exception:
        return
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                code = f.stem
                LANG_NAMES[code] = str(data.pop("_name", code))
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
        return {0x19: "ru", 0x22: "uk", 0x07: "de"}.get(lid, "en")
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
                    r"Program Files (x86)\GOG Galaxy\Games", r"Program Files\Rockstar Games",
                    r"XboxGames", r"Games"):
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


@lru_cache(maxsize=1)
def _user_documents_dirs() -> list[Path]:
    dirs: list[Path] = []
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            dirs.append(Path(os.path.expandvars(winreg.QueryValueEx(k, "Personal")[0])))
    except Exception:
        pass
    for raw in (r"%USERPROFILE%\Documents", r"%OneDrive%\Documents", r"%OneDriveConsumer%\Documents"):
        p = Path(os.path.expandvars(raw))
        if "%" not in str(p):
            dirs.append(p)
    seen, uniq = set(), []
    for p in dirs:
        if str(p).lower() not in seen:
            seen.add(str(p).lower())
            uniq.append(p)
    return uniq


def find_game_dir(profile: dict) -> str:
    for raw in profile.get("paths", []):
        p = Path(os.path.expandvars(raw))
        if is_game_dir(profile, p):
            return str(p)
        low = raw.lower()
        for prefix in (r"%userprofile%\documents\\", r"%userprofile%\documents/"):
            if low.startswith(prefix):
                rel = raw[len(prefix):]
                for doc in _user_documents_dirs():
                    cand = doc / rel
                    if is_game_dir(profile, cand):
                        return str(cand)
    for root in store_roots():
        for f in profile.get("folders", []):
            for p in (root / f, root / f / "Content"):
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
        res.append(("7-Zip", lambda p, d, z=z: _run_tool([z, "x", "-y", f"-o{d}", str(p)], (0,))))
    if u := find_unrar():
        res.append(("UnRAR", lambda p, d, u=u: _run_tool([u, "x", "-y", "-o+", str(p), str(d) + os.sep])))
    if t := find_tar():
        res.append(("tar", lambda p, d, t=t: _run_tool([t, "-xf", str(p), "-C", str(d)])))
    return res


def extract_external(path: Path, dest: Path) -> None:
    backends = get_backends()
    if not backends:
        raise ModError("err_no_7z" if path.suffix.lower() == ".7z" else "err_no_rar")
    errors = []
    for name, fn in backends:
        try:
            fn(path, dest)
            return
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise ModError("err_extract", err=" | ".join(errors))


def _zip_oem_encoding() -> str:
    if I18N.lang in ("ru", "uk"):
        return "cp866"
    try:
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "cp866"


def extract_zip(path: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    oem_enc = _zip_oem_encoding()
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = info.filename
            if not (info.flag_bits & 0x800) and not name.isascii():
                try:
                    name = name.encode("cp437").decode(oem_enc)
                except Exception:
                    pass
            target = (dest / name).resolve()
            if target == dest_resolved or not target.is_relative_to(dest_resolved):
                continue  # zip-slip
            if info.is_dir() or name.endswith(("/", "\\")):
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
                    for c in list(dest.iterdir()):
                        shutil.rmtree(c, ignore_errors=True) if c.is_dir() else c.unlink(missing_ok=True)
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
    prefix_targets = {k.lower(): v for k, v in (p.get("prefix_targets") or {}).items()}
    plan = Plan()

    for d in outermost(root, lambda d: d != root and d.name.lower() in wl):
        plan.ops.append(Op(d, wl[d.name.lower()]))
    if plan.ops:
        return plan

    def _dst_for(name: str) -> str:
        low = name.lower()
        base = next((v for pref, v in prefix_targets.items() if low.startswith(pref)), target)
        return _join(base, name)

    if markers:
        for d in outermost(root, lambda d: has_marker(d, markers)):
            plan.ops.append(Op(d, _dst_for(stem if d == root else d.name)))
        if plan.ops:
            return plan

    kids = [c for c in root.iterdir() if c.name.lower() not in JUNK_NAMES]
    dirs = [c for c in kids if c.is_dir()]
    files = [c for c in kids if c.is_file()]
    if len(dirs) == 1 and not files:
        dst = _dst_for(dirs[0].name)
        plan.ops.append(Op(dirs[0], dst))
    else:
        dst = _dst_for(stem)
        plan.ops.append(Op(root, dst))
    if markers:
        plan.warns.append(("log_fallback", {"dst": dst}))
    return plan


def _is_fomod_installer(fomod_dir: Path) -> bool:
    try:
        return any(c.is_file() and c.name.lower() == "moduleconfig.xml" for c in fomod_dir.iterdir())
    except OSError:
        return True


def plan_root(p: dict, root: Path, stem: str) -> Plan:
    target = p.get("target", "").strip("/\\")
    markers = {m.lower() for m in p.get("markers", [])}
    rooted = {r.lower() for r in p.get("rooted", [])}
    if not rooted and target and "/" not in target and "\\" not in target:
        rooted = {target.lower()}
    loose = {k.lower(): v for k, v in (p.get("loose") or {}).items()}
    plan = Plan()

    def is_rooted(d: Path) -> bool:
        try:
            return any(c.is_dir() and c.name.lower() in rooted for c in d.iterdir())
        except OSError:
            return False

    content = next((d for d in walk_dirs(root) if has_marker(d, markers) or is_rooted(d)), None)
    fomods = [d for d in walk_dirs(root) if d.name.lower() == "fomod"]
    if fomods and (any(_is_fomod_installer(f) for f in fomods) or not all(f.parent == content for f in fomods)):
        raise ModError("err_fomod")

    if content is not None:
        dst = "" if is_rooted(content) else target
        plan.ops.append(Op(content, dst, skip_top={"fomod"}, skip_docs=True))
        return plan

    loose_hits: dict[Path, str] = {}
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix.lower() in loose and not any(x.lower() in JUNK_NAMES for x in f.parts):
            dst = loose[f.suffix.lower()]
            plan.loose.append((f, dst))
            loose_hits[f] = dst
    if loose_hits:
        added = set(loose_hits)
        for f, dst in list(loose_hits.items()):
            for c in sorted(f.parent.iterdir()):
                if c not in added and c.is_file() and c.stem.lower() == f.stem.lower() \
                        and c.suffix.lower() in COMPANION_EXTS:
                    plan.loose.append((c, dst))
                    added.add(c)
        plan.warns.append(("log_loose", {}))
        return plan

    if not markers and not loose:
        kids = [c for c in root.iterdir() if c.name.lower() not in JUNK_NAMES]
        dirs = [c for c in kids if c.is_dir()]
        files = [c for c in kids if c.is_file()]
        src = dirs[0] if len(dirs) == 1 and not files else root
        plan.ops.append(Op(src, target, skip_top={"fomod"}, skip_docs=True))
        plan.warns.append(("log_fallback", {"dst": target or "/"}))
        return plan

    raise ModError("err_layout")


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


def db_update(gid: str, rec: dict) -> None:
    with _db_lock:
        db = load_json(DB_FILE, {})
        recs = db.get(gid, [])
        for i, r in enumerate(recs):
            if r["id"] == rec["id"]:
                recs[i] = rec
                break
        save_json(DB_FILE, db)


def db_remove(gid: str, rid: str) -> None:
    with _db_lock:
        db = load_json(DB_FILE, {})
        db[gid] = [r for r in db.get(gid, []) if r["id"] != rid]
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


def copy_op(tr: Tracker, op: Op) -> int:
    dst_root = tr.game / op.dst if op.dst else tr.game
    dst_root.mkdir(parents=True, exist_ok=True)
    n = 0
    for path in sorted(op.src.rglob("*")):
        rel = path.relative_to(op.src)
        low = [x.lower() for x in rel.parts]
        if any(x in JUNK_NAMES for x in low) or low[0] in op.skip_top:
            continue
        if op.skip_docs and len(low) == 1 and path.is_file() and path.suffix.lower() in DOC_EXTS:
            continue
        dest = dst_root / rel
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            tr.copy(path, dest)
            n += 1
    return n


def protected_dirs(profile: dict, game: Path) -> set[Path]:
    prot = {game}
    if profile.get("target"):
        prot.add(game / profile["target"])
    wr = profile.get("wrappers") or {}
    for v in (wr.values() if isinstance(wr, dict) else wr):
        prot.add(game / v)
    for v in (profile.get("prefix_targets") or {}).values():
        prot.add(game / v)
    return prot


def uninstall_record(profile: dict, game: Path, rec: dict) -> None:
    gid = profile["id"]
    game_key = os.path.normcase(str(game))
    all_recs = [r for r in db_records(gid) if os.path.normcase(r.get("game", "")) == game_key]
    idx = next((i for i, r in enumerate(all_recs) if r["id"] == rec["id"]), None)
    if idx is not None:
        rec = all_recs[idx]
    later = all_recs[idx + 1:] if idx is not None else []
    bdir = BACKUP_DIR / gid / rec["id"]
    backups = set(rec.get("backups", []))
    dirty: dict[str, dict] = {}
    removed_files: list[str] = []
    for rel in rec["files"]:
        nxt = next((r for r in later if rel in r.get("files", [])), None)
        if nxt is not None:
            nbdir = BACKUP_DIR / gid / nxt["id"]
            nb_list = list(nxt.get("backups", []))
            if rel in backups and (bdir / rel).exists():
                (nbdir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bdir / rel, nbdir / rel)
                if rel not in nb_list:
                    nb_list.append(rel)
                    nxt["backups"] = nb_list
                    dirty[nxt["id"]] = nxt
            else:
                if rel in nb_list:
                    nxt["backups"] = [x for x in nb_list if x != rel]
                    dirty[nxt["id"]] = nxt
                nb_file = nbdir / rel
                if nb_file.exists():
                    try:
                        nb_file.unlink()
                    except OSError:
                        pass
            continue
        f = game / rel
        try:
            if rel in backups and (bdir / rel).exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bdir / rel, f)
            elif f.exists():
                f.unlink()
                removed_files.append(rel)
        except OSError:
            pass
    for r in dirty.values():
        db_update(gid, r)
    prot = protected_dirs(profile, game)
    dirs = sorted({(game / rel).parent for rel in removed_files}, key=lambda p: len(p.parts), reverse=True)
    for d in dirs:
        cur = d
        while cur not in prot and game in cur.parents and len(cur.relative_to(game).parts) > 1:
            try:
                cur.rmdir()  # удалится только если пустая
            except OSError:
                break
            cur = cur.parent
    shutil.rmtree(bdir, ignore_errors=True)


def install_source(profile: dict, src: Path, game: Path, log) -> bool:
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
            if not tr.files:
                raise ModError("err_empty")
        except Exception:
            rec.update(files=tr.files, backups=tr.backups)
            uninstall_record(profile, game, rec)  # откат частичной установки
            raise

        if gid == "sims4" and any(
            rel.lower().endswith(".ts4script") and len(rel.split("/")) > 3
            for rel in tr.files
        ):
            log("   " + T("log_sims4_depth"))
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
        self.gid = self.cfg.get("last_game") if self.cfg.get("last_game") in self.profiles else "rdr2"

        self.root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry(self.cfg.get("geometry", "1040x780"))
        self.root.minsize(860, 640)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Control-o>", lambda e: self.pick_archives())

        self.style = ttk.Style(self.root)
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.drop_hover = False
        self.log_lines: list[str] = []
        self.items: list[dict] = []
        self.shown: list[dict] = []

        self.game_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value=T("ready"))
        self.count_var = tk.StringVar()

        self.apply_styles()
        self._build_ui()
        self._recolor()
        self.load_game_path(autodetect=True)
        self.root.after(100, self._poll_queue)

    # ---------------------------------------------------------------- helpers
    @property
    def profile(self) -> dict:
        return self.profiles[self.gid]

    def game_path(self) -> Path | None:
        p = self.game_var.get().strip().strip('"')
        return Path(p) if p and Path(p).is_dir() else None

    def on_close(self):
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
        s.configure("Card.TFrame", background=c["surface"])
        s.configure("TLabel", background=c["bg"], foreground=c["fg"])
        s.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"])
        s.configure("Title.TLabel", background=c["bg"], foreground=c["fg"], font=(FONT, 20, "bold"))
        s.configure("Card.TLabel", background=c["surface"], foreground=c["fg"])
        s.configure("CardTitle.TLabel", background=c["surface"], foreground=c["fg"], font=(FONT, 11, "bold"))
        s.configure("CardMuted.TLabel", background=c["surface"], foreground=c["muted"])
        s.configure("Ok.Card.TLabel", foreground=c["ok"])
        s.configure("Warn.Card.TLabel", foreground=c["warn"])
        s.configure("Err.Card.TLabel", foreground=c["err"])
        s.configure("TRadiobutton", background=c["bg"], foreground=c["fg"])
        s.map("TRadiobutton", background=[("active", c["bg"])], indicatorcolor=[("selected", c["accent"])])

        for name, bg, fg, hov in (("TButton", c["surface2"], c["fg"], c["hover"]),
                                   ("Accent.TButton", c["accent"], "#ffffff", c["accent_hover"]),
                                   ("Danger.TButton", c["surface2"], c["err"], c["hover"])):
            s.configure(name, background=bg, foreground=fg, bordercolor=bg, lightcolor=bg, darkcolor=bg,
                        padding=(14, 7), relief="flat", focusthickness=0)
            s.map(name, background=[("disabled", c["surface"]), ("pressed", hov), ("active", hov)],
                  foreground=[("disabled", c["muted"])], bordercolor=[("active", hov)],
                  lightcolor=[("active", hov)], darkcolor=[("active", hov)])

        s.configure("TEntry", fieldbackground=c["surface2"], foreground=c["fg"], insertcolor=c["fg"],
                    bordercolor=c["border"], lightcolor=c["surface2"], darkcolor=c["surface2"], padding=6)
        s.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])],
              darkcolor=[("focus", c["accent"])])
        s.configure("TCombobox", fieldbackground=c["surface2"], background=c["surface2"], foreground=c["fg"],
                    arrowcolor=c["muted"], bordercolor=c["border"], lightcolor=c["surface2"],
                    darkcolor=c["surface2"], padding=6)
        s.map("TCombobox", fieldbackground=[("readonly", c["surface2"])],
              foreground=[("readonly", c["fg"])], background=[("active", c["hover"])],
              selectbackground=[("readonly", c["surface2"])], selectforeground=[("readonly", c["fg"])],
              bordercolor=[("focus", c["accent"])])
        self.root.option_add("*TCombobox*Listbox.background", c["surface2"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
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
                                selectbackground=c["accent"], selectforeground="#ffffff")
        self.log_text.tag_configure("head", foreground=c["fg"], font=("Consolas", 10, "bold"))
        self.log_text.tag_configure("ok", foreground=c["ok"])
        self.log_text.tag_configure("warn", foreground=c["warn"])
        self.log_text.tag_configure("err", foreground=c["err"])
        self.listbox.configure(bg=c["surface2"], fg=c["fg"], selectbackground=c["accent"],
                               selectforeground="#ffffff")
        self.draw_drop()
        self.root.after(60, lambda: set_titlebar_dark(self.root, self.theme == "dark"))

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.cfg["theme"] = self.theme
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
        for w in self.root.winfo_children():
            w.destroy()
        self.apply_styles()
        self._build_ui()
        self._recolor()
        for line in self.log_lines:
            self._write_log(line)
        self.update_game_status()
        self.refresh_items()

    # --------------------------------------------------------------------- ui
    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=T("subtitle"), style="Muted.TLabel").pack(side="left", padx=14, pady=(8, 0))
        ttk.Button(header, text=T("theme_to_light") if self.theme == "dark" else T("theme_to_dark"),
                   command=self.toggle_theme, width=12).pack(side="right")
        self.lang_combo = ttk.Combobox(header, values=list(LANG_NAMES.values()), state="readonly", width=11)
        self.lang_combo.set(LANG_NAMES.get(I18N.lang, "English"))
        self.lang_combo.pack(side="right", padx=8)
        self.lang_combo.bind("<<ComboboxSelected>>", self.change_language)

        # --- игра и папка
        card = ttk.Frame(outer, style="Card.TFrame", padding=14)
        card.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text=T("game"), style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.ids = list(self.profiles.keys())
        self.game_combo = ttk.Combobox(card, values=[self.profiles[i]["name"] for i in self.ids],
                                       state="readonly", height=16)
        self.game_combo.set(self.profile["name"])
        self.game_combo.grid(row=0, column=1, sticky="ew", padx=10)
        self.game_combo.bind("<<ComboboxSelected>>", self.on_game_selected)
        ttk.Button(card, text=T("add_game"), command=self.add_game_dialog).grid(row=0, column=2)

        ttk.Label(card, text=T("folder"), style="CardTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 0))
        entry = ttk.Entry(card, textvariable=self.game_var)
        entry.grid(row=1, column=1, sticky="ew", padx=10, pady=(10, 0))
        entry.bind("<KeyRelease>", lambda e: self.on_path_edited())
        bf = ttk.Frame(card, style="Card.TFrame")
        bf.grid(row=1, column=2, pady=(10, 0))
        ttk.Button(bf, text=T("browse"), command=self.pick_game_dir).pack(side="left")
        ttk.Button(bf, text=T("detect"), command=self.autodetect).pack(side="left", padx=(6, 0))
        self.game_status = ttk.Label(card, style="CardMuted.TLabel")
        self.game_status.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.note_label = ttk.Label(card, style="CardMuted.TLabel", justify="left")
        self.note_label.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        card.bind("<Configure>", lambda e: self.note_label.configure(wraplength=max(e.width - 40, 200)))

        # --- зона перетаскивания
        self.drop = tk.Canvas(outer, height=116, highlightthickness=0, bd=0, cursor="hand2")
        self.drop.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self.drop.bind("<Configure>", lambda e: self.draw_drop())
        self.drop.bind("<Button-1>", lambda e: self.pick_archives())
        if TkinterDnD:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<DropEnter>>", self.on_drop_enter)
            self.drop.dnd_bind("<<DropLeave>>", self.on_drop_leave)
            self.drop.dnd_bind("<<Drop>>", self.on_drop)

        pf = ttk.Frame(outer)
        pf.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        pf.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(pf, mode="determinate", maximum=1, value=0)
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(pf, textvariable=self.status_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))

        # --- журнал + список модов
        main = ttk.Frame(outer)
        main.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        main.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=3, uniform="cols")
        main.columnconfigure(1, weight=2, uniform="cols")

        log_card = ttk.Frame(main, style="Card.TFrame", padding=12)
        log_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        log_card.rowconfigure(1, weight=1)
        log_card.columnconfigure(0, weight=1)
        lh = ttk.Frame(log_card, style="Card.TFrame")
        lh.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(lh, text=T("log"), style="CardTitle.TLabel").pack(side="left")
        ttk.Button(lh, text=T("clear"), command=self.clear_log).pack(side="right")
        self.log_text = tk.Text(log_card, wrap="word", state="disabled", relief="flat", bd=0,
                                highlightthickness=0, padx=10, pady=8, font=("Consolas", 10))
        self.log_text.grid(row=1, column=0, sticky="nsew")
        lsb = ttk.Scrollbar(log_card, orient="vertical", command=self.log_text.yview)
        lsb.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=lsb.set)

        mods = ttk.Frame(main, style="Card.TFrame", padding=12)
        mods.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        mods.rowconfigure(2, weight=1)
        mods.columnconfigure(0, weight=1)
        mh = ttk.Frame(mods, style="Card.TFrame")
        mh.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(mh, text=T("installed"), style="CardTitle.TLabel").pack(side="left")
        ttk.Label(mh, textvariable=self.count_var, style="CardMuted.TLabel").pack(side="left", padx=8)
        sf = ttk.Frame(mods, style="Card.TFrame")
        sf.grid(row=1, column=0, columnspan=2, sticky="ew", pady=8)
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
        self.hint_label = ttk.Label(mods, text=T("hint_unmanaged"), style="CardMuted.TLabel")
        self.hint_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        bf2 = ttk.Frame(mods, style="Card.TFrame")
        bf2.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bf2, text=T("delete"), style="Danger.TButton", command=self.delete_selected).pack(side="left")
        ttk.Button(bf2, text=T("refresh"), command=self.refresh_items).pack(side="left", padx=6)
        ttk.Button(bf2, text=T("open_dir"), command=self.open_target).pack(side="left")

        self.footer = ttk.Label(outer, style="Muted.TLabel")
        self.footer.grid(row=5, column=0, sticky="w", pady=(8, 0))
        backends = get_backends()
        parts = [T("footer_rar_ok", tool=backends[0][0]) if backends else T("footer_rar_no")]
        if not TkinterDnD:
            parts.append(T("footer_dnd_no"))
        parts.append(f"v{APP_VERSION}")
        self.footer.configure(text="   ·   ".join(parts))

    def draw_drop(self):
        c, cv = THEMES[self.theme], self.drop
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 20:
            return
        hover = self.drop_hover
        cv.configure(bg=c["surface2"] if hover else c["surface"])
        cv.create_rectangle(5, 5, w - 5, h - 5, outline=c["accent"] if hover else c["border"], dash=(7, 5), width=2)
        title = T("drop_hover") if hover else (T("drop_title") if TkinterDnD else T("drop_nodnd"))
        cv.create_text(w / 2, h / 2 - 12, text=title, fill=c["fg"], font=(FONT, 14, "bold"))
        cv.create_text(w / 2, h / 2 + 16, text=T("drop_sub"), fill=c["muted"], font=(FONT, 10))

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
                elif kind == "done":
                    ok, total = payload
                    self.busy = False
                    self.status_var.set(T("done_status", ok=ok, n=total))
                    self.refresh_items()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    # ------------------------------------------------------------- выбор игры
    def on_game_selected(self, event=None):
        idx = self.game_combo.current()
        if idx < 0:
            return
        self.gid = self.ids[idx]
        self.game_combo.selection_clear()
        self.cfg["last_game"] = self.gid
        self.save_cfg()
        self.load_game_path(autodetect=True)

    def load_game_path(self, autodetect: bool = False):
        saved = self.cfg.get("games", {}).get(self.gid, {}).get("path", "")
        if saved and Path(saved).is_dir():
            self.game_var.set(saved)
        elif autodetect:
            self.game_var.set(find_game_dir(self.profile))
            if self.game_var.get():
                self.remember_path()
        else:
            self.game_var.set("")
        self.update_game_status()
        self.refresh_items()

    def remember_path(self):
        p = self.game_var.get().strip().strip('"')
        self.cfg.setdefault("games", {}).setdefault(self.gid, {})["path"] = p
        self.save_cfg()

    def on_path_edited(self):
        if self.game_path():
            self.remember_path()
        self.update_game_status()
        self.refresh_items()

    def pick_game_dir(self):
        p = filedialog.askdirectory(title=T("dlg_folder", game=self.profile["name"]))
        if p:
            self.game_var.set(os.path.normpath(p))
            self.remember_path()
            self.update_game_status()
            self.refresh_items()

    def autodetect(self):
        found = find_game_dir(self.profile)
        if found:
            self.game_var.set(found)
            self.remember_path()
        else:
            self.log(T("log_autodetect_fail"))
        self.update_game_status()
        self.refresh_items()

    def update_game_status(self):
        gp = self.game_path()
        if gp and is_game_dir(self.profile, gp):
            self.game_status.configure(text=T("st_ok"), style="Ok.Card.TLabel")
        elif gp:
            self.game_status.configure(text=T("st_nodetect"), style="Warn.Card.TLabel")
        else:
            self.game_status.configure(text=T("st_nofolder"), style="Err.Card.TLabel")
        note = self.profile.get("note", "")
        self.note_label.configure(text=("ℹ " + T(note)) if note else "")
        self.hint_label.configure(text=T("hint_unmanaged") if self.profile["mode"] == "folder" else "")

    # ------------------------------------------------------------ установка
    def pick_archives(self):
        files = filedialog.askopenfilenames(
            title=T("dlg_archives"),
            filetypes=[(T("ft_archives"), "*.zip *.7z *.rar"), (T("ft_all"), "*.*")])
        if files:
            self.start_install([Path(f) for f in files])

    def on_drop_enter(self, event):
        self.drop_hover = True
        self.draw_drop()
        return event.action

    def on_drop_leave(self, event):
        self.drop_hover = False
        self.draw_drop()
        return event.action

    def on_drop(self, event):
        self.drop_hover = False
        self.draw_drop()
        self.start_install([Path(f) for f in self.root.tk.splitlist(event.data)])
        return event.action

    def start_install(self, files: list[Path]):
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
                if install_source(profile, f, game, self.log):
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
        gp = self.game_path()
        if gp:
            key = os.path.normcase(str(gp))
            known: set[str] = set()
            for r in db_records(self.gid):
                if os.path.normcase(r.get("game", "")) == key:
                    items.append({"label": r["name"], "kind": "rec", "ref": r})
                    known |= {d.lower() for d in r.get("dirs", [])}
            target = self.profile.get("target", "")
            if self.profile["mode"] == "folder" and target and (gp / target).is_dir():
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
        self.count_var.set(f"{len(self.shown)} / {total}" if q else str(total))

    def _selected(self) -> list[dict]:
        return [self.shown[i] for i in self.listbox.curselection()]

    def delete_selected(self):
        if self.busy:
            messagebox.showinfo(APP_NAME, T("msg_busy"))
            return
        sel, gp = self._selected(), self.game_path()
        if not sel or not gp:
            return
        if not messagebox.askyesno(APP_NAME, T("confirm_delete", names="\n".join(s["label"] for s in sel))):
            return
        for it in sel:
            name = it["label"][:-2] if it["kind"] == "dir" else it["label"]
            try:
                if it["kind"] == "rec":
                    uninstall_record(self.profile, gp, it["ref"])
                    db_remove(self.gid, it["ref"]["id"])
                else:
                    shutil.rmtree(it["ref"])
                self.log(T("log_deleted", name=name))
            except Exception as e:
                self.log(T("log_delete_fail", name=name, err=e))
        self.refresh_items()

    def open_selected(self):
        sel, gp = self._selected(), self.game_path()
        if not sel or not gp:
            return
        it = sel[0]
        if it["kind"] == "dir":
            os.startfile(it["ref"])  # Windows only
        else:
            target = gp / self.profile.get("target", "")
            dirs = it["ref"].get("dirs", [])
            files = it["ref"].get("files", [])
            if dirs and (target / dirs[0]).is_dir():
                p = target / dirs[0]
            elif files and (gp / files[0]).parent.is_dir():
                p = (gp / files[0]).parent
            else:
                p = target if target.is_dir() else gp
            os.startfile(p)

    def open_target(self):
        gp = self.game_path()
        if gp:
            t = gp / self.profile.get("target", "")
            os.startfile(t if t.is_dir() else gp)

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
        target, detect, markers = tk.StringVar(), tk.StringVar(), tk.StringVar()
        row = 0
        for label, var in ((T("dlg_name"), name),):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(frm, textvariable=var, width=52).grid(row=row + 1, column=0, sticky="ew", pady=(2, 10))
            row += 2
        ttk.Label(frm, text=T("dlg_mode")).grid(row=row, column=0, sticky="w")
        ttk.Radiobutton(frm, text=T("mode_folder"), variable=mode, value="folder").grid(row=row + 1, column=0, sticky="w")
        ttk.Radiobutton(frm, text=T("mode_root"), variable=mode, value="root").grid(row=row + 2, column=0, sticky="w", pady=(0, 10))
        row += 3
        for label, var in ((T("dlg_target"), target), (T("dlg_detect"), detect), (T("dlg_markers"), markers)):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(frm, textvariable=var, width=52).grid(row=row + 1, column=0, sticky="ew", pady=(2, 10))
            row += 2

        def save():
            n = name.get().strip()
            if not n:
                messagebox.showerror(APP_NAME, T("dlg_need_name"), parent=dlg)
                return
            gid = "custom_" + re.sub(r"\W+", "_", n.lower()).strip("_")
            tgt = target.get().strip().strip("/\\")
            prof = {"id": gid, "name": n, "mode": mode.get(), "target": tgt,
                    "detect": [detect.get().strip()] if detect.get().strip() else [],
                    "markers": [m.strip() for m in markers.get().split(",") if m.strip()],
                    "folders": []}
            if mode.get() == "folder" and tgt and "/" not in tgt and "\\" not in tgt:
                prof["wrappers"] = [tgt]
            save_custom_profile(prof)
            self.profiles = load_profiles()
            self.gid = gid
            self.cfg["last_game"] = gid
            self.save_cfg()
            dlg.destroy()
            self.rebuild()
            self.load_game_path()

        bf = ttk.Frame(frm)
        bf.grid(row=row, column=0, sticky="e")
        ttk.Button(bf, text=T("dlg_cancel"), command=dlg.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(bf, text=T("dlg_save"), style="Accent.TButton", command=save).pack(side="left")
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_rooty() + 80
        dlg.geometry(f"+{max(x, 0)}+{y}")
        dlg.grab_set()
        self.root.after(80, lambda: set_titlebar_dark(dlg, self.theme == "dark"))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()