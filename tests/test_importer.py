"""Tests for companion_capture.importer — markdown parsing, import, and backfill."""

from __future__ import annotations

from pathlib import Path


from companion_capture.config import Config, SCHEMA_VERSION
from companion_capture.importer import (
    ParsedEntry,
    compute_deterministic_id,
    discover_files,
    import_to_store,
    parse_markdown_file,
)
from companion_capture.store import CaptureStore


# -- Helpers -------------------------------------------------------------------


def _test_config(tmp_path: Path, name: str = "TestCompanion") -> Config:
    return Config(
        companion_name=name,
        output_dir=str(tmp_path),
        log_dir=str(tmp_path / "logs"),
    )


def _write_md(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# =============================================================================
# compute_deterministic_id
# =============================================================================


class TestDeterministicId:
    def test_produces_uuid_like_format(self) -> None:
        result = compute_deterministic_id("2026-04-03", "10:48", "proj", "msg")
        parts = result.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[1]) == 4
        assert len(parts[2]) == 4
        assert len(parts[3]) == 4
        assert len(parts[4]) == 12

    def test_same_input_same_output(self) -> None:
        a = compute_deterministic_id("2026-04-03", "10:48", "proj", "msg")
        b = compute_deterministic_id("2026-04-03", "10:48", "proj", "msg")
        assert a == b

    def test_different_input_different_output(self) -> None:
        a = compute_deterministic_id("2026-04-03", "10:48", "proj", "msg A")
        b = compute_deterministic_id("2026-04-03", "10:48", "proj", "msg B")
        assert a != b

    def test_date_matters(self) -> None:
        a = compute_deterministic_id("2026-04-03", "10:48", "proj", "msg")
        b = compute_deterministic_id("2026-04-04", "10:48", "proj", "msg")
        assert a != b

    def test_time_matters(self) -> None:
        a = compute_deterministic_id("2026-04-03", "10:48", "proj", "msg")
        b = compute_deterministic_id("2026-04-03", "11:00", "proj", "msg")
        assert a != b

    def test_project_matters(self) -> None:
        a = compute_deterministic_id("2026-04-03", "10:48", "proj-a", "msg")
        b = compute_deterministic_id("2026-04-03", "10:48", "proj-b", "msg")
        assert a != b


# =============================================================================
# parse_markdown_file
# =============================================================================


class TestParseOldFormat:
    """Old format: no identity comments."""

    def test_single_entry(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Keel — Auto-Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:48` `MyProject` — Hello world\n",
        )
        entries = parse_markdown_file(md)
        assert len(entries) == 1
        e = entries[0]
        assert e.classification == "vibe"
        assert e.project == "MyProject"
        assert e.raw_text == "Hello world"
        assert e.timestamp == "2026-04-03T10:48:00+00:00"

    def test_multiple_entries_same_date(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:00` `proj` — First\n"
            "- `[debug]` `10:05` `proj` — Second\n"
            "- `[vibe]` `10:10` `proj` — Third\n",
        )
        entries = parse_markdown_file(md)
        assert len(entries) == 3
        assert entries[0].raw_text == "First"
        assert entries[1].raw_text == "Second"
        assert entries[2].raw_text == "Third"

    def test_multiple_dates(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:00` `proj` — Day one\n"
            "\n"
            "### 2026-04-04\n"
            "- `[vibe]` `11:00` `proj` — Day two\n",
        )
        entries = parse_markdown_file(md)
        assert len(entries) == 2
        assert entries[0].timestamp.startswith("2026-04-03")
        assert entries[1].timestamp.startswith("2026-04-04")

    def test_deterministic_id_assigned(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:48` `proj` — Hello\n",
        )
        entries = parse_markdown_file(md)
        expected_id = compute_deterministic_id("2026-04-03", "10:48", "proj", "Hello")
        assert entries[0].id == expected_id

    def test_debug_classification(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[debug]` `12:00` `proj` — Debug msg\n",
        )
        entries = parse_markdown_file(md)
        assert entries[0].classification == "debug"


class TestParseNewFormat:
    """New format: with <!-- schema:1 id:UUID ts:ISO --> identity comments."""

    def test_identity_preserved(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-04\n"
            "<!-- schema:1 id:aaa1bbb2-ccc3-ddd4-eee5-fff6aaa7bbb8 ts:2026-04-04T12:00:00+00:00 -->\n"
            "- `[vibe]` `12:00` `proj` — New format entry\n",
        )
        entries = parse_markdown_file(md)
        assert len(entries) == 1
        assert entries[0].id == "aaa1bbb2-ccc3-ddd4-eee5-fff6aaa7bbb8"
        assert entries[0].timestamp == "2026-04-04T12:00:00+00:00"

    def test_mixed_old_and_new(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:00` `proj` — Old entry\n"
            "\n"
            "### 2026-04-04\n"
            "<!-- schema:1 id:aabb0011-2233-4455-6677-8899aabbccdd ts:2026-04-04T11:00:00+00:00 -->\n"
            "- `[vibe]` `11:00` `proj` — New entry\n",
        )
        entries = parse_markdown_file(md)
        assert len(entries) == 2
        # Old entry gets deterministic ID
        assert entries[0].id == compute_deterministic_id(
            "2026-04-03", "10:00", "proj", "Old entry"
        )
        # New entry keeps its UUID
        assert entries[1].id == "aabb0011-2233-4455-6677-8899aabbccdd"

    def test_identity_only_applies_to_next_entry(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-04\n"
            "<!-- schema:1 id:ff110022-3344-5566-7788-99aabbccddee ts:2026-04-04T10:00:00+00:00 -->\n"
            "- `[vibe]` `10:00` `proj` — First\n"
            "- `[vibe]` `10:05` `proj` — Second (no identity)\n",
        )
        entries = parse_markdown_file(md)
        assert len(entries) == 2
        assert entries[0].id == "ff110022-3344-5566-7788-99aabbccddee"
        # Second entry should get deterministic ID, not reuse the first UUID
        assert entries[1].id != "ff110022-3344-5566-7788-99aabbccddee"
        expected = compute_deterministic_id(
            "2026-04-04", "10:05", "proj", "Second (no identity)"
        )
        assert entries[1].id == expected


class TestParseEdgeCases:
    def test_nonexistent_file(self, tmp_path: Path) -> None:
        entries = parse_markdown_file(tmp_path / "nope.md")
        assert entries == []

    def test_empty_file(self, tmp_path: Path) -> None:
        md = _write_md(tmp_path / "empty.md", "")
        entries = parse_markdown_file(md)
        assert entries == []

    def test_no_date_header_skips_entries(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "no_date.md",
            "# Captures\n\n## Log\n\n"
            "- `[vibe]` `10:00` `proj` — No date header above\n",
        )
        entries = parse_markdown_file(md)
        assert entries == []

    def test_header_only_no_entries(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "header_only.md",
            "# Captures\n\n## Log\n\n### 2026-04-03\n",
        )
        entries = parse_markdown_file(md)
        assert entries == []

    def test_project_with_spaces(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:00` `Keel Implementation` — Spaced project\n",
        )
        entries = parse_markdown_file(md)
        assert entries[0].project == "Keel Implementation"

    def test_message_with_backticks_and_dashes(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:00` `proj` — _honks_ Check `foo_bar` — it's broken\n",
        )
        entries = parse_markdown_file(md)
        assert entries[0].raw_text == "_honks_ Check `foo_bar` — it's broken"

    def test_schema_version_default(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:00` `proj` — Hello\n",
        )
        entries = parse_markdown_file(md)
        assert entries[0].schema_version == SCHEMA_VERSION


# =============================================================================
# discover_files
# =============================================================================


class TestDiscoverFiles:
    def test_finds_companion_captures(self, tmp_path: Path) -> None:
        config = _test_config(tmp_path, "TestCompanion")
        _write_md(tmp_path / "TestCompanion-captures.md", "# content")
        found = discover_files(config)
        assert len(found) == 1
        assert found[0].name == "TestCompanion-captures.md"

    def test_finds_companion_archive(self, tmp_path: Path) -> None:
        config = _test_config(tmp_path, "TestCompanion")
        _write_md(tmp_path / "TestCompanion-archive.md", "# content")
        found = discover_files(config)
        assert len(found) == 1
        assert found[0].name == "TestCompanion-archive.md"

    def test_finds_both_companion_files(self, tmp_path: Path) -> None:
        config = _test_config(tmp_path, "TestCompanion")
        _write_md(tmp_path / "TestCompanion-captures.md", "# captures")
        _write_md(tmp_path / "TestCompanion-archive.md", "# archive")
        found = discover_files(config)
        assert len(found) == 2

    def test_finds_keel_legacy_files(self, tmp_path: Path) -> None:
        config = _test_config(tmp_path, "TestCompanion")
        _write_md(tmp_path / "KEEL-captures.md", "# keel captures")
        _write_md(tmp_path / "KEEL-archive.md", "# keel archive")
        found = discover_files(config)
        names = {f.name for f in found}
        assert "KEEL-captures.md" in names
        assert "KEEL-archive.md" in names

    def test_finds_both_companion_and_keel(self, tmp_path: Path) -> None:
        config = _test_config(tmp_path, "TestCompanion")
        _write_md(tmp_path / "TestCompanion-captures.md", "# new")
        _write_md(tmp_path / "KEEL-captures.md", "# old")
        found = discover_files(config)
        assert len(found) == 2

    def test_no_files_found(self, tmp_path: Path) -> None:
        config = _test_config(tmp_path, "TestCompanion")
        found = discover_files(config)
        assert found == []

    def test_skips_debug_files(self, tmp_path: Path) -> None:
        config = _test_config(tmp_path, "TestCompanion")
        _write_md(tmp_path / "TestCompanion-debug.md", "# debug")
        _write_md(tmp_path / "KEEL-debug.md", "# keel debug")
        found = discover_files(config)
        assert found == []

    def test_keel_name_no_duplicate(self, tmp_path: Path) -> None:
        """If companion_name is KEEL, don't discover KEEL files twice."""
        config = _test_config(tmp_path, "KEEL")
        _write_md(tmp_path / "KEEL-captures.md", "# content")
        found = discover_files(config)
        assert len(found) == 1


# =============================================================================
# import_to_store
# =============================================================================


class TestImportToStore:
    def test_basic_import(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        entries = [
            ParsedEntry(
                id="test-id-1",
                timestamp="2026-04-03T10:00:00+00:00",
                project="proj",
                raw_text="Hello",
                classification="vibe",
            )
        ]
        with CaptureStore(db) as store:
            result = import_to_store(store, entries)
            assert result.entries_imported == 1
            assert result.entries_skipped == 0

            rows = store.search("Hello")
            assert len(rows) == 1
            assert rows[0]["id"] == "test-id-1"

    def test_idempotent_import(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        entries = [
            ParsedEntry(
                id="test-id-1",
                timestamp="2026-04-03T10:00:00+00:00",
                project="proj",
                raw_text="Hello",
                classification="vibe",
            )
        ]
        with CaptureStore(db) as store:
            result1 = import_to_store(store, entries)
            assert result1.entries_imported == 1

            result2 = import_to_store(store, entries)
            assert result2.entries_imported == 0
            assert result2.entries_skipped == 1

    def test_dry_run(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        entries = [
            ParsedEntry(
                id="test-id-1",
                timestamp="2026-04-03T10:00:00+00:00",
                project="proj",
                raw_text="Hello",
                classification="vibe",
            )
        ]
        with CaptureStore(db) as store:
            result = import_to_store(store, entries, dry_run=True)
            assert result.entries_imported == 0
            assert result.entries_skipped == len(entries)

            # Nothing should be in the DB
            rows = store.search("Hello")
            assert rows == []

    def test_multiple_entries(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        entries = [
            ParsedEntry(
                id=f"id-{i}",
                timestamp=f"2026-04-03T{10 + i}:00:00+00:00",
                project="proj",
                raw_text=f"Message {i}",
                classification="vibe",
            )
            for i in range(5)
        ]
        with CaptureStore(db) as store:
            result = import_to_store(store, entries)
            assert result.entries_imported == 5
            assert result.entries_skipped == 0

    def test_empty_entries_list(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            result = import_to_store(store, [])
            assert result.entries_parsed == 0
            assert result.entries_imported == 0


# =============================================================================
# End-to-end: parse + import
# =============================================================================


class TestEndToEnd:
    def test_parse_and_import_old_format(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "KEEL-captures.md",
            "# Keel — Auto-Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:48` `Keel Implementation` — Watch your imports\n"
            "- `[debug]` `11:00` `Keel Implementation` — Check the buffer\n",
        )
        entries = parse_markdown_file(md)
        assert len(entries) == 2

        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            result = import_to_store(store, entries)
            assert result.entries_imported == 2

            rows = store.search("imports")
            assert len(rows) == 1
            assert rows[0]["project"] == "Keel Implementation"

    def test_parse_and_import_new_format(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-04\n"
            "<!-- schema:1 id:dead0beef-cafe-babe-face-0123456789ab ts:2026-04-04T12:00:00+00:00 -->\n"
            "- `[vibe]` `12:00` `proj` — New format\n",
        )
        entries = parse_markdown_file(md)
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            result = import_to_store(store, entries)
            assert result.entries_imported == 1

            rows = store.search("New format")
            assert rows[0]["id"] == "dead0beef-cafe-babe-face-0123456789ab"
            assert rows[0]["timestamp"] == "2026-04-04T12:00:00+00:00"

    def test_reimport_idempotent(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path / "captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:00` `proj` — Stable entry\n",
        )
        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            entries1 = parse_markdown_file(md)
            import_to_store(store, entries1)

            entries2 = parse_markdown_file(md)
            result = import_to_store(store, entries2)
            assert result.entries_imported == 0
            assert result.entries_skipped == 1

    def test_full_pipeline_with_discover(self, tmp_path: Path) -> None:
        config = _test_config(tmp_path, "TestCompanion")
        _write_md(
            tmp_path / "TestCompanion-captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:00` `proj` — From captures\n",
        )
        _write_md(
            tmp_path / "TestCompanion-archive.md",
            "# Archive\n\n## Log\n\n"
            "### 2026-03-01\n"
            "- `[vibe]` `09:00` `proj` — From archive\n",
        )

        files = discover_files(config)
        assert len(files) == 2

        all_entries = []
        for f in files:
            all_entries.extend(parse_markdown_file(f))
        assert len(all_entries) == 2

        db = tmp_path / "test.db"
        with CaptureStore(db) as store:
            result = import_to_store(store, all_entries)
            assert result.entries_imported == 2


# =============================================================================
# CLI integration (import_captures function)
# =============================================================================


class TestImportCLI:
    def test_import_no_files(self, tmp_path: Path, capsys, monkeypatch) -> None:
        from companion_capture.cli import import_captures

        monkeypatch.setattr(
            "companion_capture.cli.Config.load",
            classmethod(lambda cls: _test_config(tmp_path)),
        )
        exit_code = import_captures()
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "No capture files found" in captured.out

    def test_import_dry_run(self, tmp_path: Path, capsys, monkeypatch) -> None:
        from companion_capture.cli import import_captures

        config = _test_config(tmp_path)
        _write_md(
            tmp_path / "TestCompanion-captures.md",
            "# Captures\n\n## Log\n\n"
            "### 2026-04-03\n"
            "- `[vibe]` `10:00` `proj` — Test entry\n",
        )
        monkeypatch.setattr(
            "companion_capture.cli.Config.load",
            classmethod(lambda cls: config),
        )
        exit_code = import_captures(dry_run=True)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Dry run" in captured.out
        assert "1 entries would be imported" in captured.out
