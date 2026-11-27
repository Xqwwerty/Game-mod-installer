"""Тесты логики Game Mod Installer (без GUI)."""
import json
import zipfile
from pathlib import Path

import pytest

import game_mod_installer as m


# ----------------------------------------------------------------- fixtures --
@pytest.fixture
def env(tmp_path, monkeypatch):
    """Изолирует конфиг, БД установок, бэкапы и пользовательские профили во временной папке."""
    cfg = tmp_path / "cfg"
    monkeypatch.setattr(m, "CONFIG_DIR", cfg)
    monkeypatch.setattr(m, "DB_FILE", cfg / "installs.json")
    monkeypatch.setattr(m, "BACKUP_DIR", cfg / "backups")
    monkeypatch.setattr(m, "GAMES_FILE", cfg / "games.json")
    monkeypatch.setattr(m, "GAMES_DIR", cfg / "games.d")
    monkeypatch.setattr(m, "LANG_DIR", cfg / "lang")
    return cfg


@pytest.fixture
def game(tmp_path):
    g = tmp_path / "game"
    g.mkdir()
    return g


def make_zip(path: Path, files: dict) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return path


def make_dir(root: Path, files: dict) -> Path:
    for name, content in files.items():
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return root


def noop(_msg):
    pass


def tree(root: Path) -> list:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


# ------------------------------------------------------ 1. защита от Zip-Slip --
def test_zip_slip_is_blocked(tmp_path):
    z = make_zip(tmp_path / "evil.zip", {
        "../evil.txt": "x",
        "sub/../../evil2.txt": "x",
        "good/a.txt": "ok",
    })
    outer = tmp_path / "out"
    dest = outer / "inner"
    dest.mkdir(parents=True)

    m.extract_zip(z, dest)

    assert (dest / "good" / "a.txt").read_text() == "ok"
    assert not (outer / "evil.txt").exists()
    assert not (outer / "evil2.txt").exists()
    assert not (tmp_path / "evil.txt").exists()
    assert [p.name for p in outer.iterdir()] == ["inner"]  # за пределы dest ничего не попало


# ------------------------------------------------------------ 2. FOMOD-детектор --
def test_fomod_archive_is_rejected(env, game, tmp_path):
    z = make_zip(tmp_path / "fomod_mod.zip", {
        "fomod/ModuleConfig.xml": "<config/>",
        "00 Core/Data/core.esp": "1",
        "01 Option/Data/option.esp": "1",
    })
    profile = m.load_profiles()["skyrimse"]

    with pytest.raises(m.ModError) as exc:
        m.install_source(profile, z, game, noop)

    assert exc.value.key == "err_fomod"
    assert tree(game) == []  # ничего не скопировано
    assert m.db_records("skyrimse") == []


def test_simple_fomod_next_to_data_is_ignored(env, game, tmp_path):
    z = make_zip(tmp_path / "simple.zip", {
        "fomod/info.xml": "<fomod/>",
        "Data/meshes/a.nif": "1",
        "Readme.txt": "docs",
    })
    profile = m.load_profiles()["skyrimse"]

    assert m.install_source(profile, z, game, noop) is True
    assert tree(game) == ["Data/meshes/a.nif"]  # ни fomod/, ни Readme.txt


# ------------------------------------------------------ 3. раскладка folder/root --
def test_layout_folder_vs_root(env, tmp_path):
    profiles = m.load_profiles()

    # folder: каждый мод -> отдельная папка внутри target
    src = make_dir(tmp_path / "a", {"ModX/manifest.json": "{}", "ModX/ModX.dll": "1"})
    plan = m.make_plan(profiles["stardew"], src, "a")
    assert [(op.src.name, op.dst) for op in plan.ops] == [("ModX", "Mods/ModX")]

    # folder + wrapper: содержимое lml/ мёржится в lml
    src = make_dir(tmp_path / "b", {"lml/ModA/install.xml": "1"})
    plan = m.make_plan(profiles["rdr2"], src, "b")
    assert [op.dst for op in plan.ops] == ["lml"]

    # folder без признаков мода: оборачиваем в папку с именем архива
    src = make_dir(tmp_path / "c", {"a.package": "1"})
    plan = m.make_plan(profiles["sims4"], src, "mymod")
    assert [op.dst for op in plan.ops] == ["Mods/mymod"]

    # root: структура архива относительно корня игры копируется как есть
    src = make_dir(tmp_path / "d", {"ModName/archive/pc/mod/x.archive": "1"})
    plan = m.make_plan(profiles["cp2077"], src, "d")
    assert [op.dst for op in plan.ops] == [""] and plan.ops[0].src.name == "ModName"

    # root: одиночный файл раскладывается по расширению
    src = make_dir(tmp_path / "e", {"loose.archive": "1"})
    plan = m.make_plan(profiles["cp2077"], src, "e")
    assert [(f.name, dst) for f, dst in plan.loose] == [("loose.archive", "archive/pc/mod")]

    # root: не удалось понять раскладку -> ошибка, а не копирование наугад
    src = make_dir(tmp_path / "f", {"notes.txt": "1"})
    with pytest.raises(m.ModError) as exc:
        m.make_plan(profiles["cp2077"], src, "f")
    assert exc.value.key == "err_layout"


# ------------------------------------------------------------ 4. движковые пресеты --
def test_bepinex_preset_fields():
    prof = m.sanitize_profile({"id": "valheim", "name": "Valheim", "engine": "bepinex"})
    assert prof["mode"] == "root"
    assert prof["target"] == "BepInEx/plugins"
    assert prof["markers"] == ["BepInEx", "plugins", ".dll"]
    assert prof["loose"] == {".dll": "BepInEx/plugins"}


def test_bepinex_install_layouts(env, game, tmp_path):
    prof = m.sanitize_profile({"id": "valheim", "name": "Valheim", "engine": "bepinex"})

    # одиночная dll -> BepInEx/plugins
    m.install_source(prof, make_zip(tmp_path / "one.zip", {"MyMod.dll": "1"}), game, noop)
    # архив уже от корня игры
    m.install_source(prof, make_zip(tmp_path / "full.zip", {
        "BepInEx/plugins/Other/Other.dll": "1", "BepInEx/config/other.cfg": "1"}), game, noop)
    # архив содержит папку plugins
    m.install_source(prof, make_zip(tmp_path / "pl.zip", {"plugins/Third.dll": "1"}), game, noop)

    assert tree(game) == [
        "BepInEx/config/other.cfg",
        "BepInEx/plugins/MyMod.dll",
        "BepInEx/plugins/Other/Other.dll",
        "BepInEx/plugins/Third.dll",
    ]


@pytest.mark.parametrize("engine, archive, expected", [
    ("melonloader", {"Cool.dll": "1"}, ["Mods/Cool.dll"]),
    ("melonloader", {"Mods/Z.dll": "1"}, ["Mods/Z.dll"]),
    ("re_engine", {"reframework/autorun/a.lua": "1"}, ["reframework/autorun/a.lua"]),
    ("re_engine", {"b.lua": "1"}, ["reframework/autorun/b.lua"]),
    ("unreal_engine", {"x.pak": "1", "x.ucas": "1", "x.utoc": "1"}, ["~mods/x.pak", "~mods/x.ucas", "~mods/x.utoc"]),
    ("unreal_engine", {"~mods/y.pak": "1"}, ["~mods/y.pak"]),
])
def test_other_engine_presets(env, game, tmp_path, engine, archive, expected):
    prof = m.sanitize_profile({"id": "g_" + engine, "name": "G", "engine": engine})
    m.install_source(prof, make_zip(tmp_path / "mod.zip", archive), game, noop)
    assert tree(game) == expected


def test_games_d_import_and_validation(env):
    m.GAMES_DIR.mkdir(parents=True)
    (m.GAMES_DIR / "pack.json").write_text(json.dumps([
        {"id": "pack_game", "name": "Pack Game", "engine": "bepinex"},
        {"id": "evil", "name": "Evil", "mode": "root", "target": "../outside"},   # выход из папки игры
        {"id": "../evil", "name": "Evil2", "mode": "root"},                        # небезопасный id
    ]), encoding="utf-8")
    (m.GAMES_DIR / "single.json").write_text(json.dumps(
        {"id": "single_game", "name": "Single", "mode": "folder", "target": "Mods"}), encoding="utf-8")
    (m.GAMES_DIR / "broken.json").write_text("{not json", encoding="utf-8")

    profiles = m.load_profiles()

    assert profiles["pack_game"]["target"] == "BepInEx/plugins"
    assert profiles["single_game"]["custom"] is True
    assert "evil" not in profiles and "../evil" not in profiles
    assert "rdr2" in profiles  # встроенные профили на месте


# ------------------------------------------ 5. Tracker и цепочка бэкапов модов --
def _chain_profile():
    return m.sanitize_profile({"id": "chain", "name": "Chain", "mode": "root", "target": "", "markers": ["data"]})


def _install(prof, game, tmp_path, name, content, rel="data/x.txt"):
    src = make_dir(tmp_path / name, {rel: content})
    assert m.install_source(prof, src, game, noop) is True
    return next(r for r in m.db_records(prof["id"]) if r["name"] == name)


def _remove(prof, game, rec):
    m.uninstall_record(prof, game, rec)
    m.db_remove(prof["id"], rec["id"])


def test_backup_chain_vanilla_restored(env, game, tmp_path):
    prof = _chain_profile()
    x = game / "data" / "x.txt"
    x.parent.mkdir()
    x.write_text("vanilla")

    rec_a = _install(prof, game, tmp_path, "ModA", "A")
    assert x.read_text() == "A"
    rec_b = _install(prof, game, tmp_path, "ModB", "B")
    assert x.read_text() == "B"

    # A перед удалением передаёт свой бэкап (vanilla) моду B; файл B остаётся
    _remove(prof, game, rec_a)
    assert x.read_text() == "B"
    rec_b = m.db_records("chain")[0]
    assert "data/x.txt" in rec_b["backups"]
    assert (m.BACKUP_DIR / "chain" / rec_b["id"] / "data" / "x.txt").read_text() == "vanilla"

    # удаление B восстанавливает оригинальный файл
    _remove(prof, game, rec_b)
    assert x.read_text() == "vanilla"
    assert m.db_records("chain") == []


def test_backup_chain_reverse_order(env, game, tmp_path):
    prof = _chain_profile()
    x = game / "data" / "x.txt"
    x.parent.mkdir()
    x.write_text("vanilla")

    rec_a = _install(prof, game, tmp_path, "ModA", "A")
    rec_b = _install(prof, game, tmp_path, "ModB", "B")

    _remove(prof, game, rec_b)  # сначала последний: возвращается версия A
    assert x.read_text() == "A"
    _remove(prof, game, m.db_records("chain")[0])
    assert x.read_text() == "vanilla"


def test_backup_chain_file_created_by_mod(env, game, tmp_path):
    prof = _chain_profile()
    x = game / "data" / "y.txt"  # в «ванильной» игре этого файла нет

    rec_a = _install(prof, game, tmp_path, "ModA", "A", rel="data/y.txt")
    _install(prof, game, tmp_path, "ModB", "B", rel="data/y.txt")

    _remove(prof, game, rec_a)
    assert x.read_text() == "B"
    rec_b = m.db_records("chain")[0]
    assert rec_b["backups"] == []  # оригинала не было — B теперь «создатель» файла

    _remove(prof, game, rec_b)
    assert not x.exists()


def test_tracker_backs_up_only_overwritten_files(env, game, tmp_path):
    (game / "existing.txt").write_text("orig")
    src = make_dir(tmp_path / "src", {"existing.txt": "new", "fresh.txt": "n"})
    tr = m.Tracker(game, tmp_path / "bk")

    tr.copy(src / "existing.txt", game / "existing.txt")
    tr.copy(src / "fresh.txt", game / "fresh.txt")
    tr.copy(src / "existing.txt", game / "existing.txt")  # повторное копирование не затирает бэкап

    assert tr.files == ["existing.txt", "fresh.txt"]
    assert tr.backups == ["existing.txt"]
    assert (tmp_path / "bk" / "existing.txt").read_text() == "orig"


# --------------------------------------------------------- конфликты файлов --
def test_conflict_detection_and_confirmation(env, game, tmp_path):
    prof = _chain_profile()
    _install(prof, game, tmp_path, "ModA", "A")
    mod_b = make_dir(tmp_path / "ModB", {"data/x.txt": "B", "data/own.txt": "B"})
    x = game / "data" / "x.txt"

    seen = []

    def refuse(conflicts):
        seen.append(conflicts)
        return False

    assert m.install_source(prof, mod_b, game, noop, refuse) is False
    assert seen == [[("ModA", ["data/x.txt"])]]  # владелец и список файлов
    assert x.read_text() == "A"
    assert [r["name"] for r in m.db_records("chain")] == ["ModA"]

    assert m.install_source(prof, mod_b, game, noop, lambda c: True) is True
    assert x.read_text() == "B"
    assert [r["name"] for r in m.db_records("chain")] == ["ModA", "ModB"]


def test_reinstalling_same_mod_is_not_a_conflict(env, game, tmp_path):
    prof = _chain_profile()
    src = make_dir(tmp_path / "ModA", {"data/x.txt": "A1"})
    assert m.install_source(prof, src, game, noop, lambda c: pytest.fail("unexpected conflict"))
    (src / "data" / "x.txt").write_text("A2")
    assert m.install_source(prof, src, game, noop, lambda c: pytest.fail("unexpected conflict"))
    assert (game / "data" / "x.txt").read_text() == "A2"
    assert len(m.db_records("chain")) == 1


# ------------------------------------------------------- темы и локализация (v3.2) --
THEME_KEYS = {"bg", "surface", "surface2", "hover", "fg", "muted", "accent", "accent_hover",
              "on_accent", "accent2", "border", "ok", "warn", "err", "is_dark"}


def test_theme_palettes_are_complete():
    import re
    assert set(m.THEME_ORDER) == set(m.THEMES)
    assert {"dark", "light", "oled", "cyberpunk", "dracula", "nord", "outlaw"} <= set(m.THEMES)
    for name, palette in m.THEMES.items():
        assert THEME_KEYS <= set(palette), name
        for key in THEME_KEYS - {"is_dark"}:
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", palette[key]), (name, key)
    assert m.THEMES["oled"]["bg"] == "#000000"
    assert m.THEMES["cyberpunk"]["accent"] == "#fcee0a" and m.THEMES["cyberpunk"]["accent2"] == "#00f0ff"
    assert m.THEMES["light"]["is_dark"] is False


def test_translations_cover_all_ui_keys():
    import re
    src = Path(m.__file__).read_text(encoding="utf-8")
    keys = set(re.findall(r'\bT\(\s*"([a-z0-9_]+)"', src))
    keys |= set(re.findall(r'\("(log_[a-z_]+)"', src))         # предупреждения плана установки
    keys |= set(re.findall(r'ModError\(\s*"([a-z0-9_]+)"', src))
    keys = {k for k in keys if not k.endswith("_")}               # динамические ключи (T("theme_" + name))
    keys |= {f"theme_{n}" for n in m.THEME_ORDER}
    keys |= {p["note"] for p in m.load_profiles().values() if p.get("note")}
    keys |= {"installed", "log"}  # заголовки нижних панелей
    keys |= {"log_done_ok", "log_done_part"}
    assert {"ru", "en", "uk", "de", "fr", "es", "zh"} <= set(m.LANG_NAMES)
    for lang in ("ru", "en", "uk", "de", "fr", "es", "zh"):
        assert sorted(k for k in keys if k not in m.STR[lang]) == [], lang


def test_translation_placeholders_match_english():
    import re
    for lang in ("ru", "uk", "de", "fr", "es", "zh"):
        for key, text in m.STR[lang].items():
            if key in m.STR["en"]:
                assert set(re.findall(r"{(\w+)}", text)) == set(re.findall(r"{(\w+)}", m.STR["en"][key])), (lang, key)


def test_translate_helper_and_placeholder_text():
    old = m.I18N.lang
    try:
        m.I18N.lang = "ru"
        assert m.T("choose_game") == "Выберите игру..."
        assert m.T("st_no_game") == "Сначала выберите игру из списка выше"
        m.I18N.lang = "en"
        assert m.T("choose_game") == "Choose game..."
        assert m.T("installing", i=1, n=2, name="x") == "Installing 1/2: x"
        m.I18N.lang = "zh"
        assert m.T("search_title") == "查找游戏"
        assert m.T("no_such_key") == "no_such_key"  # неизвестный ключ не ломает UI
    finally:
        m.I18N.lang = old


def test_module_imports_without_tk_and_version():
    # CI/headless: логика доступна даже если tkinter недоступен
    assert m.APP_VERSION == "2.0"
    assert hasattr(m, "App") and callable(m.install_source)


# ============================================================ v2.0: новая логика ==
def _two_mods_over_vanilla(env, game, tmp_path):
    prof = _chain_profile()
    x = game / "data" / "x.txt"
    x.parent.mkdir()
    x.write_text("vanilla")
    rec_a = _install(prof, game, tmp_path, "ModA", "A")
    rec_b = _install(prof, game, tmp_path, "ModB", "B")
    return prof, x, rec_a, rec_b


# ------------------------------------------------------------- Ванильный режим --
def test_vanilla_mode_roundtrip(env, game, tmp_path):
    prof, x, rec_a, rec_b = _two_mods_over_vanilla(env, game, tmp_path)
    own = make_dir(tmp_path / "ModC", {"data/c_only.txt": "C"})
    assert m.install_source(prof, own, game, noop) is True  # файл, созданный модом

    assert m.vanilla_enable(prof, game) == 2
    assert x.read_text() == "vanilla"                    # на диске чистый оригинал
    assert not (game / "data" / "c_only.txt").exists()
    assert m.vanilla_state("chain", game)["vanilla_mode"] is True
    stash = m._stash_root() / "chain" / m.game_hash(game)
    assert (stash / "data" / "x.txt").read_text() == "B"  # моды лежат в stash

    # в ванильном режиме установка блокируется, повторное включение тоже
    with pytest.raises(m.ModError) as exc:
        m.install_source(prof, make_dir(tmp_path / "ModD", {"data/d.txt": "D"}), game, noop)
    assert exc.value.key == "err_vanilla_active"
    with pytest.raises(m.ModError):
        m.vanilla_enable(prof, game)

    assert m.vanilla_disable(prof, game) == 2
    assert x.read_text() == "B" and (game / "data" / "c_only.txt").read_text() == "C"
    assert m.vanilla_state("chain", game) is None and not stash.exists()
    assert [r["name"] for r in m.db_records("chain")] == ["ModA", "ModB", "ModC"]

    # после возврата модов они корректно удаляются (цепочка бэкапов цела)
    _remove(prof, game, next(r for r in m.db_records("chain") if r["name"] == "ModC"))
    _remove(prof, game, next(r for r in m.db_records("chain") if r["name"] == "ModA"))
    assert x.read_text() == "B"
    _remove(prof, game, m.db_records("chain")[0])
    assert x.read_text() == "vanilla" and not (game / "data" / "c_only.txt").exists()


def test_vanilla_disable_without_state_is_noop(env, game):
    assert m.vanilla_disable(_chain_profile(), game) == 0


# ------------------------------------------------------------- Защита сохранений --
def _saves_profile(saves_dir):
    return m.sanitize_profile({"id": "sv", "name": "Sv", "mode": "root", "saves": [str(saves_dir)]})


def test_save_protector_backup_rotation_and_restore(env, tmp_path):
    saves = tmp_path / "Saves"
    (saves / "slot1").mkdir(parents=True)
    (saves / "slot1" / "a.sav").write_text("v1")
    (saves / "b.sav").write_text("b1")
    prof = _saves_profile(saves)

    snaps = []
    for i in range(4):
        (saves / "slot1" / "a.sav").write_text(f"v{i + 1}")
        path, n = m.backup_saves(prof, limit=2)
        assert n == 2
        snaps.append(path)

    kept = m.list_save_backups("sv")
    assert kept == [snaps[3], snaps[2]]               # ротация: остаются 2 новых, самые свежие первыми
    assert not snaps[0].exists() and not snaps[1].exists()

    (saves / "slot1" / "a.sav").write_text("BROKEN")
    (saves / "new.sav").write_text("later")
    assert m.restore_save_backup(prof, snaps[2], limit=5) == 2
    assert (saves / "slot1" / "a.sav").read_text() == "v3"
    assert any(p.name.endswith("prerestore.zip") for p in m.list_save_backups("sv"))  # откат восстановления возможен


def test_save_protector_without_saves(env, tmp_path):
    prof = m.sanitize_profile({"id": "ns", "name": "NS", "mode": "root"})
    assert m.save_dirs(prof) == [] and m.backup_saves(prof) is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert m.backup_saves(_saves_profile(empty)) is None  # пустая папка — снимок не создаётся


def test_save_restore_ignores_unknown_sources(env, tmp_path):
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "a.sav").write_text("1")
    prof = _saves_profile(saves)
    path, _ = m.backup_saves(prof)
    other = m.sanitize_profile({"id": "sv", "name": "Sv", "mode": "root", "saves": [str(tmp_path / "Elsewhere")]})
    (tmp_path / "Elsewhere").mkdir()
    assert m.restore_save_backup(other, path) == 0  # чужая папка из манифеста не затрагивается


def test_autobackup_before_install(env, game, tmp_path):
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "a.sav").write_text("1")
    prof = m.sanitize_profile({"id": "ab", "name": "AB", "mode": "root", "target": "", "markers": ["data"],
                               "saves": [str(saves)]})
    src = make_dir(tmp_path / "ModA", {"data/x.txt": "A"})
    assert m.install_source(prof, src, game, noop, settings={"autobackup_saves": True, "save_backup_limit": 3})
    assert len(m.list_save_backups("ab")) == 1


# ------------------------------------------------------------ Доктор зависимостей --
def test_dependency_checker(env, game):
    prof = m.load_profiles()["cp2077"]
    assert prof["deps"] == ["bin/x64/winmm.dll", "red4ext", "r6/scripts"]
    assert m.check_deps(prof, game) == [("bin/x64/winmm.dll", False), ("red4ext", False), ("r6/scripts", False)]

    (game / "bin" / "x64").mkdir(parents=True)
    (game / "bin" / "x64" / "winmm.dll").write_text("x")
    (game / "red4ext").mkdir()
    assert m.check_deps(prof, game) == [("bin/x64/winmm.dll", True), ("red4ext", True), ("r6/scripts", False)]

    bep = m.sanitize_profile({"id": "val", "name": "Val", "engine": "bepinex"})
    assert [d for d, _ in m.check_deps(bep, game)] == ["winhttp.dll", "BepInEx"]
    mel = m.sanitize_profile({"id": "mel", "name": "Mel", "engine": "melonloader"})
    assert mel["deps"] == ["version.dll", "MelonLoader"]
    assert m.check_deps(m.sanitize_profile({"id": "nd", "name": "ND", "mode": "root"}), game) == []


def test_builtin_profiles_define_expected_deps_and_saves(env):
    profiles = m.load_profiles()
    assert profiles["skyrimse"]["deps"] == ["skse64_loader.exe"]
    assert profiles["stardew"]["deps"] == ["StardewModdingAPI.exe"]
    assert profiles["rdr2"]["deps"] == ["ScriptHookRDR2.dll", "lml"]
    assert profiles["gta5"]["deps"] == ["ScriptHookV.dll", "dinput8.dll"]
    assert profiles["rdr2"]["saves"] and profiles["stardew"]["saves"]
    bad = m.sanitize_profile({"id": "x", "name": "X", "mode": "root", "deps": ["../../evil.dll"]})
    assert bad is None  # зависимости не могут выходить за папку игры


# ---------------------------------------------------- Предпросмотр: фильтрация файлов --
def test_preview_excludes_selected_files(env, game, tmp_path):
    prof = m.load_profiles()["skyrimse"]
    src = make_dir(tmp_path / "ModP", {"Data/meshes/a.nif": "1", "Data/textures/t.dds": "1",
                                       "Data/screenshots/shot.png": "1", "Data/Mod.esp": "1"})
    seen = {}

    def preview(entries):
        seen["entries"] = entries
        return {dest for _, dest in entries if "screenshots" in dest}

    assert m.install_source(prof, src, game, noop, preview=preview) is True
    assert ("Data/meshes/a.nif", "Data/meshes/a.nif") in seen["entries"]
    assert tree(game) == ["Data/Mod.esp", "Data/meshes/a.nif", "Data/textures/t.dds"]
    rec = m.db_records("skyrimse")[0]
    assert "Data/screenshots/shot.png" not in rec["files"]


def test_preview_cancel_and_plan_filter(env, game, tmp_path):
    prof = m.load_profiles()["skyrimse"]
    src = make_dir(tmp_path / "ModQ", {"Data/Mod.esp": "1"})
    assert m.install_source(prof, src, game, noop, preview=lambda entries: None) is False
    assert tree(game) == [] and m.db_records("skyrimse") == []
    assert m.install_source(prof, src, game, noop, preview=lambda entries: {e[1] for e in entries}) is False

    plan = m.make_plan(prof, src, "ModQ")
    assert m.plan_dest_files(plan) == ["Data/Mod.esp"]
    plan.excluded.add("data/mod.esp")
    assert m.plan_dest_files(plan) == []


def test_excluded_files_do_not_cause_conflicts(env, game, tmp_path):
    prof = _chain_profile()
    _install(prof, game, tmp_path, "ModA", "A")
    mod_b = make_dir(tmp_path / "ModB", {"data/x.txt": "B", "data/own.txt": "B"})
    asked = []
    ok = m.install_source(prof, mod_b, game, noop, confirm=lambda c: asked.append(c) or False,
                          preview=lambda entries: {"data/x.txt"})
    assert ok is True and asked == []  # конфликтующий файл исключён — вопроса нет
    assert (game / "data" / "x.txt").read_text() == "A"


def test_conflict_warn_mode_logs_without_dialog(env, game, tmp_path):
    prof = _chain_profile()
    _install(prof, game, tmp_path, "ModA", "A")
    logs = []
    mod_b = make_dir(tmp_path / "ModB", {"data/x.txt": "B"})
    assert m.install_source(prof, mod_b, game, logs.append, confirm=lambda c: pytest.fail("no dialog expected"),
                            settings={"conflict_mode": "warn"})
    assert any("ModA" in line and "data/x.txt" in line for line in logs) or any("log_conflict_warn" in line for line in logs)
    assert (game / "data" / "x.txt").read_text() == "B"


# ------------------------------------------------------------ Экспорт/импорт сборок --
def test_gmpack_export_and_import(env, game, tmp_path):
    prof, x, rec_a, rec_b = _two_mods_over_vanilla(env, game, tmp_path)
    cfg_mod = make_dir(tmp_path / "ModCfg", {"data/own.txt": "own", "data/settings.ini": "[a]\nenabled=true\n"})
    assert m.install_source(prof, cfg_mod, game, noop)
    pack = tmp_path / "Chain_modpack.gmpack.zip"

    assert m.export_gmpack(prof, game, pack) == 3
    with zipfile.ZipFile(pack) as z:
        manifest = json.loads(z.read("gmpack.json"))
        assert manifest["gid"] == "chain" and [mod["name"] for mod in manifest["mods"]] == ["ModA", "ModB", "ModCfg"]
        assert z.read("mods/0/data/x.txt") == b"A"   # версия мода A, а не перезаписавшего её B
        assert z.read("mods/1/data/x.txt") == b"B"
    assert m.is_gmpack(pack) and m.is_supported_source(pack)

    # чистая установка игры -> импорт сборки
    game2 = tmp_path / "game2"
    (game2 / "data").mkdir(parents=True)
    (game2 / "data" / "x.txt").write_text("vanilla2")
    assert m.install_source(prof, pack, game2, noop) is True
    assert (game2 / "data" / "x.txt").read_text() == "B"
    assert (game2 / "data" / "settings.ini").read_text() == "[a]\nenabled=true\n"
    recs = [r for r in m.db_records("chain") if r["game"] == str(game2)]
    assert [r["name"] for r in recs] == ["ModA", "ModB", "ModCfg"]

    # каждый мод отслеживается отдельно: удаляем по цепочке -> ванильный файл
    for name in ("ModA", "ModB", "ModCfg"):
        _remove(prof, game2, next(r for r in m.db_records("chain") if r["game"] == str(game2) and r["name"] == name))
    assert (game2 / "data" / "x.txt").read_text() == "vanilla2" and not (game2 / "data" / "own.txt").exists()


def test_gmpack_wrong_game_and_broken(env, game, tmp_path):
    prof, *_ = _two_mods_over_vanilla(env, game, tmp_path)
    pack = tmp_path / "p.gmpack.zip"
    m.export_gmpack(prof, game, pack)
    other = m.sanitize_profile({"id": "other", "name": "Other", "mode": "root", "markers": ["data"]})
    with pytest.raises(m.ModError) as exc:
        m.install_source(other, pack, tmp_path / "g3", noop)
    assert exc.value.key == "err_pack_game"
    broken = tmp_path / "b.gmpack.zip"
    broken.write_bytes(b"not a zip")
    with pytest.raises(m.ModError) as exc:
        m.install_source(prof, broken, game, noop)
    assert exc.value.key == "err_pack_bad"
    with pytest.raises(m.ModError) as exc:
        m.export_gmpack(prof, tmp_path / "empty_game", tmp_path / "x.gmpack.zip")
    assert exc.value.key == "err_pack_empty"


def test_gmpack_zip_slip_is_blocked(env, game, tmp_path):
    prof = _chain_profile()
    pack = tmp_path / "evil.gmpack.zip"
    with zipfile.ZipFile(pack, "w") as z:
        z.writestr("gmpack.json", json.dumps({"format": 1, "gid": "chain", "mods": [
            {"name": "Evil", "archive": "Evil", "files": []}]}))
        z.writestr("mods/0/../../escape.txt", "x")
        z.writestr("mods/0/data/ok.txt", "ok")
    m.install_source(prof, pack, game, noop)
    assert not (tmp_path / "escape.txt").exists() and not (game.parent / "escape.txt").exists()
    assert (game / "data" / "ok.txt").read_text() == "ok"


# --------------------------------------------------------------- Конфиги модов --
def test_find_and_edit_mod_configs(env, game, tmp_path):
    prof = _chain_profile()
    src = make_dir(tmp_path / "ModCfg", {
        "data/settings.ini": "; comment\n[general]\nenabled = true ; inline\nspeed=5\n\n[misc]\nname=Bob\n",
        "data/options.json": '{"a": 1}',
        "data/readme.dat": "x",
    })
    assert m.install_source(prof, src, game, noop)
    rec = m.db_records("chain")[0]

    found = m.find_mod_configs(game, rec["files"])
    assert [p.name for p in found] == ["options.json", "settings.ini"]
    assert [p.name for p in m.scan_folder_configs(game / "data")] == ["options.json", "settings.ini"]

    ini = game / "data" / "settings.ini"
    text, nl, enc = m.read_text_file(ini)
    rows = m.parse_ini(text)
    assert [(r["section"], r["key"], r["value"], r["bool"]) for r in rows] == [
        ("general", "enabled", "true", True), ("general", "speed", "5", False), ("misc", "name", "Bob", False)]
    new = m.apply_ini_edits(text, {rows[0]["line"]: "false", rows[1]["line"]: "9"})
    assert "enabled = false ; inline" in new and "speed=9" in new and "; comment" in new

    m.write_text_file(ini, new, nl, enc)
    assert ini.read_text() == new
    assert ini.with_name("settings.ini.bak").read_text() == text          # резервная копия оригинала
    assert not ini.with_name("settings.ini.gmi_tmp").exists()


def test_config_writer_preserves_crlf_and_bom_and_validates(env, tmp_path):
    f = tmp_path / "c.cfg"
    f.write_bytes(b"\xef\xbb\xbfkey=1\r\nflag=true\r\n")
    text, nl, enc = m.read_text_file(f)
    assert text == "key=1\nflag=true\n" and nl == "\r\n" and enc == "utf-8-sig"
    m.write_text_file(f, m.apply_ini_edits(text, {1: "false"}), nl, enc)
    assert f.read_bytes() == b"\xef\xbb\xbfkey=1\r\nflag=false\r\n"

    assert m.validate_config_text(Path("a.json"), '{"a": 1}') is None
    assert m.validate_config_text(Path("a.json"), "{bad") is not None
    assert m.validate_config_text(Path("a.xml"), "<a><b></a>") is not None
    assert m.validate_config_text(Path("a.ini"), "anything") is None


# ------------------------------------------------------------ Загрузки и настройки --
def test_recent_archives_only_first_level_sorted(tmp_path):
    import os
    d = tmp_path / "Downloads"
    (d / "sub").mkdir(parents=True)
    names = ["a.zip", "b.7z", "c.rar", "d.txt", "e.part"]
    for i, n in enumerate(names):
        (d / n).write_text("x")
        os.utime(d / n, (1000 + i, 1000 + i))
    (d / "sub" / "deep.zip").write_text("x")
    os.utime(d / "sub" / "deep.zip", (9999, 9999))
    got = m.recent_archives(d, limit=2)
    assert [r["path"].name for r in got] == ["c.rar", "b.7z"]      # новые первыми, вложенные папки не читаются
    assert m.recent_archives(tmp_path / "missing") == []
    assert isinstance(m.get_downloads_dir(), Path)


def test_settings_defaults_and_validation():
    s = m.get_settings({})
    assert s == m.DEFAULT_SETTINGS and s["save_backup_limit"] == 5
    s = m.get_settings({"settings": {"save_backup_limit": 999, "conflict_mode": "boom", "preview_tree": True,
                                     "show_doctor": "yes"}})
    assert s["save_backup_limit"] == 50 and s["conflict_mode"] == "ask"
    assert s["preview_tree"] is True and s["show_doctor"] is True  # неверный тип -> значение по умолчанию


# --------------------------------------------------------------------- Макет --




def test_mix_color_and_lazy_py7zr():
    assert m.mix_color("#000000", "#ffffff", 0.5) == "#808080"
    assert m.mix_color("#102030", "#102030", 0.7) == "#102030"
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert not any(line.startswith(("import py7zr", "from py7zr")) for line in src.splitlines())  # нет импорта на верхнем уровне
    assert callable(m.get_py7zr)




def test_heavy_modules_are_not_imported_at_startup():
    import subprocess
    import sys
    code = ("import sys; sys.path.insert(0, %r); import game_mod_installer;"
            "print('py7zr' in sys.modules, 'tkinterdnd2' in sys.modules)" % str(Path(m.__file__).parent))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.split()
    assert out == ["False", "False"]


# ====================================================== исправления эргономики и Vanilla ==
def _folder_profile():
    """Как RDR2/LML: каждый мод — отдельная папка внутри lml."""
    return m.sanitize_profile({"id": "fold", "name": "Fold", "mode": "folder", "target": "lml",
                               "wrappers": ["lml"], "markers": ["install.xml"]})


def test_vanilla_cycle_folder_mode(env, game, tmp_path):
    prof = _folder_profile()
    (game / "lml").mkdir()
    shared = game / "lml" / "ModA" / "shared.ini"
    shared.parent.mkdir()
    shared.write_text("vanilla-ish")  # файл, который мод перезапишет
    mod_a = make_dir(tmp_path / "ModA", {"install.xml": "<a/>", "shared.ini": "A", "a_only.txt": "a"})
    mod_b = make_dir(tmp_path / "ModB", {"install.xml": "<b/>", "b_only.txt": "b"})
    assert m.install_source(prof, mod_a, game, noop) and m.install_source(prof, mod_b, game, noop)
    assert shared.read_text() == "A"

    files_on = m.vanilla_enable(prof, game)
    assert files_on >= 4
    assert shared.read_text() == "vanilla-ish"                       # ванильный файл восстановлен из бэкапа
    assert not (game / "lml" / "ModA" / "a_only.txt").exists()       # файлы модов убраны с диска
    assert not (game / "lml" / "ModB").exists()                      # пустые папки модов подчищены
    assert (game / "lml").is_dir()                                   # сама папка lml остаётся
    state = m.vanilla_state("fold", game)
    assert state["vanilla_mode"] is True and sorted(state["mods"]) == ["ModA", "ModB"]
    stash = m._stash_root() / "fold" / m.game_hash(game)
    assert (stash / "lml" / "ModA" / "a_only.txt").read_text() == "a"

    assert m.disable_vanilla(prof, game) == files_on
    assert shared.read_text() == "A" and (game / "lml" / "ModB" / "b_only.txt").read_text() == "b"
    assert m.vanilla_state("fold", game) is None and not stash.exists()

    for name in ("ModB", "ModA"):                                    # бэкапы целы: удаление работает
        _remove(prof, game, next(r for r in m.db_records("fold") if r["name"] == name))
    assert shared.read_text() == "vanilla-ish" and not (game / "lml" / "ModB").exists()


def test_vanilla_without_managed_mods_is_explained(env, game):
    prof = _chain_profile()
    with pytest.raises(m.ModError) as exc:
        m.vanilla_enable(prof, game)
    assert exc.value.key == "vanilla_nothing"                          # а не «молчаливое ничего»
    assert m.vanilla_state("chain", game) is None


def test_vanilla_enable_rolls_back_on_error(env, game, tmp_path, monkeypatch):
    prof, x, rec_a, rec_b = _two_mods_over_vanilla(env, game, tmp_path)
    (game / "data" / "y.txt").write_text("Y")
    own = make_dir(tmp_path / "ModY", {"data/y2.txt": "Y2"})
    assert m.install_source(prof, own, game, noop)
    real_move, calls = m.shutil.move, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk error")
        return real_move(src, dst)

    monkeypatch.setattr(m.shutil, "move", flaky)
    with pytest.raises(OSError):
        m.vanilla_enable(prof, game)
    monkeypatch.setattr(m.shutil, "move", real_move)
    assert m.vanilla_state("chain", game) is None                      # состояние не «залипло»
    assert x.read_text() == "B" and (game / "data" / "y2.txt").read_text() == "Y2"   # моды на месте
    assert m.vanilla_enable(prof, game) >= 2                           # и повторная попытка работает
    assert m.vanilla_disable(prof, game) >= 2


def test_vanilla_state_is_per_game_path(env, game, tmp_path):
    prof, *_ = _two_mods_over_vanilla(env, game, tmp_path)
    m.vanilla_enable(prof, game)
    assert m.vanilla_state("chain", game) and m.vanilla_state("chain", tmp_path / "other_game") is None
    assert m.game_hash(game) != m.game_hash(tmp_path / "other_game")
    m.vanilla_disable(prof, game)


# ------------------------------------------------------------ макет и запуск без игры --
def test_layout_presets_and_normalization():
    d = m.default_layout()
    assert d == {"arrange": "side", "swap": False, "sash": None}
    assert m.bottom_order(d) == ("mods", "log")
    assert m.bottom_order(m.swap_panels(d)) == ("log", "mods")
    stacked = m.set_arrangement(d, "stack")
    assert stacked["arrange"] == "stack" and stacked["sash"] is None
    assert m.set_arrangement(stacked, "stack") is stacked and m.set_arrangement(d, "bogus") is d
    assert m.swap_panels(m.swap_panels(d))["swap"] is False
    assert m.default_sash(d) > 0.5 > m.default_sash(m.swap_panels(d))   # «Моды» всегда получают больше места
    # старый формат (v2.0 с rows), мусор и неверные значения -> безопасный макет
    assert m.normalize_layout({"rows": [["log"]], "hidden": []}) == d
    assert m.normalize_layout("junk") == d
    assert m.normalize_layout({"arrange": "stack", "swap": True, "sash": 0.55}) == \
        {"arrange": "stack", "swap": True, "sash": 0.55}
    assert m.normalize_layout({"arrange": "x", "swap": "yes", "sash": 5})["sash"] is None


def test_no_game_is_selected_on_first_run():
    profiles = m.load_profiles()
    assert m.initial_game({}, profiles) is None                         # первый запуск: «Выберите игру...»
    assert m.initial_game({"last_game": "no_such_game"}, profiles) is None
    assert m.initial_game({"last_game": 42}, profiles) is None
    first_alphabetical = next(iter(profiles))
    assert m.initial_game({}, profiles) != first_alphabetical           # не «первая в списке» (напр. American Truck Simulator)
    assert m.initial_game({"last_game": "rdr2"}, profiles) == "rdr2"   # сохранённый выбор пользователя уважается


def test_show_log_setting_defaults_and_validation():
    assert m.DEFAULT_SETTINGS["show_log"] is True
    assert m.get_settings({})["show_log"] is True
    assert m.get_settings({"settings": {"show_log": False}})["show_log"] is False
    assert m.get_settings({"settings": {"show_log": "no"}})["show_log"] is True   # неверный тип -> по умолчанию


def test_gui_wiring_is_checkable_without_tk():
    for name in ("build_bottom", "_make_panel", "_fill_mods", "_fill_log", "_build_top_card", "_build_game_card",
                 "_build_edit_bar", "apply_top_tabs", "_switch_tab", "set_arrange", "swap_ui", "reset_layout",
                 "draw_drop", "_on_drop_configure", "toggle_vanilla", "update_vanilla_ui", "_vanilla_worker",
                 "open_settings", "show_preview_dialog", "open_config_editor", "open_game_search", "export_pack",
                 "backup_saves_now", "open_restore_saves", "refresh_recents", "refresh_deps", "rebuild",
                 "set_layout_edit"):
        assert callable(getattr(m.App, name)), name
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert 'bind(seq, lambda e: "break")' in src          # колесо мыши не листает список игр
    assert "tk.PanedWindow(" in src and "minsize=" in src  # единый разделитель с минимальными размерами
    assert src.count("ttk.PanedWindow(") == 1              # только в редакторе конфигов (не в главном окне)
