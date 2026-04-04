"""Configuration model for companion-capture.

Precedence: hardcoded defaults < config file < environment variables.
"""

from __future__ import annotations

import datetime
import fnmatch  # noqa: F401
import json
import os
import re
import sys  # noqa: F401
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_ -]+$")

SCHEMA_VERSION = 1
CONFIG_DIR = Path("~/.companion-capture").expanduser()
CONFIG_FILE = CONFIG_DIR / "config.json"

# Env var prefix and mapping from config field to env var suffix
_ENV_PREFIX = "COMPANION_"
_ENV_MAP = {
    "companion_name": "NAME",
    "output_dir": "OUTPUT_DIR",
    "log_dir": "LOG_DIR",
    "rotation_days": "ROTATION_DAYS",
    "archive_days": "ARCHIVE_DAYS",
    "debug": "DEBUG",
    "exclude_patterns": "EXCLUDE_PATTERNS",
    "excluded_projects": "EXCLUDED_PROJECTS",
    "recall_enabled": "RECALL_ENABLED",
    "recall_max_results": "RECALL_MAX_RESULTS",
    "recall_cooldown_seconds": "RECALL_COOLDOWN_SECONDS",
}


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes")


@dataclass
class Config:
    companion_name: str = "Companion"
    output_dir: str = "~/.claude/"
    log_dir: str = "~/.companion-capture/logs/"
    rotation_days: int = 7
    archive_days: int = 90
    debug: bool = False
    exclude_patterns: list[str] = field(default_factory=list)
    excluded_projects: list[str] = field(default_factory=list)
    recall_enabled: bool = False
    recall_max_results: int = 3
    recall_cooldown_seconds: int = 60

    def __post_init__(self) -> None:
        # Normalize and validate companion_name — blocks shell injection,
        # path traversal, and leading/trailing whitespace in filenames
        self.companion_name = self.companion_name.strip()
        if not _SAFE_NAME_RE.match(self.companion_name):
            raise ValueError(
                f"companion_name must be alphanumeric, spaces, hyphens, "
                f"or underscores only, got: {self.companion_name!r}"
            )
        # Expand ~ in path fields
        self.output_dir = str(Path(self.output_dir).expanduser())
        self.log_dir = str(Path(self.log_dir).expanduser())

    @classmethod
    def load(cls, config_path: Path | None = None) -> Config:
        """Load config: defaults < config file < env vars."""
        if config_path is None:
            config_path = CONFIG_FILE

        file_overrides: dict = {}
        if config_path.exists():
            try:
                text = config_path.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""
            if not text:
                # Empty file — treat as no overrides (use defaults)
                pass
            else:
                try:
                    file_overrides = json.loads(text)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(
                        f"Invalid JSON in config file {config_path}: {exc}"
                    ) from exc
            if not isinstance(file_overrides, dict):
                raise ValueError(
                    f"Config file {config_path} must contain a JSON object"
                )

        # Only accept keys that match dataclass fields
        valid_keys = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in file_overrides.items() if k in valid_keys}

        # Apply env var overrides
        _LIST_FIELDS = {"exclude_patterns", "excluded_projects"}
        for field_name, env_suffix in _ENV_MAP.items():
            env_val = os.environ.get(f"{_ENV_PREFIX}{env_suffix}")
            if env_val is not None:
                if field_name in ("debug", "recall_enabled"):
                    kwargs[field_name] = _parse_bool(env_val)
                elif field_name in (
                    "rotation_days",
                    "archive_days",
                    "recall_max_results",
                    "recall_cooldown_seconds",
                ):
                    kwargs[field_name] = int(env_val)
                elif field_name in _LIST_FIELDS:
                    kwargs[field_name] = [
                        item.strip() for item in env_val.split(",") if item.strip()
                    ]
                else:
                    kwargs[field_name] = env_val

        return cls(**kwargs)

    @property
    def config_dir(self) -> Path:
        return CONFIG_DIR

    @property
    def captures_file(self) -> Path:
        return Path(self.output_dir) / f"{self.companion_name}-captures.md"

    @property
    def debug_file(self) -> Path:
        return Path(self.output_dir) / f"{self.companion_name}-debug.md"

    @property
    def archive_file(self) -> Path:
        return Path(self.output_dir) / f"{self.companion_name}-archive.md"

    @property
    def db_path(self) -> Path:
        return self.config_dir / "captures.db"

    def should_exclude(self, message: str, project: str) -> bool:
        """Return True if message or project matches any exclusion pattern."""
        for pattern in self.exclude_patterns:
            try:
                if re.search(pattern, message):
                    return True
            except re.error:
                if self.debug:
                    print(
                        f"companion-capture: invalid regex in exclude_patterns: {pattern!r}",
                        file=sys.stderr,
                    )
        for proj_pattern in self.excluded_projects:
            if fnmatch.fnmatch(project, proj_pattern):
                return True
        return False

    def ensure_dirs(self) -> None:
        """Create config_dir, log_dir, and output_dir if they don't exist."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

    def generate_entry_id(self) -> str:
        """Return an HTML comment with schema version, UUID, and UTC timestamp."""
        entry_uuid = uuid.uuid4()
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return f"<!-- schema:{SCHEMA_VERSION} id:{entry_uuid} ts:{ts} -->"
