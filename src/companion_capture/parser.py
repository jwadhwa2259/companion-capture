#!/usr/bin/env python3
"""
parser.py — Real-time companion speech bubble extractor

Watches a terminal log file (from `script`) for the companion goose's
speech bubble messages and appends them to the configured captures file.

Modes:
  Live:  python3 -m companion_capture.parser <logfile>          (streams via tail -f)
  Sweep: python3 -m companion_capture.parser <logfile> --sweep   (single pass, then exit)
"""

from __future__ import annotations

import fcntl  # noqa: F401 — non-blocking fd control (Unix only, fine for macOS)
import os
import re
import signal  # noqa: F401 — used in watch_live() SIGTERM handler
import subprocess  # noqa: F401 — used in watch_live() for tail -f
import sys
import time
from datetime import datetime
from pathlib import Path

from companion_capture.config import SCHEMA_VERSION, Config  # noqa: F401
from companion_capture.store import CaptureStore  # noqa: F401

# --- ANSI stripping (used by sweep mode) -------------------------------------

ANSI_RE = re.compile(
    r"\x1b"
    r"(?:"
    r"\[[0-9;:?]*[A-Za-z]"
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|[()][AB012]"
    r"|[78=>]"
    r"|\[[\?]?[0-9;]*[hl]"
    r")"
    r"|\r"
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and carriage returns."""
    return ANSI_RE.sub("", text)


# --- Virtual screen buffer ----------------------------------------------------


class ScreenBuffer:
    """Minimal VT100 emulator that tracks cursor position.

    The TUI draws the goose+bubble using cursor-movement sequences
    (CSI nA/B/C/D, CSI n;mH, etc.).  Plain ANSI stripping loses that
    positional info, producing garbled text.  This class maintains a 2-D
    character grid so every character lands at the right (row, col),
    and get_lines() returns text in correct reading order.
    """

    ROWS = 60
    COLS = 220

    def __init__(self):
        self.grid = [[" "] * self.COLS for _ in range(self.ROWS)]
        self.row = 0
        self.col = 0
        self.saved = (0, 0)
        self._pending = ""  # incomplete escape sequence carried across feed() calls

    # --- public API ---

    def feed(self, data: str) -> None:
        """Process a chunk of decoded terminal output.

        Handles escape sequences split across chunk boundaries by
        buffering incomplete sequences in self._pending.
        """
        data = self._pending + data
        self._pending = ""
        i = 0
        n = len(data)
        while i < n:
            c = data[i]
            if c == "\x1b":
                consumed = self._esc(data, i + 1, n)
                if consumed == -1:
                    # Incomplete escape — save remainder for next feed()
                    self._pending = data[i:]
                    return
                i = consumed
            elif c == "\n":
                self._lf()
                i += 1
            elif c == "\r":
                self.col = 0
                i += 1
            elif c == "\x08":  # backspace
                self.col = max(0, self.col - 1)
                i += 1
            elif ord(c) < 32:  # other control chars (BEL, etc.)
                i += 1
            else:
                self._put(c)
                i += 1

    def get_lines(self) -> list[str]:
        """Return all screen rows as strings (trailing spaces stripped)."""
        return ["".join(row).rstrip() for row in self.grid]

    def reset(self) -> None:
        """Clear grid and cursor after a confirmed capture.

        Prevents old bubble remnants from contaminating the next scan.
        Preserves _pending so in-flight escape sequences survive.
        """
        self.grid = [[" "] * self.COLS for _ in range(self.ROWS)]
        self.row = 0
        self.col = 0
        self.saved = (0, 0)

    # --- character output ---

    def _put(self, c: str) -> None:
        if 0 <= self.row < self.ROWS and 0 <= self.col < self.COLS:
            self.grid[self.row][self.col] = c
        self.col += 1

    def _lf(self) -> None:
        self.row += 1
        if self.row >= self.ROWS:
            self.grid.pop(0)
            self.grid.append([" "] * self.COLS)
            self.row = self.ROWS - 1

    # --- escape sequence dispatch ---

    def _esc(self, data: str, i: int, n: int) -> int:
        """Parse after ESC. Returns new index, or -1 if truncated."""
        if i >= n:
            return -1  # ESC at end of chunk
        c = data[i]
        if c == "[":
            return self._csi(data, i + 1, n)
        if c == "]":
            return self._osc(data, i + 1, n)
        if c == "7":
            self.saved = (self.row, self.col)
            return i + 1
        if c == "8":
            self.row, self.col = self.saved
            return i + 1
        if c in "()":
            if i + 1 >= n:
                return -1  # ESC ( at end of chunk
            return i + 2
        return i + 1

    def _osc(self, data: str, i: int, n: int) -> int:
        """Skip OSC sequence (terminated by BEL or ST). Returns -1 if truncated."""
        while i < n:
            if data[i] == "\x07":
                return i + 1
            if data[i] == "\x1b" and i + 1 < n and data[i + 1] == "\\":
                return i + 2
            i += 1
        return -1  # OSC unterminated at end of chunk

    def _csi(self, data: str, i: int, n: int) -> int:
        """Parse CSI sequence: ESC [ params command. Returns -1 if truncated."""
        start = i
        # Private prefix
        if i < n and data[i] in "?>=!":
            i += 1
        # Parameter bytes
        while i < n and data[i] in "0123456789;:":
            i += 1
        if i >= n:
            return -1  # CSI params truncated at end of chunk
        cmd = data[i]
        param_str = data[start:i]
        i += 1

        # Private modes (DECSET/DECRST) — ignore
        if param_str and param_str[0] in "?>=!":
            return i

        # Parse numeric params (colons treated as semicolons for 38:2:... SGR)
        parts = param_str.replace(":", ";").split(";") if param_str else []
        nums: list[int] = []
        for p in parts:
            try:
                nums.append(int(p) if p else 0)
            except ValueError:
                nums.append(0)

        p1 = nums[0] if nums else 0

        if cmd == "A":  # Cursor up
            self.row = max(0, self.row - max(1, p1))
        elif cmd == "B":  # Cursor down
            self.row = min(self.ROWS - 1, self.row + max(1, p1))
        elif cmd == "C":  # Cursor right
            self.col += max(1, p1)
        elif cmd == "D":  # Cursor left
            self.col = max(0, self.col - max(1, p1))
        elif cmd == "G":  # Cursor to column (1-based)
            self.col = max(0, max(1, p1) - 1)
        elif cmd in ("H", "f"):  # Cursor position (row;col, 1-based)
            r = nums[0] if len(nums) > 0 and nums[0] else 1
            c = nums[1] if len(nums) > 1 and nums[1] else 1
            self.row = max(0, min(self.ROWS - 1, r - 1))
            self.col = max(0, c - 1)
        elif cmd == "J":  # Erase display
            self._erase_display(p1)
        elif cmd == "K":  # Erase line
            self._erase_line(p1)
        elif cmd == "s":  # Save cursor
            self.saved = (self.row, self.col)
        elif cmd == "u":  # Restore cursor
            self.row, self.col = self.saved
        # m (SGR), r (scroll region), h/l (mode), etc. — ignore

        return i

    def _erase_display(self, mode: int) -> None:
        if mode == 0:  # Below cursor
            if 0 <= self.row < self.ROWS:
                for c in range(self.col, self.COLS):
                    self.grid[self.row][c] = " "
            for r in range(self.row + 1, self.ROWS):
                self.grid[r] = [" "] * self.COLS
        elif mode == 1:  # Above cursor
            for r in range(0, self.row):
                self.grid[r] = [" "] * self.COLS
            if 0 <= self.row < self.ROWS:
                for c in range(0, self.col + 1):
                    self.grid[self.row][c] = " "
        elif mode >= 2:  # Entire screen
            self.grid = [[" "] * self.COLS for _ in range(self.ROWS)]

    def _erase_line(self, mode: int) -> None:
        if not (0 <= self.row < self.ROWS):
            return
        if mode == 0:  # Right of cursor
            for c in range(self.col, self.COLS):
                self.grid[self.row][c] = " "
        elif mode == 1:  # Left of cursor
            for c in range(0, self.col + 1):
                self.grid[self.row][c] = " "
        elif mode == 2:  # Entire line
            self.grid[self.row] = [" "] * self.COLS


# --- Companion detection ------------------------------------------------------

GOOSE_MARKERS = ["◉>", "(◉", "_(__)_"]
BUBBLE_LINE_RE = re.compile(r"│\s*(.+?)\s*│")
BUBBLE_OVERFLOW_RE = re.compile(
    r"│\s+(.{10,})"
)  # opening │ but no closing — text overflow
DECORATIVE = set("─═┌┐└┘┬┴├┤┼│ ╭╮╰╯")

# --- Classification -----------------------------------------------------------

DEBUG_WORDS = frozenset(
    {
        "anti-pattern",
        "bounds",
        "break",
        "brittle",
        "buffer",
        "bug",
        "careful",
        "check",
        "circular",
        "clean",
        "complexity",
        "conflict",
        "consider",
        "coupling",
        "crash",
        "deadlock",
        "debug",
        "dependency",
        "deprecat",
        "duplicate",
        "edge case",
        "error",
        "escape",
        "extract",
        "fail",
        "fix",
        "flaky",
        "flush",
        "hard-coded",
        "hardcode",
        "import",
        "inject",
        "issue",
        "lag",
        "leak",
        "logic",
        "maze",
        "merge",
        "missing",
        "nested",
        "null",
        "off-by-one",
        "overflow",
        "overwrite",
        "path",
        "performance",
        "polling",
        "race",
        "redundant",
        "refactor",
        "rename",
        "rethink",
        "retry",
        "revert",
        "risk",
        "sanitize",
        "simplify",
        "slow",
        "smell",
        "split",
        "structure",
        "syntax",
        "test",
        "tight",
        "timeout",
        "trace",
        "type",
        "undefined",
        "unused",
        "warning",
        "wrong",
    }
)


def classify(text: str) -> str:
    """Tag message as [debug] or [vibe]."""
    lower = text.lower()
    return "debug" if any(w in lower for w in DEBUG_WORDS) else "vibe"


# --- Extraction ---------------------------------------------------------------


def find_keel_messages(lines: list[str], min_col: int = 0) -> list[str]:
    """Scan lines for goose ASCII art near a speech bubble.

    Returns extracted message strings.  Tracks claimed line indices
    so overlapping goose markers don't produce duplicates.

    Args:
        min_col: Minimum column where a goose marker must appear.
            Live/sweep modes pass min_col=40 so test output rendered
            at the left edge of the screen is ignored.
    """
    messages: list[str] = []
    claimed: set[int] = set()

    goose_indices = []
    for i, line in enumerate(lines):
        for m in GOOSE_MARKERS:
            pos = line.rfind(m)
            if pos >= min_col:
                goose_indices.append(i)
                break
    if not goose_indices:
        return messages

    for gi in goose_indices:
        if gi in claimed:
            continue
        start = max(0, gi - 20)
        end = min(len(lines), gi + 20)

        parts: list[str] = []
        bubble_rows: set[int] = set()  # rows with confirmed │...│ matches
        for j in range(start, end):
            if j in claimed:
                continue
            match = BUBBLE_LINE_RE.search(lines[j])
            if match:
                text = match.group(1).strip()
                if text and not all(c in DECORATIVE for c in text):
                    parts.append(text)
                    bubble_rows.add(j)

        # Second pass: pick up overflow lines (opening │ but no closing │)
        # Only match lines adjacent to confirmed bubble rows to avoid
        # false positives from random │ in tool output.
        for j in range(start, end):
            if j in claimed or j in bubble_rows:
                continue
            if not any(abs(j - br) <= 2 for br in bubble_rows):
                continue
            overflow = BUBBLE_OVERFLOW_RE.search(lines[j])
            if overflow:
                text = overflow.group(1).strip()
                if text and not all(c in DECORATIVE for c in text):
                    parts.append(text)

        if parts:
            messages.append(" ".join(parts))
            for j in range(start, end):
                claimed.add(j)

    return messages


# --- Capture file writer ------------------------------------------------------


def get_project_name() -> str:
    """Get the current working directory's project name."""
    cwd = os.environ.get("PWD", os.getcwd())
    return Path(cwd).name


def _append_entry(
    target: Path, header: str, entry: str, today_header: str, companion_name: str
) -> None:
    """Append an entry to a capture markdown file, creating/repairing as needed."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)

        if not target.exists():
            target.write_text(f"{header}\n{today_header}\n{entry}\n")
            return

        content = target.read_text()

        if f"# {companion_name}" not in content.split("\n")[0]:
            salvaged = [ln for ln in content.splitlines() if ln.startswith("- `[")]
            rebuilt = header
            if salvaged:
                rebuilt += "\n" + "\n".join(salvaged) + "\n"
            target.write_text(rebuilt)
            content = rebuilt

        if today_header in content:
            with open(target, "a") as f:
                f.write(entry + "\n")
        else:
            with open(target, "a") as f:
                f.write(f"\n{today_header}\n{entry}\n")

    except OSError:
        pass


def append_capture(
    message: str,
    tag: str,
    config: Config,
    store: CaptureStore | None = None,
    session_id: str = "",
) -> None:
    """Route entry to captures (vibe) or debug file by tag, then SQLite."""
    project = get_project_name()
    if config.should_exclude(message, project):
        return

    now = datetime.now()
    time_str = now.strftime("%H:%M")
    today_header = f"### {now.strftime('%Y-%m-%d')}"
    entry_id = config.generate_entry_id()
    entry = f"{entry_id}\n- `[{tag}]` `{time_str}` `{project}` — {message}"

    # Markdown write (primary)
    if tag == "debug":
        _append_entry(
            config.debug_file,
            f"# {config.companion_name} — Debug Observations\n\n## Log\n",
            entry,
            today_header,
            config.companion_name,
        )
    else:
        _append_entry(
            config.captures_file,
            f"# {config.companion_name} — Auto-Captures\n\n## Log\n",
            entry,
            today_header,
            config.companion_name,
        )

    # SQLite dual-write (additive — failure is silent)
    if store is not None:
        # Extract UUID and timestamp from the entry_id HTML comment
        id_match = re.search(r"id:([0-9a-f-]+)", entry_id)
        ts_match = re.search(r"ts:(\S+)", entry_id)
        store.insert(
            id=id_match.group(1) if id_match else entry_id,
            timestamp=ts_match.group(1) if ts_match else now.isoformat(),
            project=project,
            session_id=session_id,
            raw_text=message,
            classification=tag,
            schema_version=SCHEMA_VERSION,
        )


# --- Dedup helpers ------------------------------------------------------------


def _normalize(msg: str) -> str:
    """Collapse whitespace for fuzzy comparison."""
    return re.sub(r"\s+", " ", msg).strip()


def _word_bag(msg: str) -> set[str]:
    """Extract words (3+ alpha chars) for overlap comparison."""
    return {w.lower() for w in re.findall(r"[a-zA-Z]{3,}", msg)}


def _is_same_message(a_norm: str, b_norm: str) -> bool:
    """Check if two normalized messages are the same bubble at different stages."""
    if a_norm == b_norm:
        return True
    if a_norm in b_norm or b_norm in a_norm:
        return True
    min_len = min(len(a_norm), len(b_norm))
    if min_len >= 15:
        common = sum(1 for _ in itertools_takewhile_match(a_norm, b_norm))
        if common >= 15:
            return True
    wa, wb = _word_bag(a_norm), _word_bag(b_norm)
    if wa and wb and len(wa) >= 3 and len(wb) >= 3:
        if len(wa & wb) / max(len(wa), len(wb)) > 0.5:
            return True
    return False


def itertools_takewhile_match(a: str, b: str):
    """Yield matching prefix characters."""
    for ca, cb in zip(a, b):
        if ca == cb:
            yield ca
        else:
            break


def _is_novel(n: str, seen_norm: set[str]) -> bool:
    """True if normalized message n isn't already covered by seen entries."""
    if n in seen_norm:
        return False
    if any(n in s for s in seen_norm):
        return False
    if any(s in n for s in seen_norm):
        return False
    wn = _word_bag(n)
    if wn and len(wn) >= 3:
        for s in seen_norm:
            ws = _word_bag(s)
            if ws and len(ws) >= 3:
                if len(wn & ws) / max(len(wn), len(ws)) > 0.5:
                    return False
    return True


def _best_message(msgs: list[str]) -> str | None:
    """Pick the longest message over the minimum length threshold."""
    candidates = [m for m in msgs if len(m) > 8]
    return max(candidates, key=len) if candidates else None


# --- Main loops ---------------------------------------------------------------


def watch_live(
    log_path: str, config: Config, store: CaptureStore | None = None
) -> None:
    """Stream the log via tail -f, using a virtual screen buffer.

    Architecture:
      1. Non-blocking reads feed raw terminal data into a ScreenBuffer
         that tracks cursor position — text always lands at the right
         (row, col), eliminating garbled extractions.
      2. Every SCAN_INTERVAL seconds, read the screen grid and look
         for the goose + speech bubble.
      3. Two-scan confirmation: a message must appear on two consecutive
         scans before it's written, so partial streaming renders are
         never captured.
    """
    SCAN_INTERVAL = 5.0

    log_dir = Path(config.log_dir)

    # Session-scoped heartbeat so multi-session crashes aren't masked
    session_id = Path(log_path).stem.replace("session_", "")
    heartbeat = log_dir / f".parser-heartbeat-{session_id}"

    while not os.path.exists(log_path):
        time.sleep(0.2)

    screen = ScreenBuffer()
    seen_norm: set[str] = set()
    prev_scan_norm: str | None = None
    prev_scan_msg: str | None = None
    empty_scans = 0  # consecutive scans with no message (grace period)

    try:
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.touch()
    except OSError:
        pass

    tail = subprocess.Popen(
        ["tail", "-f", log_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    # Non-blocking reads so we control the scan cadence
    fd = tail.stdout.fileno()
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    force_scan = False
    shutdown = False

    def _handle_term(*_):
        nonlocal shutdown
        shutdown = True

    def _handle_usr1(*_):
        nonlocal force_scan
        force_scan = True

    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGUSR1, _handle_usr1)

    last_scan = time.time()
    byte_carry = b""

    try:
        while not shutdown:
            # Drain available data into the screen buffer
            try:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                # Carry incomplete UTF-8 bytes across reads so multi-byte
                # chars like │ and ◉ aren't destroyed at chunk boundaries
                chunk = byte_carry + chunk
                try:
                    text = chunk.decode("utf-8")
                    byte_carry = b""
                except UnicodeDecodeError:
                    # Find the last valid decode boundary
                    for trim in range(1, 4):
                        try:
                            text = chunk[:-trim].decode("utf-8")
                            byte_carry = chunk[-trim:]
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        text = chunk.decode("utf-8", errors="replace")
                        byte_carry = b""
                screen.feed(text)
            except BlockingIOError:
                pass

            now = time.time()
            forced = force_scan
            if forced:
                force_scan = False
            elif now - last_scan < SCAN_INTERVAL:
                time.sleep(0.1)
                continue

            last_scan = now

            # Scan the virtual screen for goose + bubble
            lines = screen.get_lines()
            msgs = find_keel_messages(lines, min_col=40)
            best = _best_message(msgs)

            if best:
                n = _normalize(best)
                empty_scans = 0
                if forced:
                    # SIGUSR1 — write immediately, skip two-scan wait
                    if _is_novel(n, seen_norm):
                        seen_norm.add(n)
                        append_capture(best, classify(best), config, store, session_id)
                    prev_scan_norm = None
                    prev_scan_msg = None
                    screen.reset()
                elif prev_scan_norm and _is_same_message(n, prev_scan_norm):
                    # Stable across two scans — write the longer version
                    if len(n) >= len(prev_scan_norm):
                        to_write, tn = best, n
                    else:
                        to_write, tn = prev_scan_msg, prev_scan_norm
                    if _is_novel(tn, seen_norm):
                        seen_norm.add(tn)
                        append_capture(
                            to_write, classify(to_write), config, store, session_id
                        )
                    prev_scan_norm = None
                    prev_scan_msg = None
                    screen.reset()
                else:
                    # Different message replaced the pending one.
                    # _is_same_message was False, so old pending isn't a
                    # partial render of the new bubble — flush it.
                    if prev_scan_norm and _is_novel(prev_scan_norm, seen_norm):
                        seen_norm.add(prev_scan_norm)
                        append_capture(
                            prev_scan_msg,
                            classify(prev_scan_msg),
                            config,
                            store,
                            session_id,
                        )
                        screen.reset()
                    prev_scan_norm = n
                    prev_scan_msg = best
            else:
                # No message on screen. If we have an unconfirmed pending
                # message, the goose scrolled away. Give one grace scan,
                # then flush — a message that appeared and disappeared is
                # more likely complete than garbled.
                if prev_scan_norm:
                    empty_scans += 1
                    if empty_scans >= 2:
                        if _is_novel(prev_scan_norm, seen_norm):
                            seen_norm.add(prev_scan_norm)
                            append_capture(
                                prev_scan_msg,
                                classify(prev_scan_msg),
                                config,
                                store,
                                session_id,
                            )
                        prev_scan_norm = None
                        prev_scan_msg = None
                        empty_scans = 0
                        screen.reset()
                else:
                    empty_scans = 0

            # Heartbeat
            try:
                heartbeat.touch()
            except OSError:
                pass

    except KeyboardInterrupt:
        pass
    finally:
        tail.terminate()
        tail.wait()


def sweep(log_path: str, config: Config, store: CaptureStore | None = None) -> None:
    """Single pass over the entire log file — catches anything missed live.

    Uses ScreenBuffer (same as live mode) so sweep output matches live
    quality. The old strip_ansi() approach produced garbled text that
    bypassed dedup and polluted capture files.
    """
    try:
        with open(log_path, "r", errors="replace") as f:
            raw = f.read()
    except OSError:
        return

    existing_norm: set[str] = set()
    for target in (config.captures_file, config.debug_file):
        try:
            for line in target.read_text().splitlines():
                if line.startswith("- `["):
                    sep = line.find(" — ")
                    if sep != -1:
                        existing_norm.add(_normalize(line[sep + 3 :]))
        except OSError:
            pass

    screen = ScreenBuffer()
    screen.feed(raw)
    lines = screen.get_lines()

    for msg in find_keel_messages(lines, min_col=40):
        if len(msg) <= 8:
            continue
        n = _normalize(msg)
        if _is_novel(n, existing_norm):
            tag = classify(msg)
            append_capture(msg, tag, config, store)
            existing_norm.add(n)


# --- Entry point --------------------------------------------------------------


def main(config: Config | None = None) -> None:
    """CLI entry point for the parser."""
    if config is None:
        config = Config.load()

    config.ensure_dirs()

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <log_file> [--sweep]")
        sys.exit(1)

    log_file = sys.argv[1]

    store = CaptureStore(config.db_path, debug=config.debug)
    try:
        store.open()
    except Exception:
        store = None

    try:
        if "--sweep" in sys.argv:
            sweep(log_file, config, store)
        else:
            watch_live(log_file, config, store)
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    main()
