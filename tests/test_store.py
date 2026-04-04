"""Tests for companion_capture.store — CaptureStore and dual-write integration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from companion_capture.config import Config, SCHEMA_VERSION
from companion_capture.parser import append_capture
from companion_capture.store import CaptureStore


# -- Helpers -------------------------------------------------------------------


def _test_config(tmp_path: Path, name: str = "TestCompanion") -> Config:
    """Create a Config pointing to tmp_path for testing."""
    return Config(
        companion_name=name,
        output_dir=str(tmp_path),
        log_dir=str(tmp_path / "logs"),
    )


def _insert_sample(store: CaptureStore, **overrides) -> dict:
    """Insert a sample capture and return the kwargs used."""
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
    return defaults


# =============================================================================
# CaptureStore unit tests
# =============================================================================


class TestSchemaCreation:
    def test_creates_captures_table(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = CaptureStore(db)
        store.open()
        try:
            rows = store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='captures'"
            ).fetchall()
            assert len(rows) == 1
        finally:
            store.close()

    def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = CaptureStore(db)
        store.open()
        try:
            mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"
        finally:
            store.close()

    def test_table_has_correct_columns(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            info = store._conn.execute("PRAGMA table_info(captures)").fetchall()
            col_names = {row[1] for row in info}
            expected = {
                "id",
                "timestamp",
                "project",
                "session_id",
                "raw_text",
                "classification",
                "files_in_context",
                "schema_version",
            }
            assert col_names == expected


class TestFTS5Detection:
    def test_has_fts5_on_macos(self, tmp_path: Path) -> None:
        """macOS ships SQLite with FTS5 compiled in."""
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            assert store.has_fts5 is True

    def test_fts_table_created_when_fts5_available(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            if store.has_fts5:
                rows = store._conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='captures_fts'"
                ).fetchall()
                assert len(rows) == 1


class TestInsertAndQuery:
    def test_round_trip(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            data = _insert_sample(store)
            results = store.search("Hello")
            assert len(results) == 1
            row = results[0]
            assert row["id"] == data["id"]
            assert row["timestamp"] == data["timestamp"]
            assert row["project"] == data["project"]
            assert row["session_id"] == data["session_id"]
            assert row["raw_text"] == data["raw_text"]
            assert row["classification"] == data["classification"]
            assert row["schema_version"] == data["schema_version"]

    def test_fts5_search(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            if not store.has_fts5:
                pytest.skip("FTS5 not available")
            _insert_sample(store, id="1", raw_text="The quick brown fox")
            _insert_sample(store, id="2", raw_text="Lazy dog sleeping")
            _insert_sample(store, id="3", raw_text="Fox jumped over")

            results = store.search("fox")
            ids = {r["id"] for r in results}
            assert "1" in ids
            assert "3" in ids
            assert "2" not in ids

    def test_like_fallback(self, tmp_path: Path) -> None:
        """When FTS5 is mocked as unavailable, LIKE fallback still works."""
        db = tmp_path / "test.db"
        store = CaptureStore(db)
        store.open()
        try:
            # Force FTS5 off
            store._has_fts5 = False
            _insert_sample(store, id="1", raw_text="alpha beta gamma")
            _insert_sample(store, id="2", raw_text="delta epsilon")

            results = store.search("beta")
            assert len(results) == 1
            assert results[0]["id"] == "1"
        finally:
            store.close()


class TestFilters:
    def test_project_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="1", project="proj-a", raw_text="shared keyword")
            _insert_sample(store, id="2", project="proj-b", raw_text="shared keyword")

            results = store.search("shared", project="proj-a")
            assert len(results) == 1
            assert results[0]["project"] == "proj-a"

    def test_classification_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(
                store, id="1", classification="vibe", raw_text="something nice"
            )
            _insert_sample(
                store, id="2", classification="debug", raw_text="something nice"
            )

            results = store.search("something", classification="debug")
            assert len(results) == 1
            assert results[0]["classification"] == "debug"

    def test_combined_filters(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(
                store,
                id="1",
                project="proj-a",
                classification="vibe",
                raw_text="target message",
            )
            _insert_sample(
                store,
                id="2",
                project="proj-a",
                classification="debug",
                raw_text="target message",
            )
            _insert_sample(
                store,
                id="3",
                project="proj-b",
                classification="vibe",
                raw_text="target message",
            )

            results = store.search("target", project="proj-a", classification="vibe")
            assert len(results) == 1
            assert results[0]["id"] == "1"


class TestIdempotency:
    def test_duplicate_id_ignored(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="dup-id", raw_text="first version")
            _insert_sample(store, id="dup-id", raw_text="second version")

            results = store.search("version")
            # Only the first insert should survive
            assert len(results) == 1
            assert results[0]["raw_text"] == "first version"

    def test_duplicate_insert_no_error(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="dup-id")
            # Should not raise
            _insert_sample(store, id="dup-id")


class TestLimitAndOrder:
    def test_limit_respected(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            for i in range(10):
                _insert_sample(
                    store,
                    id=f"id-{i}",
                    timestamp=f"2026-04-04T12:{i:02d}:00+00:00",
                    raw_text="common keyword",
                )

            results = store.search("common", limit=3)
            assert len(results) == 3

    def test_order_by_timestamp_desc(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(
                store,
                id="old",
                timestamp="2026-01-01T00:00:00+00:00",
                raw_text="shared text",
            )
            _insert_sample(
                store,
                id="new",
                timestamp="2026-12-31T23:59:59+00:00",
                raw_text="shared text",
            )

            results = store.search("shared")
            assert len(results) == 2
            assert results[0]["id"] == "new"
            assert results[1]["id"] == "old"


# =============================================================================
# Config.db_path
# =============================================================================


class TestConfigDbPath:
    def test_db_path_property(self) -> None:
        cfg = Config()
        expected = Path("~/.companion-capture").expanduser() / "captures.db"
        assert cfg.db_path == expected


# =============================================================================
# Dual-write integration tests
# =============================================================================


class TestDualWrite:
    def test_append_capture_writes_to_sqlite(self, tmp_path: Path) -> None:
        config = _test_config(tmp_path)
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            append_capture(
                "Hello from goose", "vibe", config, store=store, session_id="s1"
            )

            results = store.search("Hello")
            assert len(results) == 1
            row = results[0]
            assert row["raw_text"] == "Hello from goose"
            assert row["classification"] == "vibe"
            assert row["session_id"] == "s1"
            assert row["schema_version"] == SCHEMA_VERSION

    def test_append_capture_without_store(self, tmp_path: Path) -> None:
        config = _test_config(tmp_path)
        # store=None (default) should not raise
        append_capture("No store here", "vibe", config, store=None)
        # Markdown file should still be written
        assert config.captures_file.exists()
        content = config.captures_file.read_text()
        assert "No store here" in content

    def test_sqlite_failure_is_silent(self, tmp_path: Path) -> None:
        config = _test_config(tmp_path)
        db = tmp_path / "test.db"
        store = CaptureStore(db)
        store.open()
        # Make the DB read-only by closing and reopening with read-only connection
        store._conn.close()
        store._conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            # Should not raise even though write will fail
            append_capture("This will fail silently", "vibe", config, store=store)
            # Markdown should still be written
            assert config.captures_file.exists()
            content = config.captures_file.read_text()
            assert "This will fail silently" in content
        finally:
            store.close()

    def test_sqlite_failure_with_debug(self, tmp_path: Path, capsys) -> None:
        config = _test_config(tmp_path)
        db = tmp_path / "test.db"
        store = CaptureStore(db, debug=True)
        store.open()
        store._conn.close()
        store._conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            append_capture("Debug fail", "vibe", config, store=store)
            captured = capsys.readouterr()
            assert "SQLite write failed" in captured.err
        finally:
            store.close()

    def test_dual_write_entry_id_matches(self, tmp_path: Path) -> None:
        """The UUID in the markdown and SQLite row should come from the same entry_id."""
        config = _test_config(tmp_path)
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            append_capture("match test", "vibe", config, store=store)

            # Get the UUID from the markdown file
            md_content = config.captures_file.read_text()
            import re

            md_id = re.search(r"id:([0-9a-f-]+)", md_content)
            assert md_id is not None

            # Get the UUID from SQLite
            results = store.search("match")
            assert len(results) == 1
            assert results[0]["id"] == md_id.group(1)


# =============================================================================
# Context manager
# =============================================================================


class TestContextManager:
    def test_with_statement(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            assert store._conn is not None
            _insert_sample(store)
            results = store.search("Hello")
            assert len(results) == 1
        # After exiting, connection should be closed
        assert store._conn is None

    def test_close_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = CaptureStore(db)
        store.open()
        store.close()
        store.close()  # second close should not raise
        assert store._conn is None


# =============================================================================
# Edge cases
# =============================================================================


class TestEdgeCases:
    def test_empty_search(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store)
            results = store.search("nonexistent_xyz_term")
            assert results == []

    def test_insert_before_open(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = CaptureStore(db)
        # conn is None, insert should be a no-op
        store.insert(
            id="nope",
            timestamp="2026-01-01T00:00:00+00:00",
            project="p",
            session_id="s",
            raw_text="text",
            classification="vibe",
            schema_version=1,
        )
        # No error raised, nothing stored

    def test_search_before_open(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = CaptureStore(db)
        results = store.search("anything")
        assert results == []

    def test_db_parent_dirs_created(self, tmp_path: Path) -> None:
        db = tmp_path / "deep" / "nested" / "dir" / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store)
            results = store.search("Hello")
            assert len(results) == 1


# =============================================================================
# search() since parameter
# =============================================================================


class TestSearchSince:
    def test_since_filters_old_entries(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(
                store,
                id="old",
                timestamp="2026-01-01T00:00:00+00:00",
                raw_text="common word",
            )
            _insert_sample(
                store,
                id="mid",
                timestamp="2026-03-01T00:00:00+00:00",
                raw_text="common word",
            )
            _insert_sample(
                store,
                id="new",
                timestamp="2026-04-01T00:00:00+00:00",
                raw_text="common word",
            )

            results = store.search("common", since="2026-02-01T00:00:00+00:00")
            ids = {r["id"] for r in results}
            assert ids == {"mid", "new"}

    def test_since_with_project_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(
                store,
                id="1",
                timestamp="2026-01-01T00:00:00+00:00",
                project="proj-a",
                raw_text="shared term",
            )
            _insert_sample(
                store,
                id="2",
                timestamp="2026-04-01T00:00:00+00:00",
                project="proj-a",
                raw_text="shared term",
            )
            _insert_sample(
                store,
                id="3",
                timestamp="2026-04-01T00:00:00+00:00",
                project="proj-b",
                raw_text="shared term",
            )

            results = store.search(
                "shared",
                project="proj-a",
                since="2026-02-01T00:00:00+00:00",
            )
            assert len(results) == 1
            assert results[0]["id"] == "2"

    def test_since_none_returns_all(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(
                store,
                id="1",
                timestamp="2026-01-01T00:00:00+00:00",
                raw_text="keyword xyz",
            )
            _insert_sample(
                store,
                id="2",
                timestamp="2026-04-01T00:00:00+00:00",
                raw_text="keyword xyz",
            )

            results = store.search("keyword", since=None)
            assert len(results) == 2


# =============================================================================
# recent() method
# =============================================================================


class TestRecent:
    def test_recent_returns_latest(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            for i in range(5):
                _insert_sample(
                    store,
                    id=f"id-{i}",
                    timestamp=f"2026-04-04T12:{i:02d}:00+00:00",
                )

            results = store.recent(limit=3)
            assert len(results) == 3
            # Should be newest first
            assert results[0]["id"] == "id-4"
            assert results[1]["id"] == "id-3"
            assert results[2]["id"] == "id-2"

    def test_recent_project_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="1", project="proj-a")
            _insert_sample(store, id="2", project="proj-b")
            _insert_sample(store, id="3", project="proj-a")

            results = store.recent(project="proj-a")
            assert len(results) == 2
            assert all(r["project"] == "proj-a" for r in results)

    def test_recent_default_limit(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            for i in range(25):
                _insert_sample(
                    store,
                    id=f"id-{i}",
                    timestamp=f"2026-04-04T12:{i:02d}:00+00:00",
                )

            results = store.recent()
            assert len(results) == 20

    def test_recent_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            results = store.recent()
            assert results == []

    def test_recent_before_open(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = CaptureStore(db)
        results = store.recent()
        assert results == []


# =============================================================================
# stats() method
# =============================================================================


class TestStats:
    def test_stats_counts(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="1", project="proj-a", classification="vibe")
            _insert_sample(store, id="2", project="proj-a", classification="debug")
            _insert_sample(store, id="3", project="proj-b", classification="vibe")
            _insert_sample(store, id="4", project="proj-b", classification="vibe")
            _insert_sample(store, id="5", project="proj-b", classification="debug")

            s = store.stats()
            assert s["total"] == 5
            assert s["by_project"] == {"proj-a": 2, "proj-b": 3}
            assert s["by_classification"] == {"vibe": 3, "debug": 2}

    def test_stats_date_range(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="early", timestamp="2026-01-15T08:00:00+00:00")
            _insert_sample(store, id="late", timestamp="2026-04-04T22:00:00+00:00")

            s = store.stats()
            assert s["earliest"] == "2026-01-15T08:00:00+00:00"
            assert s["latest"] == "2026-04-04T22:00:00+00:00"

    def test_stats_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            s = store.stats()
            assert s["total"] == 0
            assert s["by_project"] == {}
            assert s["by_classification"] == {}
            assert s["earliest"] is None
            assert s["latest"] is None

    def test_stats_before_open(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = CaptureStore(db)
        s = store.stats()
        assert s["total"] == 0
        assert s["by_project"] == {}
        assert s["by_classification"] == {}
        assert s["earliest"] is None
        assert s["latest"] is None
