"""companion-capture CLI."""

import json
import os
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

    args = parser.parse_args()

    if args.command == "doctor":
        sys.exit(doctor())
    elif args.command == "migrate":
        sys.exit(migrate())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
