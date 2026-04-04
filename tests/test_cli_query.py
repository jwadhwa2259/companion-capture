"""Tests for companion_capture CLI query subcommands — search, recent, stats."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from companion_capture.cli import (
    _format_capture,
    cmd_recent,
    cmd_search,
    cmd_stats,
    parse_since,
)
from companion_capture.config import Config, SCHEMA_VERSION
from companion_capture.store import CaptureStore


# -- Helpers -------------------------------------------------------------------


def _test_config(tmp_path: Path) -> Config:
    return Config(
        companion_name="TestCompanion",
        output_dir=str(tmp_path),
        log_dir=str(tmp_path / "logs"),
    )


def _insert_sample(store: CaptureStore, **overrides) -> None:
    defaults = {
        "id": "aaa-bbb-ccc",
        "timestamp": "2026-04-04T12:00:00+00:00",
        "project": "test-project",
        "session_id": "sess-001",
        "raw_text": "Hello world from the goose",
        "classification": "vibe",
        "schema_version": SCHEMA_VERSION,
    }
    defaults.update(overrides)
    store.insert(**defaults)


# =============================================================================
# parse_since
# =============================================================================


class TestParseSince:
    def test_days(self) -> None:
        result = parse_since("7d")
        assert "T" in result  # ISO format
        assert result.endswith("+00:00") or result.endswith("Z")

    def test_hours(self) -> None:
        result = parse_since("24h")
        assert "T" in result

    def test_weeks(self) -> None:
        result = parse_since("2w")
        assert "T" in result

    def test_minutes(self) -> None:
        result = parse_since("30m")
        assert "T" in result

    def test_invalid_suffix(self) -> None:
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_since("7x")

    def test_invalid_format(self) -> None:
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_since("abc")

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError, match="empty string"):
            parse_since("")

    def test_zero(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            parse_since("0d")

    def test_negative_rejected(self) -> None:
        # Regex won't match "-5d" since it requires digits only
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_since("-5d")


# =============================================================================
# _format_capture
# =============================================================================


class TestFormatCapture:
    def test_basic_format(self) -> None:
        row = {
            "timestamp": "2026-04-04T12:30:00+00:00",
            "classification": "vibe",
            "project": "my-project",
            "raw_text": "Hello from goose",
        }
        result = _format_capture(row)
        assert "[2026-04-04 12:30]" in result
        assert "[vibe]" in result
        assert "(my-project)" in result
        assert "Hello from goose" in result

    def test_missing_project(self) -> None:
        row = {
            "timestamp": "2026-04-04T12:30:00+00:00",
            "classification": "debug",
            "project": None,
            "raw_text": "No project here",
        }
        result = _format_capture(row)
        assert "(" not in result
        assert "No project here" in result

    def test_timestamp_truncation(self) -> None:
        row = {
            "timestamp": "2026-04-04T12:30:45.123456+00:00",
            "classification": "vibe",
            "project": "p",
            "raw_text": "msg",
        }
        result = _format_capture(row)
        assert "2026-04-04 12:30" in result
        assert "45.123456" not in result


# =============================================================================
# cmd_search (integration with real SQLite)
# =============================================================================


class TestCmdSearch:
    def test_search_basic(self, tmp_path: Path, capsys, monkeypatch) -> None:
        db = tmp_path / "test.db"
        monkeypatch.setattr(
            "companion_capture.cli.Config.load", lambda: _test_config(tmp_path)
        )
        monkeypatch.setattr(
            "companion_capture.cli.Config.db_path",
            property(lambda self: db),
        )
        # Pre-populate DB
        with CaptureStore(db) as store:
            _insert_sample(store, id="1", raw_text="alpha beta gamma")
            _insert_sample(store, id="2", raw_text="delta epsilon")

        # Patch Config so cmd_search finds our DB
        monkeypatch.setattr(
            "companion_capture.cli.Config",
            type(
                "FakeConfig",
                (),
                {
                    "load": staticmethod(lambda: _test_config(tmp_path)),
                    "db_path": db,
                    "__call__": lambda self: _test_config(tmp_path),
                },
            ),
        )
        # Simpler: just patch Config.load and the db_path
        # Actually let's just use a direct approach
        monkeypatch.undo()

        # Direct approach: patch Config.load to return config with our db_path
        class PatchedConfig(Config):
            @property
            def db_path(self):
                return db

        monkeypatch.setattr(
            "companion_capture.cli.Config.load",
            classmethod(
                lambda cls, **kw: PatchedConfig(
                    companion_name="TestCompanion",
                    output_dir=str(tmp_path),
                    log_dir=str(tmp_path / "logs"),
                )
            ),
        )
        monkeypatch.setattr("companion_capture.cli.Config", PatchedConfig)

        args = argparse.Namespace(
            query="alpha",
            project=None,
            since=None,
            classification=None,
            limit=20,
        )
        rc = cmd_search(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "alpha beta gamma" in out

    def test_search_no_results(self, tmp_path: Path, capsys, monkeypatch) -> None:
        db = tmp_path / "test.db"
        # Create empty DB
        with CaptureStore(db) as _:
            pass

        class PatchedConfig(Config):
            @property
            def db_path(self):
                return db

        monkeypatch.setattr("companion_capture.cli.Config", PatchedConfig)

        args = argparse.Namespace(
            query="nonexistent",
            project=None,
            since=None,
            classification=None,
            limit=20,
        )
        rc = cmd_search(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "No captures found." in out

    def test_search_invalid_since(self, tmp_path: Path, capsys) -> None:
        args = argparse.Namespace(
            query="anything",
            project=None,
            since="bad",
            classification=None,
            limit=20,
        )
        rc = cmd_search(args)
        err = capsys.readouterr().err
        assert rc == 1
        assert "Invalid duration" in err


# =============================================================================
# cmd_recent
# =============================================================================


class TestCmdRecent:
    def test_recent_basic(self, tmp_path: Path, capsys, monkeypatch) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(
                store,
                id="1",
                timestamp="2026-04-04T10:00:00+00:00",
                raw_text="first message",
            )
            _insert_sample(
                store,
                id="2",
                timestamp="2026-04-04T11:00:00+00:00",
                raw_text="second message",
            )

        class PatchedConfig(Config):
            @property
            def db_path(self):
                return db

        monkeypatch.setattr("companion_capture.cli.Config", PatchedConfig)

        args = argparse.Namespace(project=None, limit=20)
        rc = cmd_recent(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "second message" in out
        assert "first message" in out
        # Newest first
        assert out.index("second message") < out.index("first message")

    def test_recent_with_project(self, tmp_path: Path, capsys, monkeypatch) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="1", project="proj-a", raw_text="alpha msg")
            _insert_sample(store, id="2", project="proj-b", raw_text="beta msg")

        class PatchedConfig(Config):
            @property
            def db_path(self):
                return db

        monkeypatch.setattr("companion_capture.cli.Config", PatchedConfig)

        args = argparse.Namespace(project="proj-a", limit=20)
        rc = cmd_recent(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "alpha msg" in out
        assert "beta msg" not in out

    def test_recent_with_limit(self, tmp_path: Path, capsys, monkeypatch) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            for i in range(5):
                _insert_sample(
                    store,
                    id=f"id-{i}",
                    timestamp=f"2026-04-04T1{i}:00:00+00:00",
                    raw_text=f"msg-{i}",
                )

        class PatchedConfig(Config):
            @property
            def db_path(self):
                return db

        monkeypatch.setattr("companion_capture.cli.Config", PatchedConfig)

        args = argparse.Namespace(project=None, limit=2)
        rc = cmd_recent(args)
        out = capsys.readouterr().out
        assert rc == 0
        lines = [line for line in out.strip().split("\n") if line.strip()]
        assert len(lines) == 2


# =============================================================================
# cmd_stats
# =============================================================================


class TestCmdStats:
    def test_stats_basic(self, tmp_path: Path, capsys, monkeypatch) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(
                store,
                id="1",
                project="proj-a",
                classification="vibe",
                timestamp="2026-01-15T08:00:00+00:00",
            )
            _insert_sample(
                store,
                id="2",
                project="proj-a",
                classification="debug",
                timestamp="2026-02-20T10:00:00+00:00",
            )
            _insert_sample(
                store,
                id="3",
                project="proj-b",
                classification="vibe",
                timestamp="2026-04-04T12:00:00+00:00",
            )

        class PatchedConfig(Config):
            @property
            def db_path(self):
                return db

        monkeypatch.setattr("companion_capture.cli.Config", PatchedConfig)

        args = argparse.Namespace()
        rc = cmd_stats(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "Captures: 3" in out
        assert "2026-01-15" in out
        assert "2026-04-04" in out
        assert "proj-a" in out
        assert "proj-b" in out
        assert "vibe" in out
        assert "debug" in out

    def test_stats_empty(self, tmp_path: Path, capsys, monkeypatch) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as _:
            pass

        class PatchedConfig(Config):
            @property
            def db_path(self):
                return db

        monkeypatch.setattr("companion_capture.cli.Config", PatchedConfig)

        args = argparse.Namespace()
        rc = cmd_stats(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "No captures in database." in out
