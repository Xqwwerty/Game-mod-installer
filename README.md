<img width="1041" height="810" alt="Screenshot_774" src="https://github.com/user-attachments/assets/a851599d-65ad-4f54-96c4-5ea5521bce8f" />
<img width="1034" height="809" alt="Screenshot_773" src="https://github.com/user-attachments/assets/a4562108-1980-44f3-a075-ac3ca0ee1fc2" />
# Game Mod Installer

A lightweight, universal mod installer for PC games supporting **`.zip`**, **`.7z`**, **`.rar`** archives, and unpacked folders (with Drag & Drop support).

The app automatically detects installed games, recognizes archive layouts, places files into the proper directories, and tracks every installed file—allowing clean uninstallation and automatic restoration of overwritten vanilla files from backups.

---

## Download

Pre-built Windows executable (`.exe`) is available in **[Releases](https://github.com/YOUR_USERNAME/Game-Mod-Installer/releases/latest)**.

> **Windows SmartScreen Notice:** Because the `.exe` is not signed with a paid code-signing certificate, Windows may show a *"Windows protected your PC"* prompt on the first launch. Click **More info → Run anyway**. The full source code is open and available in this repository.

---

## Features

* **Archive & Folder Support:** Install from `.zip`, `.7z`, `.rar`, or unpacked mod folders.
* **Two Installation Modes:**
  * `folder` — places each mod into its own subfolder inside the mod directory (*RDR2*, *Stardew Valley*, *The Witcher 3*, *RimWorld*, etc.).
  * `root` — mirrors the archive structure into the game root/target folder (*Cyberpunk 2077*, *GTA V*, *Bethesda* games, *Palworld*, etc.) and sorts loose files by extension (`.asi`, `.cs`, `.pak`, `.vpk`, `.scs`, `.archive`, `.xl`, `.reds`, `.yaml`, etc.).
* **Game Auto-Detection:** Scans **Steam** (via Windows Registry & `libraryfolders.vdf`), **Epic Games**, **GOG Galaxy**, **Rockstar Games**, **Xbox / Game Pass** (`XboxGames`), and the **Documents** folder (including OneDrive and relocated Documents folders).
* **Safe Uninstall & Backups:**
  * Automatically backs up original files before overwriting them.
  * Restores original files on uninstall (including proper backup chaining when multiple mods overwrite the same file).
  * Automatically rolls back partial installations if an error occurs.
* **Smart Safeguards:**
  * Detects interactive FOMOD installers (`ModuleConfig.xml`) and warns you to install them via Vortex / Mod Organizer 2.
  * Skips junk files (`__MACOSX`, `Thumbs.db`, `.DS_Store`) and top-level docs in root-mode installs.
  * Protects against Zip-Slip path traversal during extraction.
  * Warns when `.ts4script` files in *The Sims 4* are nested too deep inside `Mods`.
* **Custom Games & Translations:** Add custom games directly from the UI (**＋ Custom game**) or via `games.json`.
* **UI:** Dark and Light themes, search filter for installed mods, double-click to open mod folder, 4 built-in languages (**English**, **Русский**, **Українська**, **Deutsch**).

---

## Supported Games (27 Built-in Profiles)

<details>
<summary>Show all supported games and target folders</summary>

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

</details>

---

## Usage

1. Launch `GameModInstaller.exe`.
2. Select your game from the dropdown list — the app will try to locate the game folder automatically (or click **Browse…** to select it manually).
3. Drag and drop `.zip`, `.7z`, `.rar` archives or mod folders into the drop area (or click / press `Ctrl + O` to browse).
4. Installed mods appear in the list on the right. Select any mod and click **Uninstall** (or press `Delete`) to remove it cleanly. Items marked with `*` were not installed through this tool.

---

## Configuration & Custom Languages

All settings, installation records, and backups are stored in:
```text
%APPDATA%\GameModInstaller\
```
* **`games.json`** — custom game profiles (created via **＋ Custom game** or edited manually).
* **`lang\<code>.json`** — custom translation files (include `"_name": "Language Name"`).
* **`installs.json`** & **`backups\`** — tracked files and backups for clean uninstallation.

---

## Running from Source & Building `.exe`

Requires **Python 3.10+** on Windows.

```bash
pip install -r requirements.txt
python game_mod_installer.py
```

For `.rar` extraction, the app looks for an installed **7-Zip** (`7z.exe`), **WinRAR** (`UnRAR.exe`), Windows built-in `tar.exe`, or `7z.exe` + `7z.dll` placed next to the executable.

Build a standalone `.exe` with PyInstaller (bundling `7z`):
```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --add-binary "7z.exe;." --add-binary "7z.dll;." game_mod_installer.py
```
