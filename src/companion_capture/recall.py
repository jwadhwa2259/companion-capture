"""Recall — surfaces recent project captures on Read events (opt-in)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from companion_capture.config import Config
from companion_capture.store import CaptureStore


def get_project_name() -> str:
    """Return current project name from working directory."""
    return os.path.basename(os.getcwd()) or ""


def _check_cooldown(marker: Path, cooldown_seconds: int) -> bool:
    """Return True if cooldown has elapsed (OK to recall). Touches marker.

    Fails closed: if marker I/O fails, returns False (suppress recall)
    to avoid spamming on every Read event.
    """
    try:
        exists = marker.exists()
    except OSError:
        return False
    if exists:
        try:
            age = time.time() - marker.stat().st_mtime
            if age < cooldown_seconds:
                return False
        except OSError:
            return False

    # Touch marker (create parent if needed)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        return False
    return True


def _sanitize_text(text: str) -> str:
    """Replace newlines and control chars with spaces for safe single-line output."""
    import re as _re

    return _re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()


def _format_results(results: list[dict], companion_name: str, project: str) -> str:
    """Format recall results for stdout output."""
    lines = [f"[{companion_name} recall — {len(results)} recent for {project}]"]
    for r in results:
        ts = (r.get("timestamp") or "")[:16].replace("T", " ")
        tag = r.get("classification", "")
        text = _sanitize_text(r.get("raw_text", ""))
        lines.append(f"  [{ts}] [{tag}] {text}")
    return "\n".join(lines)


def recall(config: Config | None = None) -> str | None:
    """Query recent captures for current project.

    Returns formatted output string, or None if nothing to surface.
    """
    if config is None:
        config = Config.load()

    if not config.recall_enabled:
        return None

    project = get_project_name()
    if not project:
        return None

    # Query store — check DB exists before burning cooldown
    if not config.db_path.exists():
        return None

    # Cooldown check (after DB check so missing DB doesn't burn the timer)
    log_dir = Path(config.log_dir)
    marker = log_dir / f".recall-cooldown-{project}"
    if not _check_cooldown(marker, config.recall_cooldown_seconds):
        return None

    try:
        with CaptureStore(config.db_path) as store:
            results = store.recent(project=project, limit=config.recall_max_results)
    except Exception:
        return None

    if not results:
        return None

    return _format_results(results, config.companion_name, project)


def main() -> None:
    """Entry point for shell hook: print recall to stdout, never crash."""
    try:
        config = Config.load()
        output = recall(config)
        if output:
            print(output)
    except Exception:
        pass


if __name__ == "__main__":
    main()
