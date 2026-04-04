"""companion-capture CLI."""

import json
import os
import re  # noqa: used by _SINCE_RE at module level
import shutil  # noqa
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import argparse

from companion_capture import __version__
from companion_capture.config import Config


CheckResult = Tuple[str, str, Optional[str]]


def _check_config() -> CheckResult:
    """Validate config loads without error."""
    try:
        Config.load()
        return ("pass", "Config loaded successfully", None)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return (
            "fail",
            f"Config invalid: {exc}",
            "Check ~/.companion-capture/config.json syntax",
        )


def _check_python_version() -> CheckResult:
    """Check Python >= 3.9."""
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 9):
        return ("pass", f"Python {version_str} detected", None)
    return (
        "fail",
        f"Python 3.9+ required, found {version_str}",
        "Install Python 3.9+ from python.org",
    )


def _check_shell_alias() -> CheckResult:
    """Check shell rc file for wrapper alias."""
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        rc_file = Path("~/.zshrc").expanduser()
    else:
        rc_file = Path("~/.bashrc").expanduser()

    rc_display = f"~/{rc_file.name}"
    if not rc_file.exists():
        return (
            "fail",
            f"Shell alias not found in {rc_display}",
            "Run: scripts/install.sh",
        )

    try:
        content = rc_file.read_text(encoding="utf-8")
    except OSError:
        return ("fail", f"Cannot read {rc_display}", "Run: scripts/install.sh")

    if "companion-capture" in content and "wrapper.sh" in content:
        return ("pass", f"Shell alias found in {rc_display}", None)
    return (
        "fail",
        f"Shell alias not found in {rc_display}",
        "Run: scripts/install.sh",
    )


def _check_hook() -> CheckResult:
    """Check PostToolUse hook in Claude settings."""
    settings_path = Path("~/.claude/settings.json").expanduser()
    if not settings_path.exists():
        return (
            "fail",
            "PostToolUse hook not configured",
            "Run: scripts/install.sh",
        )

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return (
            "fail",
            "PostToolUse hook not configured",
            "Run: scripts/install.sh",
        )

    hooks = data.get("hooks", {})
    post_tool_use = hooks.get("PostToolUse", [])
    for entry in post_tool_use:
        for h in entry.get("hooks", []):
            cmd = h.get("command", "")
            if "companion-capture" in cmd and "check.sh" in cmd:
                return ("pass", "PostToolUse hook found in settings.json", None)

    return (
        "fail",
        "PostToolUse hook not configured",
        "Run: scripts/install.sh",
    )


def _check_parser_syntax() -> CheckResult:
    """Verify parser.py compiles without syntax errors."""
    parser_path = Path(__file__).parent / "parser.py"
    if not parser_path.exists():
        return (
            "fail",
            "Parser not found at expected path",
            "Check src/companion_capture/parser.py exists",
        )

    try:
        source = parser_path.read_text(encoding="utf-8")
        compile(source, str(parser_path), "exec")
        return ("pass", "Parser syntax OK", None)
    except SyntaxError as exc:
        return (
            "fail",
            f"Parser has syntax error: {exc}",
            "Check src/companion_capture/parser.py for syntax errors",
        )


def _check_file_permissions(config: Config) -> list[CheckResult]:
    """Check output_dir and log_dir are writable."""
    results: list[CheckResult] = []
    checks = [
        ("Output directory", config.output_dir),
        ("Log directory", config.log_dir),
    ]
    for label, path in checks:
        if os.path.isdir(path) and os.access(path, os.W_OK):
            results.append(("pass", f"{label} writable: {path}", None))
        else:
            results.append(
                (
                    "fail",
                    f"Directory not writable: {path}",
                    f"Run: mkdir -p {path} && chmod 755 {path}",
                )
            )
    return results


def _check_parser_heartbeat(config: Config) -> CheckResult:
    """Check parser heartbeat freshness (skip if no session active)."""
    log_dir = Path(config.log_dir)
    if not log_dir.is_dir():
        return ("skip", "No active parser session detected", None)

    heartbeat_files = list(log_dir.glob(".parser-heartbeat-*"))
    if not heartbeat_files:
        return ("skip", "No active parser session detected", None)

    newest = max(heartbeat_files, key=lambda f: f.stat().st_mtime)
    age = time.time() - newest.stat().st_mtime
    age_int = int(age)

    if age < 600:
        return ("pass", f"Parser heartbeat OK ({age_int}s ago)", None)
    return (
        "fail",
        f"Parser heartbeat stale ({age_int}s)",
        "Restart your Claude session via the wrapper",
    )


def _check_claude_md() -> CheckResult:
    """Check CLAUDE.md has companion instructions."""
    from companion_capture.claude_md_snippet import snippet_present

    claude_md = Path("~/.claude/CLAUDE.md").expanduser()
    if not claude_md.exists():
        return (
            "fail",
            "Companion instructions not found in ~/.claude/CLAUDE.md",
            "Run: scripts/install.sh",
        )

    try:
        content = claude_md.read_text(encoding="utf-8")
    except OSError:
        return (
            "fail",
            "Cannot read ~/.claude/CLAUDE.md",
            "Run: scripts/install.sh",
        )

    if snippet_present(content):
        return ("pass", "Companion instructions found in ~/.claude/CLAUDE.md", None)
    return (
        "fail",
        "Companion instructions not found in ~/.claude/CLAUDE.md",
        "Run: scripts/install.sh",
    )


def _format_result(result: CheckResult) -> str:
    """Format a check result as a display line."""
    status, message, fix = result
    if status == "pass":
        line = f"\u2713 {message}"
    elif status == "fail":
        line = f"\u2717 {message}"
        if fix:
            line += f"\n  \u2192 Fix: {fix}"
    else:
        line = f"- {message}"
    return line


def doctor() -> int:
    """Run diagnostic checks and return exit code."""
    results: list[CheckResult] = []

    # 1. Config validity
    config_result = _check_config()
    results.append(config_result)

    # Load config for later checks (use defaults if config failed)
    try:
        config = Config.load()
    except (ValueError, OSError, json.JSONDecodeError):
        config = Config()

    # 2. Python version
    results.append(_check_python_version())

    # 3. Shell alias
    results.append(_check_shell_alias())

    # 4. PostToolUse hook
    results.append(_check_hook())

    # 5. Parser syntax
    results.append(_check_parser_syntax())

    # 6. CLAUDE.md instructions
    results.append(_check_claude_md())

    # 7. File permissions
    results.extend(_check_file_permissions(config))

    # 8. Parser heartbeat
    results.append(_check_parser_heartbeat(config))

    # Print results
    for result in results:
        print(_format_result(result))

    # Exit code: 1 if any failure
    has_failure = any(r[0] == "fail" for r in results)
    return 1 if has_failure else 0


# --- migrate subcommand ---

_KEEL_OUTPUT_FILES = {
    "KEEL-captures.md": "captures",
    "KEEL-debug.md": "debug",
    "KEEL-archive.md": "archive",
}

_OLD_KEEL_LOG_DIR = Path("~/.claude/keel-logs/").expanduser()


def _migrate_output_files(config: Config) -> list[str]:
    """Rename KEEL-* output files to companion-capture names. Returns action log."""
    actions: list[str] = []
    output_dir = Path(config.output_dir)
    name = config.companion_name

    for old_name, suffix in _KEEL_OUTPUT_FILES.items():
        src = output_dir / old_name
        dst = output_dir / f"{name}-{suffix}.md"

        if not src.exists():
            continue
        if dst.exists():
            actions.append(f"Skipped {old_name} (target {dst.name} already exists)")
            continue

        src.rename(dst)
        actions.append(f"Renamed {old_name} -> {dst.name}")

    return actions


def _migrate_marker_files(config: Config) -> list[str]:
    """Move and rename marker files from old keel-logs/ to new log_dir. Returns action log."""
    actions: list[str] = []
    old_dir = _OLD_KEEL_LOG_DIR
    new_dir = Path(config.log_dir)
    name = config.companion_name

    if not old_dir.is_dir():
        return actions

    new_dir.mkdir(parents=True, exist_ok=True)

    for item in old_dir.iterdir():
        fname = item.name

        # Rename KEEL-captures.md / KEEL-debug.md in marker filenames
        new_fname = fname
        for old_file_stem, suffix in _KEEL_OUTPUT_FILES.items():
            if old_file_stem in fname:
                new_fname = fname.replace(old_file_stem, f"{name}-{suffix}.md")
                break

        dst = new_dir / new_fname
        if dst.exists():
            actions.append(f"Skipped {fname} (already exists in new log dir)")
            continue

        shutil.move(str(item), str(dst))
        actions.append(f"Moved {fname} -> {new_dir.name}/{new_fname}")

    return actions


def _get_rc_file() -> Path:
    """Return the shell rc file path based on $SHELL."""
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return Path("~/.zshrc").expanduser()
    return Path("~/.bashrc").expanduser()


def _migrate_remove_keel_alias() -> list[str]:
    """Remove old keel alias from shell rc file. Returns action log."""
    actions: list[str] = []
    rc_file = _get_rc_file()

    if not rc_file.exists():
        return actions

    try:
        lines = rc_file.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return actions

    if not any("claude-keel.sh" in line for line in lines):
        return actions

    new_lines: list[str] = []
    skip_next = False

    for line in lines:
        stripped = line.strip()
        # Comment marker line right before the alias
        if stripped.lower().startswith("# keel") and not skip_next:
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            if "claude-keel.sh" in line:
                continue
            # Comment wasn't followed by alias — preserve both
            new_lines.append(
                f"# {stripped}\n" if not stripped.startswith("#") else line
            )
            continue
        # Standalone alias line without comment marker above
        if "claude-keel.sh" in line:
            continue
        new_lines.append(line)

    try:
        rc_file.write_text("".join(new_lines), encoding="utf-8")
    except OSError:
        return actions
    actions.append(f"Removed Keel alias from ~/{rc_file.name}")
    return actions


def _migrate_remove_keel_hook() -> list[str]:
    """Remove old keel-check.sh hook from settings.json. Returns action log."""
    actions: list[str] = []
    settings_path = Path("~/.claude/settings.json").expanduser()

    if not settings_path.exists():
        return actions

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return actions

    post_hooks = data.get("hooks", {}).get("PostToolUse", [])
    if not post_hooks:
        return actions

    original_count = len(post_hooks)
    filtered = []
    for entry in post_hooks:
        is_keel = False
        for h in entry.get("hooks", []):
            cmd = h.get("command", "")
            if "keel-check.sh" in cmd:
                is_keel = True
                break
        if not is_keel:
            filtered.append(entry)

    if len(filtered) == original_count:
        return actions

    data["hooks"]["PostToolUse"] = filtered

    # Clean up empty structures
    if not data["hooks"]["PostToolUse"]:
        del data["hooks"]["PostToolUse"]
    if not data["hooks"]:
        del data["hooks"]

    try:
        settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return actions
    actions.append("Removed keel-check.sh hook from settings.json")
    return actions


def _migrate_check_new_wiring() -> list[str]:
    """Check if companion-capture wiring is present. Returns advice messages."""
    advice: list[str] = []

    rc_file = _get_rc_file()
    has_alias = False
    if rc_file.exists():
        try:
            content = rc_file.read_text(encoding="utf-8")
            has_alias = "companion-capture" in content and "wrapper.sh" in content
        except OSError:
            pass

    settings_path = Path("~/.claude/settings.json").expanduser()
    has_hook = False
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            for entry in data.get("hooks", {}).get("PostToolUse", []):
                for h in entry.get("hooks", []):
                    cmd = h.get("command", "")
                    if "companion-capture" in cmd and "check.sh" in cmd:
                        has_hook = True
                        break
        except (json.JSONDecodeError, OSError):
            pass

    if not has_alias or not has_hook:
        advice.append("companion-capture wiring not detected. Run: scripts/install.sh")

    return advice


def migrate() -> int:
    """Migrate from Keel installation to companion-capture. Returns exit code."""
    try:
        config = Config.load()
    except (ValueError, OSError, json.JSONDecodeError):
        config = Config()

    all_actions: list[str] = []
    all_skips: list[str] = []

    # Step 1: Rename output files
    file_actions = _migrate_output_files(config)
    for a in file_actions:
        if a.startswith("Skipped"):
            all_skips.append(a)
        else:
            all_actions.append(a)

    # Step 2: Migrate marker files
    marker_actions = _migrate_marker_files(config)
    for a in marker_actions:
        if a.startswith("Skipped"):
            all_skips.append(a)
        else:
            all_actions.append(a)

    # Step 3: Remove old Keel alias
    alias_actions = _migrate_remove_keel_alias()
    all_actions.extend(alias_actions)

    # Step 4: Remove old keel-check.sh hook
    hook_actions = _migrate_remove_keel_hook()
    all_actions.extend(hook_actions)

    # Step 5: Check new wiring
    advice = _migrate_check_new_wiring()

    # Step 6: Summary
    print("[companion-capture] --- Migration summary ---")
    print()

    if all_actions:
        print("[companion-capture] Actions taken:")
        for a in all_actions:
            print(f"[companion-capture]   \u2713 {a}")

    if all_skips:
        print("[companion-capture] Skipped:")
        for s in all_skips:
            print(f"[companion-capture]   \u2192 {s}")

    if not all_actions and not all_skips:
        print("[companion-capture] Nothing to migrate (no Keel artifacts found).")

    if advice:
        print()
        for msg in advice:
            print(f"[companion-capture] \u2192 {msg}")

    return 0


# --- import subcommand --------------------------------------------------------


def import_captures(dry_run: bool = False) -> int:
    """Import existing markdown captures into SQLite. Returns exit code."""
    from companion_capture.importer import (
        discover_files,
        import_to_store,
        parse_markdown_file,
    )
    from companion_capture.store import CaptureStore

    try:
        config = Config.load()
    except (ValueError, OSError, json.JSONDecodeError):
        config = Config()

    files = discover_files(config)

    if not files:
        print("[companion-capture] No capture files found to import.")
        return 0

    print(f"[companion-capture] Found {len(files)} file(s) to import:")
    for f in files:
        print(f"[companion-capture]   {f.name}")

    all_entries = []
    errors = []
    for f in files:
        try:
            entries = parse_markdown_file(f)
        except OSError as exc:
            errors.append(f"Failed to read {f.name}: {exc}")
            print(f"[companion-capture]   {f.name}: read error — {exc}")
            continue
        print(f"[companion-capture]   {f.name}: {len(entries)} entries parsed")
        all_entries.extend(entries)

    if not all_entries:
        print("[companion-capture] No entries found in capture files.")
        return 0

    if dry_run:
        print(
            f"\n[companion-capture] Dry run: {len(all_entries)} entries would be imported."
        )
        return 0

    with CaptureStore(config.db_path) as store:
        result = import_to_store(store, all_entries)

    print("\n[companion-capture] --- Import summary ---")
    print(f"[companion-capture]   Files scanned: {len(files)}")
    print(f"[companion-capture]   Entries parsed: {result.entries_parsed}")
    print(f"[companion-capture]   Imported: {result.entries_imported}")
    print(f"[companion-capture]   Skipped (already in DB): {result.entries_skipped}")

    if result.errors:
        print(f"[companion-capture]   Errors: {len(result.errors)}")
        for err in result.errors:
            print(f"[companion-capture]     {err}")

    return 1 if result.errors else 0


# --- query subcommands -------------------------------------------------------

_SINCE_RE = re.compile(r"^(\d+)([mhdw])$")


def parse_since(value: str) -> str:
    """Parse a relative duration string and return an ISO 8601 UTC datetime.

    Supported formats: "30m", "24h", "7d", "2w".
    Raises ValueError on invalid input.
    """
    import datetime as _dt  # noqa

    if not value:
        raise ValueError("Invalid duration: empty string")

    m = _SINCE_RE.match(value)
    if not m:
        raise ValueError(
            f"Invalid duration: {value!r} (expected format like '7d', '24h', '2w', '30m')"
        )

    amount = int(m.group(1))
    unit = m.group(2)

    if amount <= 0:
        raise ValueError(f"Invalid duration: {value!r} (amount must be positive)")

    unit_map = {
        "m": "minutes",
        "h": "hours",
        "d": "days",
        "w": "weeks",
    }

    delta = _dt.timedelta(**{unit_map[unit]: amount})
    ts = _dt.datetime.now(_dt.timezone.utc) - delta
    return ts.isoformat()


def _format_capture(row: dict) -> str:
    """Format a single capture row for display."""
    ts = row.get("timestamp", "")[:16].replace("T", " ")
    classification = row.get("classification", "")
    project = row.get("project")
    text = row.get("raw_text", "")

    parts = [f"[{ts}]"]
    if classification:
        parts.append(f"[{classification}]")
    if project:
        parts.append(f"({project})")
    parts.append(text)

    return " ".join(parts)


def cmd_search(args) -> int:
    """Search captures by query string."""
    from companion_capture.store import CaptureStore

    since = None
    if args.since:
        try:
            since = parse_since(args.since)
        except ValueError as exc:
            print(f"[companion-capture] Error: {exc}", file=sys.stderr)
            return 1

    try:
        config = Config.load()
    except (ValueError, OSError, json.JSONDecodeError):
        config = Config()

    with CaptureStore(config.db_path) as store:
        results = store.search(
            args.query,
            project=args.project,
            classification=args.classification,
            since=since,
            limit=args.limit,
        )

    if not results:
        print("No captures found.")
        return 0

    for row in results:
        print(_format_capture(row))

    return 0


def cmd_recent(args) -> int:
    """Show most recent captures."""
    from companion_capture.store import CaptureStore

    try:
        config = Config.load()
    except (ValueError, OSError, json.JSONDecodeError):
        config = Config()

    with CaptureStore(config.db_path) as store:
        results = store.recent(
            project=args.project,
            limit=args.limit,
        )

    if not results:
        print("No captures found.")
        return 0

    for row in results:
        print(_format_capture(row))

    return 0


def cmd_stats(args) -> int:
    """Show capture statistics."""
    from companion_capture.store import CaptureStore

    try:
        config = Config.load()
    except (ValueError, OSError, json.JSONDecodeError):
        config = Config()

    with CaptureStore(config.db_path) as store:
        data = store.stats()

    if data["total"] == 0:
        print("No captures in database.")
        return 0

    print(f"Captures: {data['total']}")

    earliest = (data.get("earliest") or "")[:10]
    latest = (data.get("latest") or "")[:10]
    if earliest and latest:
        print(f"Date range: {earliest} — {latest}")

    by_project = data.get("by_project", {})
    if by_project:
        print()
        print("By project:")
        for name, count in sorted(by_project.items(), key=lambda x: -x[1]):
            print(f"  {name}    {count}")

    by_class = data.get("by_classification", {})
    if by_class:
        print()
        print("By classification:")
        for name, count in sorted(by_class.items(), key=lambda x: -x[1]):
            print(f"  {name}    {count}")

    return 0


# --- redact subcommand -------------------------------------------------------

_SCHEMA_COMMENT_RE = re.compile(r"^<!--\s*schema:\d+\s+id:")


def redact_markdown_file(filepath: Path, pattern: str) -> int:
    """Remove entries matching pattern from a markdown capture file.

    Handles both new format (comment + entry line) and old format (entry only).
    Returns count of removed entries.
    """
    if not filepath.exists():
        return 0

    try:
        lines = filepath.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return 0

    kept: list[str] = []
    removed = 0
    i = 0

    while i < len(lines):
        line = lines[i]

        # New format: <!-- schema:... --> comment followed by - [tag] line
        if _SCHEMA_COMMENT_RE.match(line.strip()):
            entry_line = lines[i + 1] if i + 1 < len(lines) else ""
            if re.search(pattern, entry_line):
                removed += 1
                i += 2  # skip both comment and entry
                continue
            kept.append(line)
            i += 1
            continue

        # Old format: bare - [tag] ... line (no preceding comment)
        stripped = line.strip()
        if stripped.startswith("- `[") and re.search(pattern, stripped):
            removed += 1
            i += 1
            continue

        kept.append(line)
        i += 1

    if removed > 0:
        filepath.write_text("".join(kept), encoding="utf-8")

    return removed


def _count_markdown_matches(filepath: Path, pattern: str) -> int:
    """Count entries that would be removed by redact, without modifying the file."""
    if not filepath.exists():
        return 0

    try:
        lines = filepath.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return 0

    count = 0
    i = 0

    while i < len(lines):
        line = lines[i]

        if _SCHEMA_COMMENT_RE.match(line.strip()):
            entry_line = lines[i + 1] if i + 1 < len(lines) else ""
            if re.search(pattern, entry_line):
                count += 1
                i += 2
                continue
            i += 1
            continue

        stripped = line.strip()
        if stripped.startswith("- `[") and re.search(pattern, stripped):
            count += 1

        i += 1

    return count


def cmd_redact(args) -> int:
    """Redact captures matching a regex pattern from SQLite and markdown files."""
    from companion_capture.store import CaptureStore

    # Validate regex early
    try:
        re.compile(args.pattern)
    except re.error as exc:
        print(f"[companion-capture] Error: invalid regex: {exc}", file=sys.stderr)
        return 1

    try:
        config = Config.load()
    except (ValueError, OSError, json.JSONDecodeError):
        config = Config()

    md_files = [config.captures_file, config.debug_file, config.archive_file]

    if not args.confirm:
        # Dry-run: count matches without modifying anything
        with CaptureStore(config.db_path) as store:
            rows = (
                store._conn.execute("SELECT raw_text FROM captures").fetchall()
                if store._conn
                else []
            )
            db_count = sum(1 for r in rows if re.search(args.pattern, r[0]))

        md_total = 0
        for f in md_files:
            n = _count_markdown_matches(f, args.pattern)
            if n > 0:
                md_total += n
                print(f"[companion-capture]   {f.name}: {n} entries match")

        if db_count:
            print(f"[companion-capture]   SQLite: {db_count} rows match")

        total = db_count + md_total
        if total == 0:
            print("[companion-capture] No captures match the pattern.")
        else:
            print(
                f"\n[companion-capture] {total} total matches. "
                f"Re-run with --confirm to delete."
            )
        return 0

    # Confirmed: actually delete
    with CaptureStore(config.db_path) as store:
        db_deleted = store.delete_matching(args.pattern)

    md_deleted = 0
    for f in md_files:
        n = redact_markdown_file(f, args.pattern)
        if n > 0:
            md_deleted += n
            print(f"[companion-capture]   {f.name}: {n} entries removed")

    if db_deleted:
        print(f"[companion-capture]   SQLite: {db_deleted} rows removed")

    total = db_deleted + md_deleted
    if total == 0:
        print("[companion-capture] No captures matched the pattern.")
    else:
        print(f"\n[companion-capture] {total} total captures redacted.")

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="companion-capture",
        description="Capture AI companion speech bubble messages from terminal sessions",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="Run diagnostic checks")
    subparsers.add_parser(
        "migrate", help="Migrate from Keel installation to companion-capture"
    )
    import_parser = subparsers.add_parser(
        "import", help="Import markdown captures into SQLite"
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be imported without writing to the database",
    )

    # Query subcommands
    search_parser = subparsers.add_parser("search", help="Search captures")
    search_parser.add_argument("query", help="Search query string")
    search_parser.add_argument("--project", help="Filter by project name")
    search_parser.add_argument("--since", help="Filter by age (e.g. 7d, 24h, 2w, 30m)")
    search_parser.add_argument(
        "--classification", help="Filter by classification (vibe, debug)"
    )
    search_parser.add_argument(
        "--limit", type=int, default=20, help="Max results (default: 20)"
    )

    recent_parser = subparsers.add_parser("recent", help="Show most recent captures")
    recent_parser.add_argument("--project", help="Filter by project name")
    recent_parser.add_argument(
        "-n", "--limit", type=int, default=20, help="Max results (default: 20)"
    )

    subparsers.add_parser("stats", help="Show capture statistics")

    # Redact subcommand
    redact_parser = subparsers.add_parser(
        "redact", help="Delete captures matching a regex pattern"
    )
    redact_parser.add_argument(
        "--pattern", required=True, help="Regex pattern to match against capture text"
    )
    redact_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete matches (without this flag, only shows what would be deleted)",
    )

    args = parser.parse_args()

    if args.command == "doctor":
        sys.exit(doctor())
    elif args.command == "migrate":
        sys.exit(migrate())
    elif args.command == "import":
        sys.exit(import_captures(dry_run=args.dry_run))
    elif args.command == "search":
        sys.exit(cmd_search(args))
    elif args.command == "recent":
        sys.exit(cmd_recent(args))
    elif args.command == "stats":
        sys.exit(cmd_stats(args))
    elif args.command == "redact":
        sys.exit(cmd_redact(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
