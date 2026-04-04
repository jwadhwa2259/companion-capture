"""Tests for companion_capture.config."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from companion_capture.config import Config, SCHEMA_VERSION


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """Return a temp config directory with a config.json path."""
    return tmp_path / "config"


@pytest.fixture()
def config_file(config_dir: Path) -> Path:
    return config_dir / "config.json"


@pytest.fixture()
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all COMPANION_ env vars so tests start clean."""
    for key in list(
        dict(monkeypatch._setattr if hasattr(monkeypatch, "_setattr") else {}).keys()
    ):
        pass
    # Just delete any that might be set
    import os

    for key in list(os.environ):
        if key.startswith("COMPANION_"):
            monkeypatch.delenv(key, raising=False)


# -- Default values ----------------------------------------------------------


@pytest.mark.usefixtures("_clean_env")
class TestDefaults:
    def test_defaults_when_no_config_file(self, tmp_path: Path) -> None:
        cfg = Config.load(config_path=tmp_path / "nonexistent.json")
        assert cfg.companion_name == "Companion"
        assert cfg.rotation_days == 7
        assert cfg.archive_days == 90
        assert cfg.debug is False

    def test_default_paths_are_expanded(self, tmp_path: Path) -> None:
        cfg = Config.load(config_path=tmp_path / "nonexistent.json")
        assert "~" not in cfg.output_dir
        assert "~" not in cfg.log_dir

    def test_default_output_dir(self, tmp_path: Path) -> None:
        cfg = Config.load(config_path=tmp_path / "nonexistent.json")
        expected = str(Path("~/.claude/").expanduser())
        assert cfg.output_dir == expected

    def test_default_log_dir(self, tmp_path: Path) -> None:
        cfg = Config.load(config_path=tmp_path / "nonexistent.json")
        expected = str(Path("~/.companion-capture/logs/").expanduser())
        assert cfg.log_dir == expected


# -- Name validation --------------------------------------------------------


class TestNameValidation:
    def test_valid_names(self) -> None:
        for name in ["Keel", "My Goose", "companion-1", "test_name"]:
            cfg = Config(companion_name=name)
            assert cfg.companion_name == name

    def test_rejects_shell_injection(self) -> None:
        with pytest.raises(ValueError, match="companion_name must be"):
            Config(companion_name='"; rm -rf /; echo "')

    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="companion_name must be"):
            Config(companion_name="../../etc/evil")

    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError, match="companion_name must be"):
            Config(companion_name="")

    def test_rejects_special_chars(self) -> None:
        for name in ["foo;bar", "a$(cmd)", "name`id`", "a\nb"]:
            with pytest.raises(ValueError, match="companion_name must be"):
                Config(companion_name=name)

    def test_strips_leading_trailing_whitespace(self) -> None:
        cfg = Config(companion_name="  Keel  ")
        assert cfg.companion_name == "Keel"

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="companion_name must be"):
            Config(companion_name="   ")

    def test_invalid_name_via_config_file(self, config_dir: Path) -> None:
        config_file = config_dir / "config.json"
        config_dir.mkdir(parents=True)
        config_file.write_text(json.dumps({"companion_name": '"; rm -rf /'}))
        with pytest.raises(ValueError, match="companion_name must be"):
            Config.load(config_path=config_file)

    def test_invalid_name_via_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COMPANION_NAME", "../../etc/evil")
        with pytest.raises(ValueError, match="companion_name must be"):
            Config.load(config_path=tmp_path / "nonexistent.json")


# -- Loading from config file -----------------------------------------------


@pytest.mark.usefixtures("_clean_env")
class TestConfigFile:
    def test_load_from_json(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            json.dumps(
                {
                    "companion_name": "Goose",
                    "rotation_days": 14,
                    "debug": True,
                }
            )
        )
        cfg = Config.load(config_path=config_file)
        assert cfg.companion_name == "Goose"
        assert cfg.rotation_days == 14
        assert cfg.debug is True
        # Unset fields keep defaults
        assert cfg.archive_days == 90

    def test_load_custom_paths(self, config_file: Path, tmp_path: Path) -> None:
        out = str(tmp_path / "out")
        log = str(tmp_path / "log")
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            json.dumps(
                {
                    "output_dir": out,
                    "log_dir": log,
                }
            )
        )
        cfg = Config.load(config_path=config_file)
        assert cfg.output_dir == out
        assert cfg.log_dir == log

    def test_unknown_keys_ignored(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            json.dumps(
                {
                    "companion_name": "Duck",
                    "unknown_key": "should be ignored",
                }
            )
        )
        cfg = Config.load(config_path=config_file)
        assert cfg.companion_name == "Duck"
        assert not hasattr(cfg, "unknown_key")

    def test_missing_config_file_no_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist" / "config.json"
        cfg = Config.load(config_path=missing)
        assert cfg.companion_name == "Companion"

    def test_empty_config_file_uses_defaults(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text("")
        cfg = Config.load(config_path=config_file)
        assert cfg.companion_name == "Companion"
        assert cfg.rotation_days == 7

    def test_whitespace_only_config_file_uses_defaults(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text("  \n  ")
        cfg = Config.load(config_path=config_file)
        assert cfg.companion_name == "Companion"

    def test_unreadable_config_file_uses_defaults(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text('{"companion_name": "Goose"}')
        config_file.chmod(0o000)
        try:
            cfg = Config.load(config_path=config_file)
            assert cfg.companion_name == "Companion"
        finally:
            config_file.chmod(0o644)

    def test_invalid_json_raises(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text("not valid json {{{")
        with pytest.raises(ValueError, match="Invalid JSON"):
            Config.load(config_path=config_file)

    def test_non_object_json_raises(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps([1, 2, 3]))
        with pytest.raises(ValueError, match="must contain a JSON object"):
            Config.load(config_path=config_file)


# -- Env var overrides -------------------------------------------------------


@pytest.mark.usefixtures("_clean_env")
class TestEnvVars:
    def test_env_overrides_config_file(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({"companion_name": "Goose"}))
        monkeypatch.setenv("COMPANION_NAME", "Pelican")
        cfg = Config.load(config_path=config_file)
        assert cfg.companion_name == "Pelican"

    def test_env_overrides_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COMPANION_ROTATION_DAYS", "30")
        monkeypatch.setenv("COMPANION_ARCHIVE_DAYS", "180")
        cfg = Config.load(config_path=tmp_path / "none.json")
        assert cfg.rotation_days == 30
        assert cfg.archive_days == 180

    def test_env_output_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = str(tmp_path / "custom_out")
        monkeypatch.setenv("COMPANION_OUTPUT_DIR", target)
        cfg = Config.load(config_path=tmp_path / "none.json")
        assert cfg.output_dir == target

    def test_env_log_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = str(tmp_path / "custom_log")
        monkeypatch.setenv("COMPANION_LOG_DIR", target)
        cfg = Config.load(config_path=tmp_path / "none.json")
        assert cfg.log_dir == target


# -- Debug boolean parsing ---------------------------------------------------


@pytest.mark.usefixtures("_clean_env")
class TestDebugParsing:
    @pytest.mark.parametrize("val", ["1", "true", "True", "TRUE", "yes", "YES", "Yes"])
    def test_truthy_values(
        self, val: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COMPANION_DEBUG", val)
        cfg = Config.load(config_path=tmp_path / "none.json")
        assert cfg.debug is True

    @pytest.mark.parametrize("val", ["0", "false", "False", "no", "NO", "", "nope"])
    def test_falsy_values(
        self, val: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COMPANION_DEBUG", val)
        cfg = Config.load(config_path=tmp_path / "none.json")
        assert cfg.debug is False

    def test_debug_env_overrides_file(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({"debug": True}))
        monkeypatch.setenv("COMPANION_DEBUG", "0")
        cfg = Config.load(config_path=config_file)
        assert cfg.debug is False


# -- Derived path properties -------------------------------------------------


@pytest.mark.usefixtures("_clean_env")
class TestDerivedPaths:
    def test_captures_file(self, tmp_path: Path) -> None:
        cfg = Config(companion_name="Keel", output_dir=str(tmp_path))
        assert cfg.captures_file == tmp_path / "Keel-captures.md"

    def test_debug_file(self, tmp_path: Path) -> None:
        cfg = Config(companion_name="Keel", output_dir=str(tmp_path))
        assert cfg.debug_file == tmp_path / "Keel-debug.md"

    def test_archive_file(self, tmp_path: Path) -> None:
        cfg = Config(companion_name="Keel", output_dir=str(tmp_path))
        assert cfg.archive_file == tmp_path / "Keel-archive.md"

    def test_derived_paths_use_companion_name(self, tmp_path: Path) -> None:
        cfg = Config(companion_name="Goose", output_dir=str(tmp_path))
        assert "Goose-captures.md" in str(cfg.captures_file)
        assert "Goose-debug.md" in str(cfg.debug_file)
        assert "Goose-archive.md" in str(cfg.archive_file)

    def test_config_dir_is_fixed(self, tmp_path: Path) -> None:
        cfg = Config.load(config_path=tmp_path / "none.json")
        assert cfg.config_dir == Path("~/.companion-capture").expanduser()


# -- Path expansion ----------------------------------------------------------


@pytest.mark.usefixtures("_clean_env")
class TestPathExpansion:
    def test_tilde_expanded_in_output_dir(self) -> None:
        cfg = Config(output_dir="~/somewhere")
        assert "~" not in cfg.output_dir
        assert cfg.output_dir == str(Path("~/somewhere").expanduser())

    def test_tilde_expanded_in_log_dir(self) -> None:
        cfg = Config(log_dir="~/logs")
        assert "~" not in cfg.log_dir
        assert cfg.log_dir == str(Path("~/logs").expanduser())

    def test_tilde_in_config_file_expanded(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({"output_dir": "~/custom-output"}))
        cfg = Config.load(config_path=config_file)
        assert "~" not in cfg.output_dir


# -- generate_entry_id -------------------------------------------------------


class TestEntryId:
    _ENTRY_RE = re.compile(
        r"^<!-- "
        r"schema:\d+ "
        r"id:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12} "
        r"ts:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
        r".*"
        r" -->$"
    )

    def test_format_matches(self) -> None:
        cfg = Config()
        entry_id = cfg.generate_entry_id()
        assert self._ENTRY_RE.match(entry_id), f"Entry ID didn't match: {entry_id}"

    def test_contains_schema_version(self) -> None:
        cfg = Config()
        entry_id = cfg.generate_entry_id()
        assert f"schema:{SCHEMA_VERSION}" in entry_id

    def test_unique_ids(self) -> None:
        cfg = Config()
        ids = {cfg.generate_entry_id() for _ in range(20)}
        assert len(ids) == 20

    def test_utc_timestamp(self) -> None:
        cfg = Config()
        entry_id = cfg.generate_entry_id()
        # ISO UTC timestamps end with +00:00
        assert "+00:00" in entry_id


# -- ensure_dirs -------------------------------------------------------------


@pytest.mark.usefixtures("_clean_env")
class TestEnsureDirs:
    def test_creates_output_dir(self, tmp_path: Path) -> None:
        out = tmp_path / "a" / "b" / "output"
        cfg = Config(output_dir=str(out), log_dir=str(tmp_path / "logs"))
        assert not out.exists()
        cfg.ensure_dirs()
        assert out.is_dir()

    def test_creates_log_dir(self, tmp_path: Path) -> None:
        log = tmp_path / "x" / "y" / "logs"
        cfg = Config(output_dir=str(tmp_path / "out"), log_dir=str(log))
        assert not log.exists()
        cfg.ensure_dirs()
        assert log.is_dir()

    def test_idempotent(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        log = tmp_path / "log"
        cfg = Config(output_dir=str(out), log_dir=str(log))
        cfg.ensure_dirs()
        cfg.ensure_dirs()  # no error on second call
        assert out.is_dir()
        assert log.is_dir()
