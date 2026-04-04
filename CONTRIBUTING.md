# Contributing to companion-capture

## Development Setup

```bash
git clone https://github.com/jaywadhwa/companion-capture.git
cd companion-capture
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
python3 -m pytest tests/ -v
```

The test suite covers: VT100 screen buffer, bubble extraction, classification, dedup, file routing, sweep mode, config model, doctor checks, and migration logic.

## Code Style

This project uses [ruff](https://docs.astral.sh/ruff/) for linting:

```bash
ruff check src/ tests/
```

### Conventions

- **Python 3.9+** — no walrus operators or other 3.10+ syntax
- **stdlib only** — no runtime dependencies (pytest and ruff are dev-only)
- **Guard imports** with `# noqa` if the formatter would strip them (e.g., `fcntl`, `signal`)
- Comments only where the logic isn't self-evident
- Functions under 50 lines

## Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes — one concern per PR
3. Add or update tests for any new behavior
4. Run `python3 -m pytest tests/ -v` and `ruff check src/ tests/` before submitting
5. Write a clear PR description explaining what and why

## Reporting Issues

Open an issue on GitHub with:

- What you expected
- What happened instead
- Your OS version, Python version, and Claude Code version
- Relevant log output (check `~/.companion-capture/logs/`)

## Platform Support

v1 targets **macOS only**. If you're interested in Linux support, open an issue — it's planned for a future release.
