"""Configuration model for companion-capture.

Precedence: hardcoded defaults < config file < environment variables.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import uuid
from dataclasses import dataclass, fields
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
        for field_name, env_suffix in _ENV_MAP.items():
            env_val = os.environ.get(f"{_ENV_PREFIX}{env_suffix}")
            if env_val is not None:
                if field_name == "debug":
                    kwargs[field_name] = _parse_bool(env_val)
                elif field_name in ("rotation_days", "archive_days"):
                    kwargs[field_name] = int(env_val)
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
