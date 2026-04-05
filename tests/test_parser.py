"""
test_parser.py — Pytest suite for companion speech bubble capture pipeline.

Covers: ScreenBuffer VT100 emulation, message extraction, classification,
dedup helpers, file routing, sweep mode, and edge cases.
"""

import os
from pathlib import Path
from unittest.mock import patch

from companion_capture.parser import (
    ScreenBuffer,
    find_keel_messages,
    classify,
    strip_ansi,
    _normalize,
    _is_same_message,
    _is_novel,
    _best_message,
    _word_bag,
    _append_entry,
    append_capture,
    sweep,
    BUBBLE_LINE_RE,
    GOOSE_MARKERS,
)
from companion_capture.config import Config

import companion_capture.parser as parser_module


# =============================================================================
# Test config helper
# =============================================================================


def _test_config(tmp_path, name="TestCompanion"):
    """Create a Config pointing to tmp_path for testing."""
    return Config(
        companion_name=name,
        output_dir=str(tmp_path),
        log_dir=str(tmp_path / "logs"),
    )


# =============================================================================
# Helpers
# =============================================================================

GOOSE_ART = [
    "     ___        ",
    "    (◉>         ",
    "   / |          ",
    "  /  |          ",
    " _(__)_         ",
]


def make_bubble(lines: list[str], width: int = 30) -> list[str]:
    """Build a speech bubble with the given text lines."""
    top = "╭" + "─" * (width + 2) + "╮"
    bot = "╰" + "─" * (width + 2) + "╯"
    body = [f"│ {line:<{width}} │" for line in lines]
    return [top] + body + [bot]


def goose_with_bubble(text_lines: list[str], width: int = 30) -> list[str]:
    """Return lines that look like the TUI's goose + speech bubble."""
    bubble = make_bubble(text_lines, width)
    # Pad goose art to match bubble height
    combined = []
    max_lines = max(len(bubble), len(GOOSE_ART))
    for i in range(max_lines):
        b = bubble[i] if i < len(bubble) else " " * (width + 4)
        g = GOOSE_ART[i] if i < len(GOOSE_ART) else ""
        combined.append(f"{b}    {g}")
    return combined


# =============================================================================
# ScreenBuffer — basic text placement
# =============================================================================


class TestScreenBufferBasic:
    def test_plain_text(self):
        sb = ScreenBuffer()
        sb.feed("hello")
        lines = sb.get_lines()
        assert lines[0].startswith("hello")

    def test_newline_advances_row(self):
        """LF advances row only; CR+LF resets column too."""
        sb = ScreenBuffer()
        sb.feed("line1\r\nline2")
        lines = sb.get_lines()
        assert lines[0].startswith("line1")
        assert lines[1].startswith("line2")

    def test_lf_only_preserves_column(self):
        """Pure LF without CR keeps the column position."""
        sb = ScreenBuffer()
        sb.feed("ABCDE\nX")
        lines = sb.get_lines()
        # col was 5 after "ABCDE", LF only moves row, so X lands at col 5
        assert lines[1] == "     X"

    def test_carriage_return(self):
        sb = ScreenBuffer()
        sb.feed("abcdef\rXY")
        lines = sb.get_lines()
        assert lines[0].startswith("XYcdef")

    def test_backspace(self):
        sb = ScreenBuffer()
        sb.feed("abc\x08X")
        lines = sb.get_lines()
        assert lines[0].startswith("abX")

    def test_control_chars_skipped(self):
        sb = ScreenBuffer()
        sb.feed("a\x07b\x01c")
        lines = sb.get_lines()
        assert lines[0].startswith("abc")


# =============================================================================
# ScreenBuffer — CSI cursor movement
# =============================================================================


class TestScreenBufferCursor:
    def test_cursor_up(self):
        sb = ScreenBuffer()
        # Place cursor at row 3 col 1, then move up 2
        sb.feed("\x1b[3;1H\x1b[2AX")
        lines = sb.get_lines()
        # Row 3 (idx 2), up 2 -> row 1 (idx 0), col 0
        assert lines[0][0] == "X"

    def test_cursor_down(self):
        sb = ScreenBuffer()
        sb.feed("top\x1b[3BX")
        lines = sb.get_lines()
        assert lines[3][3] == "X"

    def test_cursor_right(self):
        sb = ScreenBuffer()
        sb.feed("A\x1b[5CX")
        lines = sb.get_lines()
        assert lines[0] == "A     X"

    def test_cursor_left(self):
        sb = ScreenBuffer()
        sb.feed("ABCDE\x1b[3DX")
        lines = sb.get_lines()
        assert lines[0].startswith("ABXDE")

    def test_cursor_to_column(self):
        sb = ScreenBuffer()
        sb.feed("ABCDEF\x1b[3GX")
        lines = sb.get_lines()
        # Column 3 is 1-based -> index 2
        assert lines[0].startswith("ABXDEF")

    def test_cursor_position_H(self):
        sb = ScreenBuffer()
        sb.feed("\x1b[5;10HX")
        lines = sb.get_lines()
        # Row 5, col 10 (1-based) -> index (4, 9)
        assert lines[4][9] == "X"

    def test_cursor_position_f(self):
        sb = ScreenBuffer()
        sb.feed("\x1b[3;7fX")
        lines = sb.get_lines()
        assert lines[2][6] == "X"

    def test_default_params(self):
        sb = ScreenBuffer()
        sb.feed("\x1b[HX")  # CSI H with no params = (1,1)
        lines = sb.get_lines()
        assert lines[0][0] == "X"

    def test_cursor_up_default(self):
        sb = ScreenBuffer()
        sb.feed("\n\n\x1b[AX")  # up 1 (default)
        lines = sb.get_lines()
        assert lines[1][0] == "X"


# =============================================================================
# ScreenBuffer — save/restore cursor
# =============================================================================


class TestScreenBufferSaveRestore:
    def test_esc_7_8(self):
        sb = ScreenBuffer()
        sb.feed("AB\x1b7\x1b[5;5H\x1b8X")
        lines = sb.get_lines()
        # Saved at (0,2), restored -> write X at (0,2)
        assert lines[0].startswith("ABX")

    def test_csi_s_u(self):
        sb = ScreenBuffer()
        sb.feed("AB\x1b[s\x1b[10;10H\x1b[uX")
        lines = sb.get_lines()
        assert lines[0].startswith("ABX")


# =============================================================================
# ScreenBuffer — erase
# =============================================================================


class TestScreenBufferErase:
    def test_erase_line_right(self):
        sb = ScreenBuffer()
        sb.feed("ABCDEF\x1b[4G\x1b[0K")  # move to col 4 (idx 3), erase right
        lines = sb.get_lines()
        assert lines[0] == "ABC"

    def test_erase_line_left(self):
        sb = ScreenBuffer()
        sb.feed("ABCDEF\x1b[4G\x1b[1K")  # erase left (inclusive)
        lines = sb.get_lines()
        assert lines[0] == "    EF"

    def test_erase_line_full(self):
        sb = ScreenBuffer()
        sb.feed("ABCDEF\x1b[2K")
        lines = sb.get_lines()
        assert lines[0] == ""

    def test_erase_display_below(self):
        sb = ScreenBuffer()
        sb.feed("row0\nrow1\nrow2\x1b[2;1H\x1b[0J")
        lines = sb.get_lines()
        assert lines[0].startswith("row0")
        assert lines[1] == ""
        assert lines[2] == ""

    def test_erase_display_full(self):
        sb = ScreenBuffer()
        sb.feed("hello\x1b[2J")
        lines = sb.get_lines()
        assert all(line == "" for line in lines)


# =============================================================================
# ScreenBuffer — linefeed scrolling
# =============================================================================


class TestScreenBufferScroll:
    def test_scroll_at_bottom(self):
        sb = ScreenBuffer()
        # Fill to last row with CR+LF, then one more
        for i in range(sb.ROWS):
            sb.feed(f"R{i}\r\n")
        sb.feed("overflow")
        lines = sb.get_lines()
        # Row 0 content should have scrolled away, last row has "overflow"
        assert not lines[0].startswith("R0")
        assert lines[sb.ROWS - 1].startswith("overflow")


# =============================================================================
# ScreenBuffer — mid-escape chunk splits
# =============================================================================


class TestScreenBufferChunkSplits:
    def test_split_inside_csi(self):
        """ESC [ 5 ; 1 0 H split between chunks."""
        sb = ScreenBuffer()
        sb.feed("\x1b[5;")
        sb.feed("10HX")
        lines = sb.get_lines()
        assert lines[4][9] == "X"

    def test_split_at_esc(self):
        """ESC at end of one chunk, rest in next."""
        sb = ScreenBuffer()
        sb.feed("AB\x1b")
        sb.feed("[3GX")
        lines = sb.get_lines()
        assert lines[0].startswith("ABX")

    def test_split_after_bracket(self):
        """ESC [ at end, params + command in next."""
        sb = ScreenBuffer()
        sb.feed("AB\x1b[")
        sb.feed("3GX")
        lines = sb.get_lines()
        assert lines[0].startswith("ABX")

    def test_split_in_osc(self):
        """OSC sequence split across chunks."""
        sb = ScreenBuffer()
        sb.feed("\x1b]0;title")
        sb.feed(" here\x07X")
        lines = sb.get_lines()
        # OSC is skipped, X at (0,0)
        assert lines[0].startswith("X")

    def test_pending_preserved_across_reset(self):
        """reset() must NOT clear _pending — in-flight escapes survive."""
        sb = ScreenBuffer()
        sb.feed("hello\x1b")
        sb.reset()
        sb.feed("[5;5HX")
        lines = sb.get_lines()
        assert lines[4][4] == "X"

    def test_split_esc_paren(self):
        """ESC ( at end of chunk, charset byte in next."""
        sb = ScreenBuffer()
        sb.feed("A\x1b(")
        sb.feed("BX")
        lines = sb.get_lines()
        assert lines[0].startswith("AX")


# =============================================================================
# ScreenBuffer — reset
# =============================================================================


class TestScreenBufferReset:
    def test_reset_clears_grid(self):
        sb = ScreenBuffer()
        sb.feed("stuff on screen")
        sb.reset()
        lines = sb.get_lines()
        assert all(line == "" for line in lines)

    def test_reset_resets_cursor(self):
        sb = ScreenBuffer()
        sb.feed("\x1b[10;20H")
        sb.reset()
        assert sb.row == 0
        assert sb.col == 0

    def test_reset_preserves_pending(self):
        sb = ScreenBuffer()
        sb.feed("x\x1b")
        assert sb._pending == "\x1b"
        sb.reset()
        assert sb._pending == "\x1b"


# =============================================================================
# ScreenBuffer — private modes and SGR ignored
# =============================================================================


class TestScreenBufferIgnored:
    def test_sgr_ignored(self):
        sb = ScreenBuffer()
        sb.feed("\x1b[1;31mhello\x1b[0m")
        lines = sb.get_lines()
        assert lines[0].startswith("hello")

    def test_private_mode_ignored(self):
        sb = ScreenBuffer()
        sb.feed("\x1b[?25lhello\x1b[?25h")
        lines = sb.get_lines()
        assert lines[0].startswith("hello")


# =============================================================================
# strip_ansi
# =============================================================================


class TestStripAnsi:
    def test_plain_text(self):
        assert strip_ansi("hello world") == "hello world"

    def test_sgr(self):
        assert strip_ansi("\x1b[1;31mred\x1b[0m") == "red"

    def test_cursor_moves(self):
        assert strip_ansi("\x1b[5Ahello") == "hello"

    def test_osc(self):
        assert strip_ansi("\x1b]0;title\x07text") == "text"

    def test_carriage_return(self):
        assert strip_ansi("abc\rdef") == "abcdef"


# =============================================================================
# find_keel_messages — goose + bubble extraction
# =============================================================================


class TestFindKeelMessages:
    def test_simple_bubble(self):
        lines = goose_with_bubble(["Hello from the companion!"])
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "Hello from the companion" in msgs[0]

    def test_multiline_bubble(self):
        lines = goose_with_bubble(["Line one of the", "bubble message here"])
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "Line one" in msgs[0]
        assert "bubble message" in msgs[0]

    def test_no_goose_no_message(self):
        lines = ["just some random text", "nothing to see here"]
        assert find_keel_messages(lines) == []

    def test_decorative_only_ignored(self):
        """Bubble lines with only box-drawing chars are skipped."""
        lines = [
            "╭──────────────╮",
            "│ ──────────── │",
            "╰──────────────╯",
            "    (◉>",
            "   _(__)_",
        ]
        msgs = find_keel_messages(lines)
        assert msgs == []

    def test_claimed_lines_prevent_dupes(self):
        """Two goose markers near the same bubble don't produce duplicates."""
        lines = [
            "╭──────────────────╮",
            "│ Same bubble text  │",
            "╰──────────────────╯",
            "    (◉>",
            "   / |",
            "  _(__)_",
        ]
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1

    def test_two_separate_gooses(self):
        """Two goose+bubble combos far apart should both be captured."""
        block1 = goose_with_bubble(["First message"])
        gap = [""] * 20
        block2 = goose_with_bubble(["Second message"])
        lines = block1 + gap + block2
        msgs = find_keel_messages(lines)
        assert len(msgs) == 2
        assert any("First" in m for m in msgs)
        assert any("Second" in m for m in msgs)

    def test_goose_markers_detected(self):
        """Each marker variant triggers detection."""
        for marker in GOOSE_MARKERS:
            lines = [
                "╭──────────────╮",
                "│ test text    │",
                "╰──────────────╯",
                f"   {marker}",
            ]
            msgs = find_keel_messages(lines)
            assert len(msgs) >= 1, f"Marker {marker!r} not detected"

    def test_short_messages_extracted(self):
        """find_keel_messages extracts even short text; _best_message filters."""
        lines = goose_with_bubble(["Hi"])
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "Hi" in msgs[0]


# =============================================================================
# classify — debug vs vibe tagging
# =============================================================================


class TestClassify:
    def test_debug_words(self):
        assert classify("There's a bug in the parser") == "debug"
        assert classify("Check the error logs") == "debug"
        assert classify("This might leak memory") == "debug"
        assert classify("Consider a refactor here") == "debug"
        assert classify("Missing null check") == "debug"

    def test_vibe_messages(self):
        assert classify("Looking good so far!") == "vibe"
        assert classify("Honk honk") == "vibe"
        assert classify("Nice progress today") == "vibe"

    def test_case_insensitive(self):
        assert classify("THERE IS A BUG") == "debug"
        assert classify("WARNING detected") == "debug"

    def test_substring_match(self):
        """DEBUG_WORDS uses 'in' matching — 'deprecat' matches 'deprecated'."""
        assert classify("This API is deprecated") == "debug"
        assert (
            classify("Performance is great") == "debug"
        )  # "performance" in DEBUG_WORDS


# =============================================================================
# Dedup helpers
# =============================================================================


class TestNormalize:
    def test_collapses_whitespace(self):
        assert _normalize("hello   world") == "hello world"

    def test_strips_edges(self):
        assert _normalize("  hello  ") == "hello"

    def test_newlines_collapsed(self):
        assert _normalize("line1\n  line2") == "line1 line2"


class TestIsSameMessage:
    def test_identical(self):
        assert _is_same_message("hello world", "hello world")

    def test_substring(self):
        assert _is_same_message("hello", "hello world")
        assert _is_same_message("hello world", "hello")

    def test_common_prefix(self):
        assert _is_same_message(
            "this is a long message about geese",
            "this is a long message about ducks",
        )

    def test_word_overlap(self):
        assert _is_same_message(
            "the goose is watching the code",
            "the goose was watching some code",
        )

    def test_different_messages(self):
        assert not _is_same_message("hello", "goodbye")
        assert not _is_same_message("abc", "xyz")

    def test_short_different(self):
        """Short strings with no overlap should not match."""
        assert not _is_same_message("hi", "bye")


class TestIsNovel:
    def test_novel_message(self):
        seen = {"hello world", "foo bar"}
        assert _is_novel("something new", seen)

    def test_exact_dupe(self):
        seen = {"hello world"}
        assert not _is_novel("hello world", seen)

    def test_substring_of_seen(self):
        seen = {"hello world is great"}
        assert not _is_novel("hello world", seen)

    def test_seen_is_substring(self):
        seen = {"hello"}
        assert not _is_novel("hello world", seen)

    def test_word_overlap(self):
        seen = {"the goose is watching the code carefully"}
        assert not _is_novel("the goose was watching some code carefully", seen)


class TestBestMessage:
    def test_picks_longest(self):
        msgs = ["short", "this is quite a long message", "medium length"]
        assert _best_message(msgs) == "this is quite a long message"

    def test_filters_short(self):
        """Messages 8 chars or less are excluded."""
        assert _best_message(["hi", "ok", "sure"]) is None

    def test_empty(self):
        assert _best_message([]) is None

    def test_threshold(self):
        assert _best_message(["12345678"]) is None  # exactly 8 chars
        assert _best_message(["123456789"]) == "123456789"  # 9 chars


class TestWordBag:
    def test_basic(self):
        bag = _word_bag("the goose is watching")
        assert "goose" in bag
        assert "watching" in bag

    def test_short_words_excluded(self):
        bag = _word_bag("I am ok")
        assert bag == set()  # all words < 3 alpha chars

    def test_case_insensitive(self):
        bag = _word_bag("Hello World")
        assert "hello" in bag
        assert "world" in bag


# =============================================================================
# File routing — _append_entry and append_capture
# =============================================================================


class TestAppendEntry:
    def test_creates_new_file(self, tmp_path):
        target = tmp_path / "test-captures.md"
        _append_entry(
            target,
            "# TestCompanion Test\n",
            "- entry1",
            "### 2026-04-03",
            "TestCompanion",
        )
        content = target.read_text()
        assert "# TestCompanion Test" in content
        assert "### 2026-04-03" in content
        assert "- entry1" in content

    def test_appends_same_day(self, tmp_path):
        target = tmp_path / "test-captures.md"
        target.write_text("# TestCompanion Test\n### 2026-04-03\n- entry1\n")
        _append_entry(
            target,
            "# TestCompanion Test\n",
            "- entry2",
            "### 2026-04-03",
            "TestCompanion",
        )
        content = target.read_text()
        assert "- entry1" in content
        assert "- entry2" in content
        # Only one date header
        assert content.count("### 2026-04-03") == 1

    def test_appends_new_day(self, tmp_path):
        target = tmp_path / "test-captures.md"
        target.write_text("# TestCompanion Test\n### 2026-04-02\n- old entry\n")
        _append_entry(
            target,
            "# TestCompanion Test\n",
            "- new entry",
            "### 2026-04-03",
            "TestCompanion",
        )
        content = target.read_text()
        assert "### 2026-04-02" in content
        assert "### 2026-04-03" in content
        assert "- new entry" in content

    def test_corruption_recovery(self, tmp_path):
        """Bad header -> salvage valid entries, rebuild."""
        target = tmp_path / "test-captures.md"
        target.write_text("GARBAGE\n- `[vibe]` `12:00` `proj` — saved\nmore junk\n")
        _append_entry(
            target,
            "# TestCompanion Test\n",
            "- new",
            "### 2026-04-03",
            "TestCompanion",
        )
        content = target.read_text()
        assert content.startswith("# TestCompanion Test")
        assert "- `[vibe]` `12:00` `proj` — saved" in content
        assert "- new" in content


class TestAppendCapture:
    _PROJECT = "test-project"

    def test_vibe_routes_to_captures(self, tmp_path, monkeypatch):
        config = _test_config(tmp_path)
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        append_capture("Nice work!", "vibe", config)
        cap = config.captures_file_for_project(self._PROJECT)
        dbg = config.debug_file_for_project(self._PROJECT)
        assert cap.exists()
        content = cap.read_text()
        assert "[vibe]" in content
        assert "Nice work!" in content
        assert not dbg.exists()

    def test_debug_routes_to_debug(self, tmp_path, monkeypatch):
        config = _test_config(tmp_path)
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        append_capture("Found a bug", "debug", config)
        cap = config.captures_file_for_project(self._PROJECT)
        dbg = config.debug_file_for_project(self._PROJECT)
        assert dbg.exists()
        content = dbg.read_text()
        assert "[debug]" in content
        assert "Found a bug" in content
        assert not cap.exists()

    def test_entry_format(self, tmp_path, monkeypatch):
        config = _test_config(tmp_path)
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        append_capture("test message", "vibe", config)
        content = config.captures_file_for_project(self._PROJECT).read_text()
        # Format: <!-- schema:1 id:... ts:... -->\n- `[tag]` `HH:MM` `project` — message
        lines = [ln for ln in content.splitlines() if ln.startswith("- `[")]
        assert len(lines) == 1
        assert "- `[vibe]`" in lines[0]
        assert "test message" in lines[0]
        # Verify schema identity comment exists
        id_lines = [ln for ln in content.splitlines() if ln.startswith("<!-- schema:")]
        assert len(id_lines) == 1


class TestAppendCaptureExclusion:
    """Privacy controls — exclusion at capture time."""

    _PROJECT = "test-project"

    def test_exclude_pattern_blocks_write(self, tmp_path, monkeypatch):
        config = Config(
            companion_name="TestCompanion",
            output_dir=str(tmp_path),
            log_dir=str(tmp_path / "logs"),
            exclude_patterns=[r"secret\b"],
        )
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        append_capture("this is secret info", "vibe", config)
        assert not config.captures_file_for_project(self._PROJECT).exists()

    def test_exclude_pattern_allows_non_matching(self, tmp_path, monkeypatch):
        config = Config(
            companion_name="TestCompanion",
            output_dir=str(tmp_path),
            log_dir=str(tmp_path / "logs"),
            exclude_patterns=[r"password"],
        )
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        append_capture("just a normal message", "vibe", config)
        cap = config.captures_file_for_project(self._PROJECT)
        assert cap.exists()
        assert "normal message" in cap.read_text()

    def test_excluded_project_blocks_write(self, tmp_path, monkeypatch):
        config = Config(
            companion_name="TestCompanion",
            output_dir=str(tmp_path),
            log_dir=str(tmp_path / "logs"),
            excluded_projects=["secret-*"],
        )
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name",
            lambda: "secret-project",
        )
        append_capture("hello world", "vibe", config)
        assert not config.captures_file_for_project("secret-project").exists()

    def test_excluded_project_allows_non_matching(self, tmp_path, monkeypatch):
        config = Config(
            companion_name="TestCompanion",
            output_dir=str(tmp_path),
            log_dir=str(tmp_path / "logs"),
            excluded_projects=["secret-*"],
        )
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name",
            lambda: "public-project",
        )
        append_capture("hello world", "vibe", config)
        assert config.captures_file_for_project("public-project").exists()

    def test_exclusion_blocks_sqlite_too(self, tmp_path, monkeypatch):
        config = Config(
            companion_name="TestCompanion",
            output_dir=str(tmp_path),
            log_dir=str(tmp_path / "logs"),
            exclude_patterns=[r"secret"],
        )
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        from companion_capture.store import CaptureStore

        db_path = tmp_path / "test.db"
        with CaptureStore(db_path) as store:
            append_capture("this is secret", "vibe", config, store=store)
            assert store.recent(limit=10) == []

    def test_exclusion_blocks_debug_too(self, tmp_path, monkeypatch):
        config = Config(
            companion_name="TestCompanion",
            output_dir=str(tmp_path),
            log_dir=str(tmp_path / "logs"),
            exclude_patterns=[r"token"],
        )
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        append_capture("auth token leaked", "debug", config)
        assert not config.debug_file_for_project(self._PROJECT).exists()

    def test_no_exclusion_writes_normally(self, tmp_path, monkeypatch):
        config = Config(
            companion_name="TestCompanion",
            output_dir=str(tmp_path),
            log_dir=str(tmp_path / "logs"),
        )
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        append_capture("hello", "vibe", config)
        assert config.captures_file_for_project(self._PROJECT).exists()


# =============================================================================
# Sweep mode — end-to-end
# =============================================================================


class TestSweep:
    _PROJECT = "test-project"

    def _make_log_with_goose(self, tmp_path, text_lines: list[str]) -> Path:
        """Write a log file containing goose + bubble with CR+LF (like script output)."""
        lines = goose_with_bubble(text_lines)
        log = tmp_path / "session.log"
        log.write_text("\r\n".join(lines) + "\r\n")
        return log

    def test_sweep_captures_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        log = self._make_log_with_goose(tmp_path, ["Honk honk looking good today"])
        config = _test_config(tmp_path / "output")
        sweep(str(log), config)
        cap = config.captures_file_for_project(self._PROJECT)
        assert cap.exists()
        assert "looking good today" in cap.read_text()

    def test_sweep_deduplicates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        log = self._make_log_with_goose(tmp_path, ["Already captured message"])
        config = _test_config(tmp_path / "output")
        cap = config.captures_file_for_project(self._PROJECT)
        cap.parent.mkdir(parents=True, exist_ok=True)
        cap.write_text(
            f"# {config.companion_name} — Auto-Captures\n\n## Log\n### 2026-04-03\n"
            "- `[vibe]` `12:00` `proj` — Already captured message\n"
        )
        sweep(str(log), config)
        content = cap.read_text()
        # Should appear only once
        count = content.count("Already captured message")
        assert count == 1

    def test_sweep_skips_short(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        log = self._make_log_with_goose(tmp_path, ["Hi"])
        config = _test_config(tmp_path / "output")
        sweep(str(log), config)
        cap = config.captures_file_for_project(self._PROJECT)
        # "Hi" is <= 8 chars, should be filtered
        if cap.exists():
            assert "Hi" not in cap.read_text()

    def test_sweep_routes_debug(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        log = self._make_log_with_goose(tmp_path, ["There might be a memory leak here"])
        config = _test_config(tmp_path / "output")
        sweep(str(log), config)
        dbg = config.debug_file_for_project(self._PROJECT)
        assert dbg.exists()
        assert "leak" in dbg.read_text()

    def test_sweep_nonexistent_file(self, tmp_path, monkeypatch):
        """sweep() on missing file does nothing, no crash."""
        monkeypatch.setattr(
            "companion_capture.parser.get_project_name", lambda: self._PROJECT
        )
        config = _test_config(tmp_path / "output")
        sweep(str(tmp_path / "nonexistent.log"), config)
        assert not config.captures_file_for_project(self._PROJECT).exists()


# =============================================================================
# ScreenBuffer + find_keel_messages integration
# =============================================================================


class TestScreenBufferIntegration:
    def test_cursor_positioned_goose(self):
        """Goose drawn via cursor moves should be extractable."""
        sb = ScreenBuffer()
        # Position bubble at row 2
        sb.feed("\x1b[3;1H")
        sb.feed("╭────────────────────────╮\n")
        sb.feed("│ Cursor-drawn message!  │\n")
        sb.feed("╰────────────────────────╯\n")
        # Position goose below
        sb.feed("\x1b[7;1H")
        sb.feed("    (◉>\n")
        sb.feed("   _(__)_\n")

        lines = sb.get_lines()
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "Cursor-drawn message" in msgs[0]

    def test_sgr_doesnt_corrupt(self):
        """Color codes in bubble text shouldn't break extraction."""
        sb = ScreenBuffer()
        bubble = goose_with_bubble(["Colorful message"])
        for line in bubble:
            sb.feed(f"\x1b[1;32m{line}\x1b[0m\n")
        lines = sb.get_lines()
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "Colorful message" in msgs[0]

    def test_grid_reset_no_contamination(self):
        """After reset, old bubble shouldn't contaminate new scan."""
        sb = ScreenBuffer()
        # First bubble
        for line in goose_with_bubble(["First bubble"]):
            sb.feed(line + "\n")
        msgs1 = find_keel_messages(sb.get_lines())
        assert len(msgs1) == 1

        sb.reset()

        # Second bubble
        for line in goose_with_bubble(["Second bubble"]):
            sb.feed(line + "\n")
        msgs2 = find_keel_messages(sb.get_lines())
        assert len(msgs2) == 1
        assert "Second bubble" in msgs2[0]
        assert "First bubble" not in msgs2[0]

    def test_rapid_replacement(self):
        """Simulate two bubbles drawn in quick succession."""
        sb = ScreenBuffer()
        # First bubble drawn
        for line in goose_with_bubble(["Quick bubble one"]):
            sb.feed(line + "\n")
        lines1 = sb.get_lines()
        msg1 = find_keel_messages(lines1)

        # Screen cleared and second bubble drawn
        sb.feed("\x1b[2J\x1b[H")
        for line in goose_with_bubble(["Quick bubble two"]):
            sb.feed(line + "\n")
        lines2 = sb.get_lines()
        msg2 = find_keel_messages(lines2)

        assert len(msg1) == 1 and "Quick bubble one" in msg1[0]
        assert len(msg2) == 1 and "Quick bubble two" in msg2[0]


# =============================================================================
# BUBBLE_LINE_RE edge cases
# =============================================================================


class TestBubbleLineRegex:
    def test_matches_standard(self):
        m = BUBBLE_LINE_RE.search("│ Hello world │")
        assert m and m.group(1).strip() == "Hello world"

    def test_padded(self):
        m = BUBBLE_LINE_RE.search("│   lots of space   │")
        assert m and m.group(1).strip() == "lots of space"

    def test_no_match_without_pipes(self):
        assert BUBBLE_LINE_RE.search("Hello world") is None

    def test_no_match_empty(self):
        m = BUBBLE_LINE_RE.search("│    │")
        # Should match, but content is whitespace-only
        if m:
            assert m.group(1).strip() == ""


# =============================================================================
# Edge cases
# =============================================================================


class TestEdgeCases:
    def test_empty_lines(self):
        assert find_keel_messages([]) == []

    def test_goose_without_bubble(self):
        lines = [
            "    (◉>",
            "   / |",
            "  _(__)_",
        ]
        assert find_keel_messages(lines) == []

    def test_bubble_without_goose(self):
        lines = [
            "╭──────────────────╮",
            "│ orphan bubble    │",
            "╰──────────────────╯",
        ]
        assert find_keel_messages(lines) == []

    def test_get_project_name(self):
        """get_project_name uses PWD env var."""
        with patch.dict(os.environ, {"PWD": "/tmp/test-project"}):
            assert parser_module.get_project_name() == "test-project"

    def test_screenbuffer_oversized_cursor(self):
        """Cursor moved way past grid bounds shouldn't crash."""
        sb = ScreenBuffer()
        sb.feed("\x1b[999;999H")
        sb.feed("X")
        # Should not raise; cursor clamped to bounds
        lines = sb.get_lines()
        # X written past COLS so it won't appear, but no crash
        assert isinstance(lines, list)

    def test_screenbuffer_negative_cursor(self):
        """Moving cursor up/left past 0 shouldn't crash."""
        sb = ScreenBuffer()
        sb.feed("\x1b[999AX")  # move up 999 from row 0
        lines = sb.get_lines()
        assert lines[0].startswith("X")


# =============================================================================
# Real TUI layout — multi-line bubbles with non-contiguous rows
# =============================================================================


class TestRealTUILayout:
    """Tests that mirror how Claude Code's TUI actually draws the goose.

    The TUI uses cursor positioning to draw the bubble and goose on
    non-contiguous even rows (0, 2, 4, 6, 8, 10), with blank rows between.
    The goose markers appear inline with bubble rows, not below them.
    """

    def _draw_real_layout(self, bubble_lines: list[str]) -> list[str]:
        """Simulate the TUI's actual draw pattern via ScreenBuffer.

        Draws a multi-line bubble + goose using cursor positioning to
        even rows with the goose art to the right, matching the real
        TUI layout observed in session logs.
        """
        sb = ScreenBuffer()
        col_bubble = 68  # bubble starts at column 68 (after the status bar)
        col_goose = 102  # goose art starts further right
        width = 32

        # Build bubble frame
        top = "╭" + "─" * width + "╮"
        bot = "╰" + "─" * width + "╯"
        body = [f"│ {line:<{width - 2}} │" for line in bubble_lines]
        frame = [top] + body + [bot]

        # Goose art (drawn to the right of the bubble)
        goose = ["     (◉>", "      ||", "    _(__)_"]

        # Draw on even rows using absolute cursor positioning
        for i, line in enumerate(frame):
            row = i * 2  # even rows: 0, 2, 4, 6, ...
            sb.feed(f"\x1b[{row + 1};{col_bubble + 1}H{line}")

        # Draw goose on rows aligned with bottom of bubble
        goose_start_row = (len(frame) - 3) * 2
        for i, g in enumerate(goose):
            row = goose_start_row + i * 2
            sb.feed(f"\x1b[{row + 1};{col_goose + 1}H{g}")

        return sb.get_lines()

    def test_multiline_bubble_non_contiguous_rows(self):
        """Multi-line bubble drawn on even rows should still be extracted."""
        lines = self._draw_real_layout(
            [
                "*honks approvingly, then",
                "pauses*",
                "",
                "Emulation beats",
                "destruction. But—does VT100",
                "handle your actual escape",
                "sequences?",
            ]
        )
        msgs = find_keel_messages(lines)
        assert len(msgs) >= 1
        joined = " ".join(msgs)
        assert "Emulation beats" in joined or "honks approvingly" in joined

    def test_two_line_bubble_real_layout(self):
        """Simple two-line bubble in real TUI layout."""
        lines = self._draw_real_layout(
            [
                "Fresh canvas. But",
                "where's the actual test?",
            ]
        )
        msgs = find_keel_messages(lines)
        assert len(msgs) >= 1
        joined = " ".join(msgs)
        assert "actual test" in joined or "Fresh canvas" in joined

    def test_single_line_bubble_real_layout(self):
        """Single-line bubble in real TUI layout."""
        lines = self._draw_real_layout(["Honk honk looking good today!"])
        msgs = find_keel_messages(lines)
        assert len(msgs) >= 1
        assert "looking good" in msgs[0]

    def test_goose_markers_on_even_rows(self):
        """Goose markers land on even rows — verify they're found."""
        lines = self._draw_real_layout(["Some message about the code"])
        # Check that goose markers exist somewhere in the output
        has_goose = any(any(m in line for m in GOOSE_MARKERS) for line in lines)
        assert has_goose

    def test_stale_remnant_from_previous_bubble(self):
        """Old bubble remnant on screen shouldn't corrupt new extraction.

        Real scenario: previous bubble's last line stays on row 0 because
        the TUI didn't clear it. New bubble drawn on rows 2-10.
        """
        sb = ScreenBuffer()
        # Old remnant at row 0
        sb.feed("\x1b[1;68H│ leftover from before       │")
        # New bubble drawn starting at row 2
        col = 68
        sb.feed(f"\x1b[3;{col + 1}H╭────────────────────────────────╮")
        sb.feed(f"\x1b[5;{col + 1}H│ The real new message here    │")
        sb.feed(f"\x1b[7;{col + 1}H╰────────────────────────────────╯")
        # Goose
        sb.feed(f"\x1b[5;{col + 36}H     (◉>")
        sb.feed(f"\x1b[7;{col + 36}H    _(__)_")

        lines = sb.get_lines()
        msgs = find_keel_messages(lines)
        # Should extract the new message, and ideally not include "leftover"
        assert len(msgs) >= 1
        assert any("real new message" in m for m in msgs)

    def test_bubble_line_without_closing_pipe(self):
        """Overflow line (opening | but no closing |) is recovered.

        BUBBLE_OVERFLOW_RE catches lines adjacent to confirmed bubble rows.
        """
        lines = [
            "╭────────────────────────────────╮",
            "│ Line one is complete           │",
            "│ Line two gets truncated here and overflows past the frame",  # no closing │
            "╰────────────────────────────────╯",
            "    (◉>",
            "   _(__)_",
        ]
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "Line one" in msgs[0]
        assert "truncated" in msgs[0]

    def test_wide_search_window_for_sparse_rows(self):
        """Bubble lines spread across 16+ rows need the +/-20 window to reach.

        With +/-20 window from goose at row 18/19, all rows 0-19 are
        reachable. All 7 text lines should be captured.
        """
        # Build a tall bubble: 7 text lines on even rows = rows 0-14
        lines = [""] * 20
        lines[0] = "╭────────────────────────────────╮"
        lines[2] = "│ Line one of seven              │"
        lines[4] = "│ Line two of seven              │"
        lines[6] = "│ Line three of seven            │"
        lines[8] = "│ Line four of seven             │"
        lines[10] = "│ Line five of seven             │"
        lines[12] = "│ Line six of seven              │"
        lines[14] = "│ Line seven of seven            │"
        lines[16] = "╰────────────────────────────────╯"
        lines[18] = "    (◉>"
        lines[19] = "   _(__)_"

        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        joined = msgs[0]
        assert "Line one" in joined
        assert "Line seven" in joined


# =============================================================================
# Text overflow — content wider than the bubble frame
# =============================================================================


class TestBubbleOverflow:
    """Tests for when bubble text overflows past the closing |."""

    def test_overflow_line_recovered(self):
        """Single overflow line adjacent to a confirmed bubble line."""
        lines = [
            "╭──────────────────────────────╮",
            "│ Normal line fits the frame   │",
            "│ This line overflows the bubble frame and has no closing pipe",
            "╰──────────────────────────────╯",
            "    (◉>",
            "   _(__)_",
        ]
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "Normal line" in msgs[0]
        assert "overflows the bubble" in msgs[0]

    def test_all_overflow_no_confirmed_lines(self):
        """If ALL lines overflow (no confirmed |...|), nothing is recovered.

        This prevents false positives -- overflow recovery requires at
        least one confirmed bubble line as an anchor.
        """
        lines = [
            "╭──────────────────────────────╮",
            "│ This overflows and has no closing pipe on any line whatsoever",
            "│ Neither does this one which also overflows the frame boundary",
            "╰──────────────────────────────╯",
            "    (◉>",
            "   _(__)_",
        ]
        msgs = find_keel_messages(lines)
        # No confirmed │...│ lines -> no anchor -> no overflow recovery
        assert msgs == []

    def test_overflow_not_adjacent_ignored(self):
        """Overflow line far from any confirmed bubble row is ignored."""
        lines = [
            "│ Confirmed bubble line       │",
            "",
            "",
            "",
            "│ This overflow is too far from the confirmed line to be recovered",
            "    (◉>",
            "   _(__)_",
        ]
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "Confirmed bubble" in msgs[0]
        assert "too far" not in msgs[0]

    def test_overflow_via_screenbuffer(self):
        """Overflow caused by cursor-positioned text exceeding frame width."""
        sb = ScreenBuffer()
        col = 68
        width = 32
        # Frame
        sb.feed(f"\x1b[1;{col + 1}H╭{'─' * width}╮")
        # Normal line
        sb.feed(f"\x1b[2;{col + 1}H│ Short line fits here         │")
        # Overflow line — text written past the closing │ position
        sb.feed(
            f"\x1b[3;{col + 1}H│ This message is significantly wider than the allocated frame"
        )
        # Frame bottom
        sb.feed(f"\x1b[4;{col + 1}H╰{'─' * width}╯")
        # Goose on separate rows (real TUI doesn't overlap goose with text)
        sb.feed(f"\x1b[5;{col + 36}H     (◉>")
        sb.feed(f"\x1b[6;{col + 36}H    _(__)_")

        screen_lines = sb.get_lines()
        msgs = find_keel_messages(screen_lines)
        assert len(msgs) >= 1
        joined = " ".join(msgs)
        assert "Short line" in joined
        assert "significantly wider" in joined

    def test_truncated_by_closing_pipe(self):
        """TUI places closing | at frame edge, truncating the text.

        This is the "soft" overflow -- text is cut short but both pipes
        exist. BUBBLE_LINE_RE catches it, but content is truncated.
        """
        lines = [
            "╭──────────────────────────────╮",
            "│ This message is significantl│",  # closing │ cuts the word
            "╰──────────────────────────────╯",
            "    (◉>",
            "   _(__)_",
        ]
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        # Truncated but captured — this is expected behavior
        assert "significantl" in msgs[0]

    def test_overflow_short_text_ignored(self):
        """Overflow lines under 10 chars are not recovered (noise filter)."""
        lines = [
            "│ Good confirmed line here    │",
            "│ short",  # too short for BUBBLE_OVERFLOW_RE
            "    (◉>",
            "   _(__)_",
        ]
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "Good confirmed" in msgs[0]
        assert "short" not in msgs[0]


# =============================================================================
# Self-capture prevention — min_col filtering
# =============================================================================


class TestMinColFiltering:
    """Tests for min_col parameter that prevents self-capture.

    The live parser and sweep pass min_col=40 so goose markers at the
    left edge of the screen (pytest output, tool output) are ignored.
    Real companion bubbles are drawn at column 68+ by the TUI.
    """

    def test_left_side_goose_ignored_with_min_col(self):
        """Goose at column 0-4 is ignored when min_col=40."""
        lines = [
            "╭──────────────────────────────╮",
            "│ Test output looks like goose │",
            "╰──────────────────────────────╯",
            "    (◉>",
            "   _(__)_",
        ]
        # Default min_col=0: finds it
        assert len(find_keel_messages(lines)) == 1
        # min_col=40: ignores it
        assert find_keel_messages(lines, min_col=40) == []

    def test_right_side_goose_found_with_min_col(self):
        """Goose at column 68+ passes min_col=40 filter."""
        sb = ScreenBuffer()
        col = 68
        sb.feed(f"\x1b[1;{col + 1}H╭────────────────────────────────╮")
        sb.feed(f"\x1b[2;{col + 1}H│ Real companion message here  │")
        sb.feed(f"\x1b[3;{col + 1}H╰────────────────────────────────╯")
        sb.feed(f"\x1b[2;{col + 36}H     (◉>")
        sb.feed(f"\x1b[4;{col + 36}H    _(__)_")

        screen_lines = sb.get_lines()
        msgs = find_keel_messages(screen_lines, min_col=40)
        assert len(msgs) >= 1
        assert "Real companion message" in msgs[0]

    def test_mixed_left_and_right_goose(self):
        """Left-side goose ignored, right-side goose captured."""
        lines = [""] * 50
        # Left-side test output (should be ignored with min_col=40)
        lines[0] = "│ pytest output here │"
        lines[1] = "    (◉>"
        lines[2] = "   _(__)_"
        # Right-side real bubble far enough away to avoid window overlap
        lines[40] = " " * 68 + "│ Actual companion bubble here │"
        lines[41] = " " * 100 + "(◉>"
        lines[42] = " " * 98 + "_(__)_"

        msgs = find_keel_messages(lines, min_col=40)
        assert len(msgs) == 1
        assert "Actual companion" in msgs[0]
        assert "pytest" not in msgs[0]

    def test_min_col_zero_is_default(self):
        """Default behavior (min_col=0) matches everything."""
        lines = [
            "│ message │",
            "(◉>",
            "_(__)_",
        ]
        assert len(find_keel_messages(lines)) >= 1

    def test_pytest_output_simulation(self):
        """Simulate what pytest renders when running goose-related tests."""
        sb = ScreenBuffer()
        # pytest prints test names and assertions at the left edge
        sb.feed("test_parser.py::TestFindKeelMessages::test_simple_bubble PASSED\r\n")
        sb.feed("  lines = goose_with_bubble(['Hello from the companion!'])\r\n")
        sb.feed("╭────────────────────────────────╮\r\n")
        sb.feed("│ Hello from the companion!      │\r\n")
        sb.feed("╰────────────────────────────────╯\r\n")
        sb.feed("     ___\r\n")
        sb.feed("    (◉>\r\n")
        sb.feed("   _(__)_\r\n")

        screen_lines = sb.get_lines()
        # min_col=40 should reject this — it's all at column 0
        msgs = find_keel_messages(screen_lines, min_col=40)
        assert msgs == []

    def test_dual_marker_same_line_rfind(self):
        """Goose marker at left AND right of same line — rfind picks the right one."""
        lines = [""] * 5
        # Left-side marker at col 4, right-side marker at col 72
        lines[0] = " " * 68 + "│ Real bubble here │"
        lines[1] = "    (◉>" + " " * 61 + "    (◉>"
        lines[2] = "   _(__)_" + " " * 57 + "   _(__)_"

        # min_col=40 should find the right-side marker via rfind
        msgs = find_keel_messages(lines, min_col=40)
        assert len(msgs) == 1
        assert "Real bubble" in msgs[0]

    def test_boundary_column(self):
        """Goose at exactly min_col is accepted; above is rejected.

        Note: '(corner>' has 'corner>' at col+1, so min_col checks the substring
        position, not the leading '('.  Use _(__)_ as the sole marker
        for a clean boundary test.
        """
        lines = [""] * 5
        lines[0] = " " * 40 + "│ boundary message │"
        lines[1] = " " * 40 + "_(__)_"

        # _(__)_ starts at col 40 -> passes min_col=40
        assert len(find_keel_messages(lines, min_col=40)) >= 1
        # _(__)_ at col 40 -> fails min_col=41
        assert find_keel_messages(lines, min_col=41) == []


# =============================================================================
# Window boundary edge cases — goose at index 0, last, tiny list
# =============================================================================


class TestWindowBoundaries:
    """Tests for the search window when goose is at extreme positions."""

    def test_goose_at_index_zero(self):
        """Goose at line 0 — window start must not go negative."""
        lines = [
            "    (◉>",
            "   _(__)_",
            "╭──────────────────────────────╮",
            "│ Bubble below the goose       │",
            "╰──────────────────────────────╯",
        ]
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "Bubble below" in msgs[0]

    def test_goose_at_last_index(self):
        """Goose at last line — window end clamped to len(lines)."""
        lines = [
            "╭──────────────────────────────╮",
            "│ Bubble above the goose       │",
            "╰──────────────────────────────╯",
            "    (◉>",
            "   _(__)_",
        ]
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "Bubble above" in msgs[0]

    def test_two_line_list(self):
        """Minimal list — goose + bubble on only 2 lines."""
        lines = [
            "│ tiny list message here │",
            "_(__)_",
        ]
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "tiny list" in msgs[0]

    def test_goose_at_index_one(self):
        """Goose at index 1 — window [-7, 9) clamped to [0, 9)."""
        lines = [
            "│ message just above goose     │",
            "    (◉>",
            "   _(__)_",
        ]
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "just above" in msgs[0]

    def test_single_line_goose_and_bubble(self):
        """Goose marker and bubble on the same line."""
        lines = [
            "│ inline message │     (◉>    _(__)_",
        ]
        msgs = find_keel_messages(lines)
        assert len(msgs) == 1
        assert "inline message" in msgs[0]


# =============================================================================
# Standalone config — main() fallback
# =============================================================================


class TestMainStandalone:
    """Test that main() falls back to Config.load() when no config passed."""

    def test_standalone_config_load(self, tmp_path, monkeypatch):
        """main(config=None) calls Config.load() -- standalone operation."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text('{"companion_name": "StandaloneGoose"}')

        monkeypatch.setattr("companion_capture.config.CONFIG_FILE", config_file)
        monkeypatch.setattr(
            "sys.argv", ["parser.py", str(tmp_path / "fake.log"), "--sweep"]
        )

        # sweep on nonexistent file is a no-op, but main() should not crash
        from companion_capture.parser import main

        main()  # Should use Config.load() internally
