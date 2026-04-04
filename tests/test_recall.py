"""Tests for companion_capture.recall."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from companion_capture.config import Config, SCHEMA_VERSION
from companion_capture.recall import (  # noqa: F401
    _check_cooldown,
    _format_results,
    _sanitize_text,
    main,
    recall,
)
from companion_capture.store import CaptureStore


# -- Helpers -------------------------------------------------------------------


def _make_config(tmp_path: Path, **overrides) -> Config:
    """Create a Config with recall enabled and paths in tmp_path."""
    defaults = {
        "companion_name": "TestCompanion",
        "output_dir": str(tmp_path / "output"),
        "log_dir": str(tmp_path / "logs"),
        "recall_enabled": True,
        "recall_max_results": 3,
        "recall_cooldown_seconds": 60,
    }
    defaults.update(overrides)
    return Config(**defaults)


def _seed_store(db_path: Path, project: str, count: int = 3) -> None:
    """Insert test captures into a SQLite store."""
    with CaptureStore(db_path) as store:
        for i in range(count):
            store.insert(
                id=f"test-id-{project}-{i}",
                timestamp=f"2026-04-0{min(i + 1, 9)}T12:00:00+00:00",
                project=project,
                session_id="test-session",
                raw_text=f"Capture message {i} for {project}",
                classification="vibe",
                schema_version=SCHEMA_VERSION,
            )


# -- recall() disabled / early exits ------------------------------------------


class TestRecallDisabled:
    def test_recall_disabled_by_default(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, recall_enabled=False)
        result = recall(config=cfg)
        assert result is None

    def test_recall_no_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir("/")
        cfg = _make_config(tmp_path)
        result = recall(config=cfg)
        assert result is None


# -- Cooldown ------------------------------------------------------------------


class TestCooldown:
    def test_recall_cooldown_not_elapsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(tmp_path, recall_cooldown_seconds=600)
        cfg.ensure_dirs()

        # Create a fresh marker
        log_dir = Path(cfg.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        marker = log_dir / f".recall-cooldown-{tmp_path.name}"
        marker.touch()

        # Seed data so it would return results if cooldown passed
        _seed_store(cfg.db_path, tmp_path.name)

        result = recall(config=cfg)
        assert result is None

    def test_recall_cooldown_elapsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(tmp_path, recall_cooldown_seconds=1)
        cfg.ensure_dirs()

        # Create a stale marker
        log_dir = Path(cfg.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        marker = log_dir / f".recall-cooldown-{tmp_path.name}"
        marker.touch()
        old_time = time.time() - 10
        os.utime(marker, (old_time, old_time))

        _seed_store(cfg.db_path, tmp_path.name)

        result = recall(config=cfg)
        assert result is not None
        assert tmp_path.name in result


# -- No DB / no results -------------------------------------------------------


class TestNoData:
    def test_recall_no_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(tmp_path)
        cfg.ensure_dirs()
        # Point db_path to a location that doesn't exist
        fake_db = tmp_path / "nonexistent" / "captures.db"
        monkeypatch.setattr(type(cfg), "db_path", property(lambda self: fake_db))
        assert not fake_db.exists()
        result = recall(config=cfg)
        assert result is None

    def test_recall_no_results(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(tmp_path)
        cfg.ensure_dirs()

        # Create empty store (no captures for this project)
        with CaptureStore(cfg.db_path):
            pass

        result = recall(config=cfg)
        assert result is None


# -- Successful recall ---------------------------------------------------------


class TestRecallResults:
    def test_recall_returns_formatted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(tmp_path)
        cfg.ensure_dirs()
        _seed_store(cfg.db_path, tmp_path.name, count=2)

        result = recall(config=cfg)
        assert result is not None
        assert "TestCompanion recall" in result
        assert "2 recent for" in result
        assert "Capture message" in result

    def test_recall_max_results_respected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(tmp_path, recall_max_results=2)
        cfg.ensure_dirs()
        _seed_store(cfg.db_path, tmp_path.name, count=5)

        result = recall(config=cfg)
        assert result is not None
        assert "2 recent for" in result
        # Count the detail lines (indented with two spaces)
        detail_lines = [line for line in result.split("\n") if line.startswith("  [")]
        assert len(detail_lines) == 2


# -- _format_results -----------------------------------------------------------


class TestFormatResults:
    def test_format_results(self) -> None:
        results = [
            {
                "timestamp": "2026-04-01T12:00:00+00:00",
                "classification": "vibe",
                "raw_text": "Hello world",
            },
            {
                "timestamp": "2026-04-02T15:30:00+00:00",
                "classification": "debug",
                "raw_text": "Debug message",
            },
        ]
        output = _format_results(results, "Keel", "my-project")
        assert "[Keel recall — 2 recent for my-project]" in output
        assert "  [2026-04-01 12:00] [vibe] Hello world" in output
        assert "  [2026-04-02 15:30] [debug] Debug message" in output


# -- _sanitize_text ------------------------------------------------------------


class TestSanitizeText:
    def test_strips_newlines(self) -> None:
        assert _sanitize_text("hello\nworld") == "hello world"

    def test_strips_control_chars(self) -> None:
        assert _sanitize_text("hello\x00\x1bworld") == "hello world"

    def test_leaves_normal_text(self) -> None:
        assert _sanitize_text("normal text here") == "normal text here"

    def test_strips_leading_trailing(self) -> None:
        assert _sanitize_text("\nhello\n") == "hello"


# -- _check_cooldown -----------------------------------------------------------


class TestCheckCooldown:
    def test_check_cooldown_fresh_marker(self, tmp_path: Path) -> None:
        marker = tmp_path / "marker"
        marker.touch()
        # Fresh marker, 600s cooldown — should return False
        assert _check_cooldown(marker, 600) is False

    def test_check_cooldown_stale_marker(self, tmp_path: Path) -> None:
        marker = tmp_path / "marker"
        marker.touch()
        old_time = time.time() - 120
        os.utime(marker, (old_time, old_time))
        # Marker is 120s old, cooldown is 60s — should return True
        assert _check_cooldown(marker, 60) is True

    def test_check_cooldown_no_marker(self, tmp_path: Path) -> None:
        marker = tmp_path / "nonexistent_marker"
        assert not marker.exists()
        # No marker — should return True and create it
        assert _check_cooldown(marker, 60) is True
        assert marker.exists()

    def test_check_cooldown_unwritable_dir(self, tmp_path: Path) -> None:
        # Parent dir doesn't exist and can't be created → fail closed
        marker = tmp_path / "no" / "such" / "deep" / "path" / "marker"
        # Make the parent unwritable so mkdir fails
        blocker = tmp_path / "no"
        blocker.mkdir()
        blocker.chmod(0o000)
        try:
            assert _check_cooldown(marker, 60) is False
        finally:
            blocker.chmod(0o755)


# -- main() entry point --------------------------------------------------------


class TestMain:
    def test_main_prints_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(tmp_path)
        cfg.ensure_dirs()
        _seed_store(cfg.db_path, tmp_path.name, count=1)

        # Patch Config.load to return our config
        monkeypatch.setattr(
            "companion_capture.recall.Config.load", lambda *a, **kw: cfg
        )
        main()
        captured = capsys.readouterr()
        assert "TestCompanion recall" in captured.out

    def test_main_silent_on_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        # Make Config.load raise to simulate an error
        def _raise(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr("companion_capture.recall.Config.load", _raise)
        # Should not raise
        main()
        captured = capsys.readouterr()
        assert captured.out == ""
