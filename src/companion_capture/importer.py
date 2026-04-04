"""Import and backfill markdown captures into SQLite."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from companion_capture.config import Config, SCHEMA_VERSION
from companion_capture.store import CaptureStore

# --- Regex patterns -----------------------------------------------------------

# Date header: ### 2026-04-03
_DATE_HEADER_RE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})\s*$")

# Identity comment: <!-- schema:1 id:uuid-here ts:2026-04-04T12:00:00+00:00 -->
_IDENTITY_RE = re.compile(r"<!--\s*schema:\d+\s+id:([0-9a-f-]+)\s+ts:(\S+)\s*-->")

# Entry line: - `[tag]` `HH:MM` `project` — message
_ENTRY_RE = re.compile(r"^-\s+`\[(\w+)\]`\s+`(\d{2}:\d{2})`\s+`([^`]+)`\s+—\s+(.+)$")

# Legacy Keel filenames
_KEEL_FILES = {
    "KEEL-captures.md": "captures",
    "KEEL-archive.md": "archive",
}


# --- Data types ---------------------------------------------------------------


@dataclass
class ParsedEntry:
    """A single parsed capture entry."""

    id: str
    timestamp: str
    project: str
    raw_text: str
    classification: str
    schema_version: int = SCHEMA_VERSION


@dataclass
class ImportResult:
    """Summary of an import operation."""

    files_scanned: int = 0
    entries_parsed: int = 0
    entries_imported: int = 0
    entries_skipped: int = 0
    errors: List[str] = field(default_factory=list)


# --- Core functions -----------------------------------------------------------


def compute_deterministic_id(date: str, time: str, project: str, message: str) -> str:
    """SHA-256 hash of (date + time + project + message), truncated to UUID-like format."""
    content = f"{date}|{time}|{project}|{message}"
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    # Format as UUID-like: 8-4-4-4-12
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def parse_markdown_file(path: Path) -> List[ParsedEntry]:
    """Parse a markdown capture file and return extracted entries.

    Handles both old format (no identity comment) and new format
    (with <!-- schema:N id:UUID ts:ISO --> lines).
    """
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")

    lines = content.splitlines()
    entries: List[ParsedEntry] = []
    current_date: Optional[str] = None
    pending_identity: Optional[tuple] = None  # (uuid, timestamp)

    for line in lines:
        # Track date headers
        date_match = _DATE_HEADER_RE.match(line)
        if date_match:
            current_date = date_match.group(1)
            pending_identity = None
            continue

        # Track identity comments
        id_match = _IDENTITY_RE.search(line)
        if id_match:
            pending_identity = (id_match.group(1), id_match.group(2))
            continue

        # Parse entry lines
        entry_match = _ENTRY_RE.match(line)
        if entry_match and current_date:
            tag = entry_match.group(1)
            time_str = entry_match.group(2)
            project = entry_match.group(3)
            message = entry_match.group(4)

            if pending_identity:
                # New format — use existing UUID and timestamp
                entry_id, entry_ts = pending_identity
            else:
                # Old format — deterministic ID from content
                entry_id = compute_deterministic_id(
                    current_date, time_str, project, message
                )
                entry_ts = f"{current_date}T{time_str}:00+00:00"

            entries.append(
                ParsedEntry(
                    id=entry_id,
                    timestamp=entry_ts,
                    project=project,
                    raw_text=message,
                    classification=tag,
                )
            )
            pending_identity = None
            continue

        # Non-matching line (including blank) resets pending identity
        stripped = line.strip()
        if not stripped or not stripped.startswith("#"):
            pending_identity = None

    return entries


def discover_files(config: Config) -> List[Path]:
    """Find importable markdown files (captures + archive, both Keel and companion-capture names)."""
    output_dir = Path(config.output_dir)
    found: List[Path] = []

    # Companion-capture named files
    for suffix in ("captures", "archive"):
        path = output_dir / f"{config.companion_name}-{suffix}.md"
        if path.exists():
            found.append(path)

    # Legacy Keel files (only if not already covered by companion name)
    if config.companion_name != "KEEL":
        for keel_name in _KEEL_FILES:
            path = output_dir / keel_name
            if path.exists():
                found.append(path)

    return found


def import_to_store(
    store: CaptureStore,
    entries: List[ParsedEntry],
    dry_run: bool = False,
) -> ImportResult:
    """Insert parsed entries into the SQLite store.

    Returns an ImportResult with counts. Idempotent via INSERT OR IGNORE.
    """
    result = ImportResult(entries_parsed=len(entries))

    # TODO(cross-check): dry_run should open a read-only store to distinguish
    # "would insert" from "already present" for accurate previews
    if dry_run:
        result.entries_imported = 0
        result.entries_skipped = len(entries)
        return result

    for entry in entries:
        try:
            # Check if already exists (for accurate skip counting)
            if store._conn is not None:
                existing = store._conn.execute(
                    "SELECT 1 FROM captures WHERE id = ?", (entry.id,)
                ).fetchone()
                if existing:
                    result.entries_skipped += 1
                    continue

            store.insert(
                id=entry.id,
                timestamp=entry.timestamp,
                project=entry.project,
                session_id="",
                raw_text=entry.raw_text,
                classification=entry.classification,
                schema_version=entry.schema_version,
            )
            result.entries_imported += 1
        except Exception as exc:
            result.errors.append(f"Failed to import entry {entry.id}: {exc}")
            result.entries_skipped += 1

    return result
