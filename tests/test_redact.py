"""Tests for companion-capture redact subcommand — delete_matching, redact_markdown_file, cmd_redact."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from companion_capture.cli import (
    cmd_redact,
    redact_markdown_file,
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


def _make_markdown(filepath: Path, entries: list[tuple[str, str]]) -> None:
    """Write a markdown file with entries. Each tuple is (comment_or_none, entry_line)."""
    lines = []
    for comment, entry in entries:
        if comment:
            lines.append(comment + "\n")
        lines.append(entry + "\n")
    filepath.write_text("".join(lines), encoding="utf-8")


_SAMPLE_COMMENT = "<!-- schema:1 id:aaa-bbb-ccc ts:2026-04-04T12:00:00+00:00 -->"


# =============================================================================
# CaptureStore.delete_matching
# =============================================================================


class TestDeleteMatching:
    def test_deletes_matching_rows(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="1", raw_text="secret password here")
            _insert_sample(store, id="2", raw_text="normal message")
            _insert_sample(store, id="3", raw_text="another secret thing")

            deleted = store.delete_matching(r"secret")
            assert deleted == 2

            # Verify matching rows are gone
            results = store.search("secret")
            assert len(results) == 0

            # Verify non-matching rows preserved
            results = store.search("normal")
            assert len(results) == 1
            assert results[0]["id"] == "2"

    def test_returns_zero_when_no_match(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="1", raw_text="keep this")
            _insert_sample(store, id="2", raw_text="keep that")

            deleted = store.delete_matching(r"nonexistent_xyz")
            assert deleted == 0

            # All rows still present
            results = store.recent()
            assert len(results) == 2

    def test_returns_zero_before_open(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = CaptureStore(db)
        deleted = store.delete_matching(r"anything")
        assert deleted == 0

    def test_regex_pattern(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="1", raw_text="error code 404")
            _insert_sample(store, id="2", raw_text="error code 500")
            _insert_sample(store, id="3", raw_text="success code 200")

            deleted = store.delete_matching(r"error code \d+")
            assert deleted == 2


class TestDeleteMatchingFts:
    def test_fts_index_cleaned(self, tmp_path: Path) -> None:
        """After delete_matching, FTS5 search should not find removed rows."""
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            if not store.has_fts5:
                pytest.skip("FTS5 not available")

            _insert_sample(store, id="1", raw_text="sensitive data here")
            _insert_sample(store, id="2", raw_text="normal data")

            # Confirm FTS finds it before delete
            results = store.search("sensitive")
            assert len(results) == 1

            store.delete_matching(r"sensitive")

            # FTS should no longer find it
            results = store.search("sensitive")
            assert len(results) == 0

            # Non-matching still searchable
            results = store.search("normal")
            assert len(results) == 1

    def test_fts_multiple_deletes(self, tmp_path: Path) -> None:
        """Multiple matching rows should all be removed from FTS."""
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            if not store.has_fts5:
                pytest.skip("FTS5 not available")

            _insert_sample(store, id="1", raw_text="remove alpha text")
            _insert_sample(store, id="2", raw_text="remove beta text")
            _insert_sample(store, id="3", raw_text="keep this text")

            store.delete_matching(r"remove")

            results = store.search("remove")
            assert len(results) == 0

            results = store.search("keep")
            assert len(results) == 1


# =============================================================================
# redact_markdown_file
# =============================================================================


class TestRedactMarkdownFile:
    def test_removes_new_format_entries(self, tmp_path: Path) -> None:
        md = tmp_path / "captures.md"
        _make_markdown(
            md,
            [
                (_SAMPLE_COMMENT, "- `[vibe]` `14:30` `proj` — secret password"),
                (
                    "<!-- schema:1 id:ddd-eee ts:2026-04-04T13:00:00+00:00 -->",
                    "- `[vibe]` `15:00` `proj` — normal message",
                ),
            ],
        )

        removed = redact_markdown_file(md, r"secret")
        assert removed == 1

        content = md.read_text()
        assert "secret" not in content
        assert "normal message" in content
        # Comment line for kept entry should remain
        assert "ddd-eee" in content
        # Comment line for removed entry should be gone
        assert "aaa-bbb-ccc" not in content

    def test_removes_old_format_entries(self, tmp_path: Path) -> None:
        md = tmp_path / "captures.md"
        md.write_text(
            "- `[vibe]` `14:30` `proj` — secret data\n"
            "- `[debug]` `15:00` `proj` — normal info\n",
            encoding="utf-8",
        )

        removed = redact_markdown_file(md, r"secret")
        assert removed == 1

        content = md.read_text()
        assert "secret" not in content
        assert "normal info" in content

    def test_removes_mixed_format(self, tmp_path: Path) -> None:
        md = tmp_path / "captures.md"
        lines = (
            "- `[vibe]` `10:00` `proj` — old secret\n"
            f"{_SAMPLE_COMMENT}\n"
            "- `[vibe]` `14:30` `proj` — new secret\n"
            "<!-- schema:1 id:ddd-eee ts:2026-04-04T13:00:00+00:00 -->\n"
            "- `[vibe]` `15:00` `proj` — keep this\n"
        )
        md.write_text(lines, encoding="utf-8")

        removed = redact_markdown_file(md, r"secret")
        assert removed == 2

        content = md.read_text()
        assert "secret" not in content
        assert "keep this" in content

    def test_no_matches_leaves_file_unchanged(self, tmp_path: Path) -> None:
        md = tmp_path / "captures.md"
        original = f"{_SAMPLE_COMMENT}\n- `[vibe]` `14:30` `proj` — safe message\n"
        md.write_text(original, encoding="utf-8")

        removed = redact_markdown_file(md, r"nonexistent_pattern")
        assert removed == 0
        assert md.read_text() == original


class TestRedactMarkdownEmpty:
    def test_file_does_not_exist(self, tmp_path: Path) -> None:
        md = tmp_path / "nonexistent.md"
        removed = redact_markdown_file(md, r"anything")
        assert removed == 0

    def test_empty_file(self, tmp_path: Path) -> None:
        md = tmp_path / "empty.md"
        md.write_text("", encoding="utf-8")
        removed = redact_markdown_file(md, r"anything")
        assert removed == 0


# =============================================================================
# cmd_redact dry-run
# =============================================================================


class TestRedactDryRun:
    def test_dry_run_shows_counts(self, tmp_path: Path, capsys, monkeypatch) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="1", raw_text="remove this secret")
            _insert_sample(store, id="2", raw_text="keep this safe")

        # Create a markdown file with matches
        config = _test_config(tmp_path)
        md = config.captures_file
        _make_markdown(
            md,
            [
                (_SAMPLE_COMMENT, "- `[vibe]` `14:30` `proj` — secret data"),
                (
                    "<!-- schema:1 id:ddd-eee ts:2026-04-04T13:00:00+00:00 -->",
                    "- `[vibe]` `15:00` `proj` — safe data",
                ),
            ],
        )

        monkeypatch.setattr(
            "companion_capture.cli.Config",
            _patched_config_class(tmp_path, db),
        )

        args = argparse.Namespace(pattern=r"secret", confirm=False)
        rc = cmd_redact(args)
        out = capsys.readouterr().out

        assert rc == 0
        assert "1 entries match" in out or "1 rows match" in out
        assert "Re-run with --confirm" in out

        # Verify nothing was actually deleted
        with CaptureStore(db) as store:
            results = store.recent()
            assert len(results) == 2
        assert "secret data" in md.read_text()

    def test_dry_run_no_matches(self, tmp_path: Path, capsys, monkeypatch) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="1", raw_text="safe message")

        monkeypatch.setattr(
            "companion_capture.cli.Config",
            _patched_config_class(tmp_path, db),
        )

        args = argparse.Namespace(pattern=r"nonexistent_xyz", confirm=False)
        rc = cmd_redact(args)
        out = capsys.readouterr().out

        assert rc == 0
        assert "No captures match" in out


# =============================================================================
# cmd_redact end-to-end (with --confirm)
# =============================================================================


def _patched_config_class(tmp_path: Path, db: Path):
    """Create a PatchedConfig class whose load() returns a config pointing to tmp_path."""

    class PatchedConfig(Config):
        @classmethod
        def load(cls, config_path=None):
            return cls(
                companion_name="TestCompanion",
                output_dir=str(tmp_path),
                log_dir=str(tmp_path / "logs"),
            )

        @property
        def db_path(self):
            return db

    return PatchedConfig


class TestRedactEndToEnd:
    def test_deletes_from_sqlite_and_markdown(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            _insert_sample(store, id="1", raw_text="secret password 123")
            _insert_sample(store, id="2", raw_text="normal message")

        config = _test_config(tmp_path)
        md = config.captures_file
        _make_markdown(
            md,
            [
                (_SAMPLE_COMMENT, "- `[vibe]` `14:30` `proj` — secret password 123"),
                (
                    "<!-- schema:1 id:ddd-eee ts:2026-04-04T13:00:00+00:00 -->",
                    "- `[vibe]` `15:00` `proj` — safe message",
                ),
            ],
        )

        monkeypatch.setattr(
            "companion_capture.cli.Config",
            _patched_config_class(tmp_path, db),
        )

        args = argparse.Namespace(pattern=r"secret", confirm=True)
        rc = cmd_redact(args)
        out = capsys.readouterr().out

        assert rc == 0
        assert "redacted" in out.lower() or "removed" in out.lower()

        # SQLite: only safe row remains
        with CaptureStore(db) as store:
            results = store.recent()
            assert len(results) == 1
            assert results[0]["raw_text"] == "normal message"

        # Markdown: only safe entry remains
        content = md.read_text()
        assert "secret" not in content
        assert "safe message" in content

    def test_redact_multiple_md_files(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        """Redact hits captures, debug, and archive files."""
        db = tmp_path / "test.db"
        with CaptureStore(db) as _:
            pass

        config = _test_config(tmp_path)

        # Write matching entries to all three files
        for filepath in [config.captures_file, config.debug_file, config.archive_file]:
            _make_markdown(
                filepath,
                [
                    (_SAMPLE_COMMENT, "- `[vibe]` `14:30` `proj` — toxic data"),
                    (
                        "<!-- schema:1 id:keep ts:2026-04-04T13:00:00+00:00 -->",
                        "- `[vibe]` `15:00` `proj` — clean data",
                    ),
                ],
            )

        monkeypatch.setattr(
            "companion_capture.cli.Config",
            _patched_config_class(tmp_path, db),
        )

        args = argparse.Namespace(pattern=r"toxic", confirm=True)
        rc = cmd_redact(args)

        assert rc == 0

        for filepath in [config.captures_file, config.debug_file, config.archive_file]:
            content = filepath.read_text()
            assert "toxic" not in content
            assert "clean data" in content


# =============================================================================
# Invalid regex
# =============================================================================


class TestRedactInvalidRegex:
    def test_invalid_regex_returns_error(self, tmp_path: Path, capsys) -> None:
        args = argparse.Namespace(pattern=r"[invalid", confirm=False)
        rc = cmd_redact(args)
        err = capsys.readouterr().err

        assert rc == 1
        assert "invalid regex" in err

    def test_invalid_regex_with_confirm(self, tmp_path: Path, capsys) -> None:
        args = argparse.Namespace(pattern=r"(unclosed", confirm=True)
        rc = cmd_redact(args)
        err = capsys.readouterr().err

        assert rc == 1
        assert "invalid regex" in err
