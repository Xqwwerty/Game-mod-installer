# Game Mod Installer (v2.0)

A fast, lightweight, open-source mod installer for PC games supporting **`.zip`**, **`.7z`**, **`.rar`**, **`.gmpack.zip`** modpacks, and unpacked folders — with Drag & Drop, automatic backups, conflict detection, and 1-click **Vanilla Mode**.

<img width="1033" height="813" alt="Screenshot_782" src="https://github.com/user-attachments/assets/32561277-9eb5-4b26-8f84-102e0da27f07" />



---

## Download

Pre-built Windows executable (`.exe`) is available in **[Releases](https://github.com/Xqwwerty/Game-mod-installer/releases/latest)**.

> **Windows SmartScreen Notice:** Because the `.exe` is not signed with a paid code-signing certificate, Windows may show a *"Windows protected your PC"* prompt on the first launch. Click **More info → Run anyway**. The full source code is open and tested via GitHub Actions CI in this repository.

---

## What's New in v2.0

* **Modern UI & Flexible Layout:** Redesigned interface with custom `Pill` badges and toggle switches, resizable splitters (horizontal & vertical layouts), and a **Settings (`⚙`)** menu to hide/show panels (including hiding the Log or Recent Downloads).
* **Vanilla Mode (Panic Toggle):** Disable all installed mods in 1 click (restoring clean vanilla files for online/multiplayer) and re-enable them all just as fast.
* **Engine Presets (Hundreds of Games):** Built-in templates for **BepInEx** (Unity), **MelonLoader**, **Unreal Engine** (`.pak`/`.ucas`/`.utoc`), and **RE Engine** (`reframework`).
* **Smart Conflict Detection:** Warns you before installing if an incoming mod will overwrite files owned by another installed mod.
* **Dependency Health Check:** Automatically checks if required loaders are present (e.g. `lml` / `ScriptHook`, `SKSE`, `SMAPI`, `BepInEx`, `REFramework`) and alerts you if something is missing.
* **Built-in Mod Config Editor:** Right-click any installed mod (or click **Configs**) to edit its `.ini`, `.cfg`, `.json`, `.xml`, or `.toml` files directly inside the app.
* **Recent Downloads Bar:** Quickly install your latest `.zip` / `.7z` / `.rar` downloads straight from your Windows `Downloads` folder in 1 click.
* **Save Game Protector:** Manual and automatic save-folder backup snapshots before installing mods, with easy rollback.
* **Interactive File Preview:** Inspect the destination file tree and uncheck optional files/folders before installing (via Settings or holding `Shift`).
* **1-Click Modpack Export & Import (`.gmpack.zip`):** Package all active mods and configs for a game into a shareable `.gmpack.zip` archive.
* **Live Game Search (`Ctrl + F`):** Instant search popup showing which games are detected on your PC.
* **7 Themes & 7 Languages:** Dark, Light, OLED, Cyberpunk, Dracula, Nord, Outlaw — and **EN, RU, UK, DE, FR, ES, ZH**.
* **Performance Optimization:** Lazy-loaded `py7zr`, background store scanning, and debounced search for instant startup.

---

## Core Features

* **Game Auto-Detection:** Scans **Steam** (Registry & `libraryfolders.vdf`), **Epic Games**, **GOG Galaxy**, **Rockstar Games**, **Xbox / Game Pass** (`XboxGames`), and **Documents** (including OneDrive and relocated Documents folders).
* **Safe Uninstall & Chained Backups:** Backs up original files before overwriting and restores them cleanly on uninstall — even if multiple mods modified the same file in sequence.
* **Built-in Safeguards:** Detects interactive FOMOD installers (`ModuleConfig.xml`), protects against Zip-Slip path traversal, skips junk (`__MACOSX`, `Thumbs.db`), and rolls back failed installations.
* **Community Profiles (`games.d`):** Drop `.json` game profiles into `%APPDATA%\GameModInstaller\games.d\` or create them via **＋ Custom game**.

---

## Supported Games (27+ Built-in Profiles & Engine Presets)

<details>
<summary>Show all built-in game profiles</summary>

| Game | Mod Directory | Mode |
| :--- | :--- | :--- |
| Red Dead Redemption 2 | `lml` | `folder` |
| Grand Theft Auto V | Game root / `scripts` | `root` |
| Grand Theft Auto: San Andreas | Game root / `cleo` / `modloader` | `root` |
| Cyberpunk 2077 | Game root (`archive/pc/mod`, `r6/scripts`, `r6/tweaks`, `red4ext`, `mods`) | `root` |
| The Elder Scrolls V: Skyrim SE / LE / VR | `Data` | `root` |
| The Elder Scrolls IV: Oblivion | `Data` | `root` |
| The Elder Scrolls III: Morrowind | `Data Files` | `root` |
| Fallout 3 / Fallout: New Vegas / Fallout 4 | `Data` | `root` |
| Starfield | `Data` | `root` |
| The Witcher 3: Wild Hunt | `mods` / `dlc` | `folder` |
| Stardew Valley | `Mods` | `folder` |
| Mount & Blade II: Bannerlord | `Modules` | `folder` |
| Kingdom Come: Deliverance | `Mods` | `folder` |
| RimWorld | `Mods` | `folder` |
| Kerbal Space Program | Game root (`GameData`) | `root` |
| Left 4 Dead 2 | `left4dead2` / `left4dead2/addons` | `root` |
| Garry's Mod | `garrysmod/addons` | `folder` |
| Monster Hunter: World | Game root (`nativePC`) | `root` |
| Hogwarts Legacy | `Phoenix/Content/Paks/~mods` | `root` |
| Palworld | `Pal/Content/Paks/~mods` / `LogicMods` | `root` |
| Euro Truck Simulator 2 | `Documents\Euro Truck Simulator 2\mod` | `root` |
| American Truck Simulator | `Documents\American Truck Simulator\mod` | `root` |
| The Sims 4 | `Documents\Electronic Arts\The Sims 4\Mods` | `folder` |
| **Any Unity / UE / Capcom game** | Via **Engine Presets** (`BepInEx`, `MelonLoader`, `Unreal Engine`, `RE Engine`) | `root` |

</details>

---

## Usage

1. Launch `GameModInstaller.exe`.
2. Choose a game from the dropdown or press **`Ctrl + F`** / **Find** to search.
3. Drag and drop `.zip`, `.7z`, `.rar`, `.gmpack.zip` archives or folders into the drop zone (or use **Recent Downloads** / `Ctrl + O`).
4. Manage installed mods on the right: edit configs, toggle **Vanilla Mode**, export a modpack, or uninstall cleanly with `Delete`.

---

## Configuration & Custom Content

All settings, backups, and user profiles are stored in:
```text
%APPDATA%\GameModInstaller\
```
* **`games.json`** & **`games.d\*.json`** — custom & community game profiles.
* **`lang\<code>.json`** — custom translation files (`"_name": "Language Name"`).
* **`installs.json`**, **`backups\`**, **`vanilla_stash\`**, **`save_backups\`** — tracked files, backups, and snapshots.

---

## How to Add a New Game Profile

You can contribute profiles via Pull Request or place a `.json` file in `%APPDATA%\GameModInstaller\games.d\`:

```json
{
  "id": "valheim",
  "name": "Valheim",
  "engine": "bepinex",
  "detect": ["valheim.exe"],
  "folders": ["Valheim"]
}
```
Supported `engine` presets: `"bepinex"`, `"melonloader"`, `"unreal_engine"`, `"re_engine"`.

---

## Running from Source, Tests & Building `.exe`

Requires **Python 3.10+** on Windows.

```bash
pip install -r requirements.txt
python game_mod_installer.py
```

Run unit tests:
```bash
pip install pytest
pytest -v
```

For `.rar` extraction, install **[7-Zip](https://www.7-zip.org/)** or **WinRAR**, or place `7z.exe` + `7z.dll` next to the app.

Build standalone `.exe`:
```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --noupx --clean --name GameModInstaller --add-binary "7z.exe;." --add-binary "7z.dll;." game_mod_installer.py
```
