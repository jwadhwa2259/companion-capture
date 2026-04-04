"""Tests for companion-capture doctor subcommand."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch


from companion_capture.cli import (
    _check_config,
    _check_file_permissions,
    _check_hook,
    _check_parser_heartbeat,
    _check_parser_syntax,
    _check_python_version,
    _check_shell_alias,
    _format_result,
    doctor,
)
from companion_capture.config import Config


# --- _check_config ---


class TestCheckConfig:
    def test_valid_config(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text('{"companion_name": "Test"}')
        with patch("companion_capture.cli.Config.load", return_value=Config()):
            status, msg, fix = _check_config()
        assert status == "pass"
        assert "loaded successfully" in msg

    def test_invalid_config(self, tmp_path):
        with patch(
            "companion_capture.cli.Config.load",
            side_effect=ValueError("bad JSON"),
        ):
            status, msg, fix = _check_config()
        assert status == "fail"
        assert "bad JSON" in msg
        assert fix is not None


# --- _check_python_version ---


class TestCheckPythonVersion:
    def test_current_python_passes(self):
        status, msg, fix = _check_python_version()
        assert status == "pass"
        assert "detected" in msg

    def test_old_python_fails(self, monkeypatch):
        from collections import namedtuple

        FakeVersion = namedtuple("version_info", "major minor micro")
        monkeypatch.setattr(
            "companion_capture.cli.sys.version_info", FakeVersion(3, 7, 0)
        )
        status, msg, fix = _check_python_version()
        assert status == "fail"
        assert "3.9+" in msg


# --- _check_shell_alias ---


class TestCheckShellAlias:
    def test_alias_present_zsh(self, tmp_path, monkeypatch):
        rc = tmp_path / ".zshrc"
        rc.write_text(
            "# companion-capture\nalias claude='~/companion-capture/scripts/wrapper.sh'\n"
        )
        monkeypatch.setenv("SHELL", "/bin/zsh")
        # Patch Path("~/.zshrc").expanduser() to return our tmp file
        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.zshrc":
                return rc
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)
        status, msg, fix = _check_shell_alias()
        assert status == "pass"
        assert ".zshrc" in msg

    def test_alias_missing(self, tmp_path, monkeypatch):
        rc = tmp_path / ".bashrc"
        rc.write_text("# nothing here\n")
        monkeypatch.setenv("SHELL", "/bin/bash")
        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.bashrc":
                return rc
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)
        status, msg, fix = _check_shell_alias()
        assert status == "fail"
        assert "not found" in msg

    def test_rc_file_missing(self, tmp_path, monkeypatch):
        rc = tmp_path / ".zshrc"  # don't create it
        monkeypatch.setenv("SHELL", "/bin/zsh")
        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.zshrc":
                return rc
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)
        status, msg, fix = _check_shell_alias()
        assert status == "fail"


# --- _check_hook ---


class TestCheckHook:
    def test_hook_present(self, tmp_path, monkeypatch):
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Bash|Edit|Write|MultiEdit|Read",
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
        status, msg, fix = _check_hook()
        assert status == "pass"

    def test_hook_missing(self, tmp_path, monkeypatch):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"hooks": {"PostToolUse": []}}))
        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.claude/settings.json":
                return settings
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)
        status, msg, fix = _check_hook()
        assert status == "fail"
        assert "not configured" in msg

    def test_no_settings_file(self, tmp_path, monkeypatch):
        settings = tmp_path / "settings.json"  # don't create
        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.claude/settings.json":
                return settings
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)
        status, msg, fix = _check_hook()
        assert status == "fail"

    def test_invalid_json(self, tmp_path, monkeypatch):
        settings = tmp_path / "settings.json"
        settings.write_text("not json{{{")
        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            if str(self) == "~/.claude/settings.json":
                return settings
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)
        status, msg, fix = _check_hook()
        assert status == "fail"

    def test_hook_wrong_command(self, tmp_path, monkeypatch):
        """Hook exists but for different tool — should fail."""
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
                                        "command": "bash ~/other-tool/check.sh",
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
        status, msg, fix = _check_hook()
        assert status == "fail"


# --- _check_parser_syntax ---


class TestCheckParserSyntax:
    def test_valid_parser(self):
        status, msg, fix = _check_parser_syntax()
        assert status == "pass"
        assert "syntax OK" in msg

    def test_missing_parser(self, tmp_path):
        import companion_capture.cli as cli_mod

        orig = cli_mod.__file__
        try:
            cli_mod.__file__ = str(tmp_path / "cli.py")
            status, msg, fix = _check_parser_syntax()
        finally:
            cli_mod.__file__ = orig
        assert status == "fail"
        assert "not found" in msg

    def test_syntax_error_parser(self, tmp_path):
        bad_parser = tmp_path / "parser.py"
        bad_parser.write_text("def broken(\n")
        with patch("companion_capture.cli.__file__", str(tmp_path / "cli.py")):
            status, msg, fix = _check_parser_syntax()
        assert status == "fail"
        assert "syntax error" in msg


# --- _check_file_permissions ---


class TestCheckFilePermissions:
    def test_writable_dirs(self, tmp_path):
        output_dir = tmp_path / "output"
        log_dir = tmp_path / "logs"
        output_dir.mkdir()
        log_dir.mkdir()
        config = Config(output_dir=str(output_dir), log_dir=str(log_dir))
        results = _check_file_permissions(config)
        assert len(results) == 2
        assert all(r[0] == "pass" for r in results)

    def test_missing_dirs(self, tmp_path):
        config = Config(
            output_dir=str(tmp_path / "nope"),
            log_dir=str(tmp_path / "also_nope"),
        )
        results = _check_file_permissions(config)
        assert len(results) == 2
        assert all(r[0] == "fail" for r in results)
        assert all("mkdir" in r[2] for r in results)


# --- _check_parser_heartbeat ---


class TestCheckParserHeartbeat:
    def test_no_log_dir(self, tmp_path):
        config = Config(log_dir=str(tmp_path / "nonexistent"))
        status, msg, fix = _check_parser_heartbeat(config)
        assert status == "skip"

    def test_no_heartbeat_files(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        config = Config(log_dir=str(log_dir))
        status, msg, fix = _check_parser_heartbeat(config)
        assert status == "skip"

    def test_fresh_heartbeat(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        hb = log_dir / ".parser-heartbeat-123"
        hb.touch()
        config = Config(log_dir=str(log_dir))
        status, msg, fix = _check_parser_heartbeat(config)
        assert status == "pass"
        assert "heartbeat OK" in msg

    def test_stale_heartbeat(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        hb = log_dir / ".parser-heartbeat-123"
        hb.touch()
        # Set mtime to 700 seconds ago
        old_time = time.time() - 700
        os.utime(hb, (old_time, old_time))
        config = Config(log_dir=str(log_dir))
        status, msg, fix = _check_parser_heartbeat(config)
        assert status == "fail"
        assert "stale" in msg


# --- _format_result ---


class TestFormatResult:
    def test_pass_format(self):
        out = _format_result(("pass", "All good", None))
        assert "\u2713" in out
        assert "All good" in out

    def test_fail_format_with_fix(self):
        out = _format_result(("fail", "Broken", "Run fix.sh"))
        assert "\u2717" in out
        assert "Broken" in out
        assert "Fix: Run fix.sh" in out

    def test_fail_format_no_fix(self):
        out = _format_result(("fail", "Broken", None))
        assert "\u2717" in out
        assert "Fix" not in out

    def test_skip_format(self):
        out = _format_result(("skip", "Not applicable", None))
        assert out.startswith("- ")


# --- doctor() integration ---


class TestDoctorIntegration:
    def test_all_pass_returns_zero(self, monkeypatch):
        monkeypatch.setattr(
            "companion_capture.cli._check_config",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_python_version",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_shell_alias",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_hook",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_parser_syntax",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_claude_md",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_file_permissions",
            lambda config: [("pass", "ok", None), ("pass", "ok", None)],
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_parser_heartbeat",
            lambda config: ("skip", "no session", None),
        )
        assert doctor() == 0

    def test_any_fail_returns_one(self, monkeypatch):
        monkeypatch.setattr(
            "companion_capture.cli._check_config",
            lambda: ("fail", "bad", "fix it"),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_python_version",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_shell_alias",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_hook",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_parser_syntax",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_claude_md",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_file_permissions",
            lambda config: [("pass", "ok", None), ("pass", "ok", None)],
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_parser_heartbeat",
            lambda config: ("skip", "no session", None),
        )
        assert doctor() == 1

    def test_skips_are_ok(self, monkeypatch):
        """Skips should not cause exit code 1."""
        monkeypatch.setattr(
            "companion_capture.cli._check_config",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_python_version",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_shell_alias",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_hook",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_parser_syntax",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_claude_md",
            lambda: ("pass", "ok", None),
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_file_permissions",
            lambda config: [("pass", "ok", None), ("pass", "ok", None)],
        )
        monkeypatch.setattr(
            "companion_capture.cli._check_parser_heartbeat",
            lambda config: ("skip", "no session", None),
        )
        assert doctor() == 0
