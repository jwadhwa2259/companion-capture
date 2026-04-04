"""Tests for companion-capture migrate subcommand."""

import json
from pathlib import Path
from unittest.mock import patch

from companion_capture.cli import (
    _migrate_check_new_wiring,
    _migrate_marker_files,
    _migrate_output_files,
    _migrate_remove_keel_alias,
    _migrate_remove_keel_hook,
    migrate,
)
from companion_capture.config import Config


# --- _migrate_output_files ---


class TestMigrateOutputFiles:
    def test_rename_all_files(self, tmp_path):
        """KEEL-* files get renamed, content preserved."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "KEEL-captures.md").write_text("capture content")
        (output_dir / "KEEL-debug.md").write_text("debug content")
        (output_dir / "KEEL-archive.md").write_text("archive content")

        config = Config(companion_name="Buddy", output_dir=str(output_dir))
        actions = _migrate_output_files(config)

        assert len(actions) == 3
        assert all("Renamed" in a for a in actions)
        assert (output_dir / "Buddy-captures.md").read_text() == "capture content"
        assert (output_dir / "Buddy-debug.md").read_text() == "debug content"
        assert (output_dir / "Buddy-archive.md").read_text() == "archive content"
        assert not (output_dir / "KEEL-captures.md").exists()

    def test_skip_when_target_exists(self, tmp_path):
        """Target already exists -- source left alone, skip message."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "KEEL-captures.md").write_text("old data")
        (output_dir / "Buddy-captures.md").write_text("new data")

        config = Config(companion_name="Buddy", output_dir=str(output_dir))
        actions = _migrate_output_files(config)

        assert len(actions) == 1
        assert "Skipped" in actions[0]
        assert "already exists" in actions[0]
        # Source preserved
        assert (output_dir / "KEEL-captures.md").read_text() == "old data"
        # Target untouched
        assert (output_dir / "Buddy-captures.md").read_text() == "new data"

    def test_noop_no_source(self, tmp_path):
        """Source doesn't exist -- no error, no actions."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        config = Config(companion_name="Buddy", output_dir=str(output_dir))
        actions = _migrate_output_files(config)

        assert actions == []


# --- _migrate_marker_files ---


class TestMigrateMarkerFiles:
    def test_move_and_rename_markers(self, tmp_path):
        """Markers moved and renamed from old dir to new dir."""
        old_dir = tmp_path / "keel-logs"
        old_dir.mkdir()
        new_dir = tmp_path / "new-logs"

        # Marker files with KEEL names
        (old_dir / ".last-read-KEEL-captures.md-myproject").write_text("42")
        (old_dir / ".last-mtime-KEEL-captures.md-myproject").write_text("1234")
        (old_dir / ".last-read-KEEL-debug.md-myproject").write_text("10")
        # Ephemeral file (no rename, just move)
        (old_dir / ".parser-abc123.pid").write_text("9999")

        config = Config(companion_name="Buddy", log_dir=str(new_dir))

        with patch("companion_capture.cli._OLD_KEEL_LOG_DIR", old_dir):
            actions = _migrate_marker_files(config)

        assert len(actions) == 4
        assert (new_dir / ".last-read-Buddy-captures.md-myproject").read_text() == "42"
        assert (
            new_dir / ".last-mtime-Buddy-captures.md-myproject"
        ).read_text() == "1234"
        assert (new_dir / ".last-read-Buddy-debug.md-myproject").read_text() == "10"
        assert (new_dir / ".parser-abc123.pid").read_text() == "9999"

    def test_noop_no_old_dir(self, tmp_path):
        """Old log dir doesn't exist -- no error."""
        new_dir = tmp_path / "new-logs"
        config = Config(companion_name="Buddy", log_dir=str(new_dir))

        with patch("companion_capture.cli._OLD_KEEL_LOG_DIR", tmp_path / "nonexistent"):
            actions = _migrate_marker_files(config)

        assert actions == []
        assert not new_dir.exists()

    def test_skip_existing_in_new_dir(self, tmp_path):
        """Files already in new dir are skipped."""
        old_dir = tmp_path / "keel-logs"
        old_dir.mkdir()
        new_dir = tmp_path / "new-logs"
        new_dir.mkdir()

        (old_dir / ".parser-abc.pid").write_text("old")
        (new_dir / ".parser-abc.pid").write_text("new")

        config = Config(companion_name="Buddy", log_dir=str(new_dir))

        with patch("companion_capture.cli._OLD_KEEL_LOG_DIR", old_dir):
            actions = _migrate_marker_files(config)

        assert len(actions) == 1
        assert "Skipped" in actions[0]
        assert (new_dir / ".parser-abc.pid").read_text() == "new"


# --- _migrate_remove_keel_alias ---


class TestMigrateRemoveKeelAlias:
    def _patch_rc(self, monkeypatch, rc_file):
        """Helper to patch _get_rc_file to return a test path."""
        monkeypatch.setattr("companion_capture.cli._get_rc_file", lambda: rc_file)

    def test_remove_alias_line(self, tmp_path, monkeypatch):
        """Keel alias line removed from rc file."""
        rc = tmp_path / ".zshrc"
        rc.write_text(
            "export PATH=/usr/bin\n"
            "alias claude='~/Keel\\ Implementation/claude-keel.sh'\n"
            "export EDITOR=vim\n"
        )
        self._patch_rc(monkeypatch, rc)
        actions = _migrate_remove_keel_alias()

        assert len(actions) == 1
        assert "Removed" in actions[0]
        content = rc.read_text()
        assert "claude-keel.sh" not in content
        assert "export PATH" in content
        assert "export EDITOR" in content

    def test_remove_alias_with_comment(self, tmp_path, monkeypatch):
        """Removes # Keel comment above alias."""
        rc = tmp_path / ".zshrc"
        rc.write_text(
            "export PATH=/usr/bin\n"
            "# Keel\n"
            "alias claude='~/Keel\\ Implementation/claude-keel.sh'\n"
            "export EDITOR=vim\n"
        )
        self._patch_rc(monkeypatch, rc)
        actions = _migrate_remove_keel_alias()

        assert len(actions) == 1
        content = rc.read_text()
        assert "# Keel" not in content
        assert "claude-keel.sh" not in content
        assert "export PATH" in content
        assert "export EDITOR" in content

    def test_noop_no_alias(self, tmp_path, monkeypatch):
        """No keel alias found -- no error, file unchanged."""
        rc = tmp_path / ".zshrc"
        original = "export PATH=/usr/bin\nalias claude='other-tool'\n"
        rc.write_text(original)
        self._patch_rc(monkeypatch, rc)
        actions = _migrate_remove_keel_alias()

        assert actions == []
        assert rc.read_text() == original

    def test_noop_no_rc_file(self, tmp_path, monkeypatch):
        """RC file doesn't exist -- no error."""
        self._patch_rc(monkeypatch, tmp_path / ".zshrc")
        actions = _migrate_remove_keel_alias()
        assert actions == []

    def test_comment_not_followed_by_alias(self, tmp_path, monkeypatch):
        """# Keel comment not followed by alias line -- preserve the comment."""
        rc = tmp_path / ".zshrc"
        rc.write_text(
            "# Keel\n"
            "export KEEL_VAR=1\n"
            "alias claude='~/Keel\\ Implementation/claude-keel.sh'\n"
        )
        self._patch_rc(monkeypatch, rc)
        actions = _migrate_remove_keel_alias()

        assert len(actions) == 1
        content = rc.read_text()
        # Comment was preserved because it wasn't directly above the alias
        assert "KEEL_VAR" in content
        assert "claude-keel.sh" not in content


# --- _migrate_remove_keel_hook ---


class TestMigrateRemoveKeelHook:
    def _make_settings(self, tmp_path, monkeypatch, data):
        """Write settings.json and patch expanduser to find it."""
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps(data))

        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.claude/settings.json":
                return settings
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)
        return settings

    def test_remove_keel_hook(self, tmp_path, monkeypatch):
        """keel-check.sh hook entry removed from settings.json."""
        data = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash|Edit",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "bash ~/Keel\\ Implementation/keel-check.sh",
                            }
                        ],
                    }
                ]
            }
        }
        settings = self._make_settings(tmp_path, monkeypatch, data)
        actions = _migrate_remove_keel_hook()

        assert len(actions) == 1
        assert "Removed" in actions[0]
        result = json.loads(settings.read_text())
        # hooks key should be cleaned up entirely
        assert "hooks" not in result

    def test_preserves_other_hooks(self, tmp_path, monkeypatch):
        """Only keel hook removed, others preserved."""
        data = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "bash ~/other-tool/check.sh",
                            }
                        ],
                    },
                    {
                        "matcher": "Bash|Edit",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "bash ~/Keel\\ Implementation/keel-check.sh",
                            }
                        ],
                    },
                ]
            }
        }
        settings = self._make_settings(tmp_path, monkeypatch, data)
        actions = _migrate_remove_keel_hook()

        assert len(actions) == 1
        result = json.loads(settings.read_text())
        assert len(result["hooks"]["PostToolUse"]) == 1
        assert "other-tool" in result["hooks"]["PostToolUse"][0]["hooks"][0]["command"]

    def test_noop_no_keel_hook(self, tmp_path, monkeypatch):
        """No keel hook -- no error, no actions."""
        data = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": "bash ~/other/check.sh"}
                        ],
                    }
                ]
            }
        }
        self._make_settings(tmp_path, monkeypatch, data)
        actions = _migrate_remove_keel_hook()
        assert actions == []

    def test_noop_no_settings_file(self, tmp_path, monkeypatch):
        """No settings.json -- no error."""
        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.claude/settings.json":
                return tmp_path / "nonexistent.json"
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)
        actions = _migrate_remove_keel_hook()
        assert actions == []


# --- _migrate_check_new_wiring ---


class TestMigrateCheckNewWiring:
    def test_advises_when_missing(self, tmp_path, monkeypatch):
        """When companion-capture wiring is missing, prints install advice."""
        rc = tmp_path / ".zshrc"
        rc.write_text("# nothing relevant\n")
        monkeypatch.setattr("companion_capture.cli._get_rc_file", lambda: rc)

        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.claude/settings.json":
                return tmp_path / "nonexistent.json"
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)
        advice = _migrate_check_new_wiring()

        assert len(advice) == 1
        assert "install.sh" in advice[0]

    def test_no_advice_when_present(self, tmp_path, monkeypatch):
        """No advice when wiring is already in place."""
        rc = tmp_path / ".zshrc"
        rc.write_text("alias claude='~/companion-capture/scripts/wrapper.sh'\n")
        monkeypatch.setattr("companion_capture.cli._get_rc_file", lambda: rc)

        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "bash ~/companion-capture/scripts/check.sh",
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )

        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.claude/settings.json":
                return settings
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)
        advice = _migrate_check_new_wiring()

        assert advice == []


# --- migrate() integration ---


class TestMigrateIntegration:
    def test_full_migration(self, tmp_path, monkeypatch, capsys):
        """Full migration: renames files, removes alias, removes hook."""
        # Set up output dir with KEEL files
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "KEEL-captures.md").write_text("captures")
        (output_dir / "KEEL-debug.md").write_text("debug")

        # Set up old log dir with markers
        old_log_dir = tmp_path / "keel-logs"
        old_log_dir.mkdir()
        (old_log_dir / ".last-read-KEEL-captures.md-proj").write_text("5")

        new_log_dir = tmp_path / "new-logs"

        # Set up rc file with keel alias
        rc = tmp_path / ".zshrc"
        rc.write_text("# Keel\nalias claude='~/Keel/claude-keel.sh'\n")
        monkeypatch.setattr("companion_capture.cli._get_rc_file", lambda: rc)

        # Set up settings.json with keel hook
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "bash ~/Keel/keel-check.sh",
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )

        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.claude/settings.json":
                return settings
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)

        config = Config(
            companion_name="Buddy",
            output_dir=str(output_dir),
            log_dir=str(new_log_dir),
        )
        monkeypatch.setattr("companion_capture.cli.Config.load", lambda: config)
        monkeypatch.setattr("companion_capture.cli._OLD_KEEL_LOG_DIR", old_log_dir)

        result = migrate()
        assert result == 0

        captured = capsys.readouterr().out
        assert "Actions taken" in captured
        assert "Renamed" in captured

        # Verify files were migrated
        assert (output_dir / "Buddy-captures.md").read_text() == "captures"
        assert (new_log_dir / ".last-read-Buddy-captures.md-proj").read_text() == "5"
        assert "claude-keel.sh" not in rc.read_text()

    def test_idempotency(self, tmp_path, monkeypatch, capsys):
        """Run migrate twice, second run is all skips or no-ops."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "KEEL-captures.md").write_text("data")

        new_log_dir = tmp_path / "new-logs"

        rc = tmp_path / ".zshrc"
        rc.write_text("# Keel\nalias claude='~/Keel/claude-keel.sh'\n")
        monkeypatch.setattr("companion_capture.cli._get_rc_file", lambda: rc)

        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "bash ~/Keel/keel-check.sh",
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )

        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.claude/settings.json":
                return settings
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)

        config = Config(
            companion_name="Buddy",
            output_dir=str(output_dir),
            log_dir=str(new_log_dir),
        )
        monkeypatch.setattr("companion_capture.cli.Config.load", lambda: config)
        monkeypatch.setattr(
            "companion_capture.cli._OLD_KEEL_LOG_DIR", tmp_path / "nonexistent"
        )

        # First run
        result1 = migrate()
        assert result1 == 0
        capsys.readouterr()  # clear output

        # Second run — everything already done
        result2 = migrate()
        assert result2 == 0

        captured = capsys.readouterr().out
        # No "Renamed" or "Removed" actions on second run
        assert "Renamed" not in captured
        assert "Removed" not in captured

    def test_nothing_to_migrate(self, tmp_path, monkeypatch, capsys):
        """No Keel artifacts at all -- clean message."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        new_log_dir = tmp_path / "new-logs"

        rc = tmp_path / ".zshrc"
        rc.write_text("# nothing\n")
        monkeypatch.setattr("companion_capture.cli._get_rc_file", lambda: rc)

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({}))

        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.claude/settings.json":
                return settings
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)

        config = Config(
            companion_name="Buddy",
            output_dir=str(output_dir),
            log_dir=str(new_log_dir),
        )
        monkeypatch.setattr("companion_capture.cli.Config.load", lambda: config)
        monkeypatch.setattr(
            "companion_capture.cli._OLD_KEEL_LOG_DIR", tmp_path / "nonexistent"
        )

        result = migrate()
        assert result == 0

        captured = capsys.readouterr().out
        assert "Nothing to migrate" in captured

    def test_install_advice_shown(self, tmp_path, monkeypatch, capsys):
        """When companion-capture wiring is missing, prints install advice."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        new_log_dir = tmp_path / "new-logs"

        rc = tmp_path / ".zshrc"
        rc.write_text("# nothing\n")
        monkeypatch.setattr("companion_capture.cli._get_rc_file", lambda: rc)

        # No settings file at all
        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.claude/settings.json":
                return tmp_path / "nonexistent.json"
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)

        config = Config(
            companion_name="Buddy",
            output_dir=str(output_dir),
            log_dir=str(new_log_dir),
        )
        monkeypatch.setattr("companion_capture.cli.Config.load", lambda: config)
        monkeypatch.setattr(
            "companion_capture.cli._OLD_KEEL_LOG_DIR", tmp_path / "nonexistent"
        )

        result = migrate()
        assert result == 0

        captured = capsys.readouterr().out
        assert "install.sh" in captured
