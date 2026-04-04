# Audit — 2026-04-03

v1.0 (Sections 1–5) pre-release audit. Updated after fixes + cross-check.

## Critical (fix now)

All resolved.

- ~~Shell injection via `eval`~~ — Fixed: `companion_name` validated to `[a-zA-Z0-9_ -]+` in `Config.__post_init__()`, `wrapper.sh` uses `shlex.quote()` for all string values.
- ~~Path traversal via `companion_name`~~ — Fixed: same validation blocks path separators. Leading/trailing whitespace stripped before validation.

## Warning (fix soon)

### Resolved

- ~~Missing try/except on file writes~~ — Fixed: `cli.py` `_migrate_remove_keel_alias` and `_migrate_remove_keel_hook` writes wrapped.
- ~~TOCTOU race in `sweep()`~~ — Fixed: replaced `os.path.exists` + `open()` with direct `try/except OSError`.
- ~~Dead test~~ — Fixed: `test_doctor.py` `test_alias_present()` removed.
- ~~`import shutil` inside loop~~ — Fixed: moved to module level in `cli.py`.
- ~~SIGTERM handler calls `sys.exit()`~~ — Fixed: flag pattern, loop checks `shutdown` variable.
- ~~`archive.sh` standalone injection~~ — Fixed: integer validation on `ROTATION_DAYS` and `ARCHIVE_DAYS`.
- ~~UTF-8 chunk boundary~~ — Fixed (cross-check): byte carry buffer before decode in `parser.py` `watch_live()`.

### Remaining

- **`watch_live()` is 149 lines** — `parser.py:550`. Extract scan/decision logic into `_process_scan()` helper.
- **`cli.py` is 516 lines** — combines doctor + migrate. Consider splitting.
- **`fake_expanduser` duplicated 16x** — across `test_doctor.py` and `test_migrate.py`. Extract to `tests/conftest.py`.
- **`strip_ansi()` is dead production code** — `parser.py:42`. Never called by production paths. Document as utility or remove.
- **Zero test coverage on `watch_live()`** — Core runtime loop. Highest-risk untested code.
- **`cli.py main()` untested** — No tests for: no subcommand, `--version`, unknown subcommand.
- **Session ID collision** — `wrapper.sh:52` uses seconds precision. TODO added (cross-check).

## Info (nice to have)

- **Magic numbers** — `SCAN_INTERVAL=5.0`, `min_col=40`, `±20` window, `> 8` msg length, `> 0.5` word overlap, `600s` heartbeat stale — extract as named constants.
- **Word overlap ratio duplicated** — `parser.py:509` and `parser.py:536`. Same calculation in two functions.
- **README demo GIF placeholder** — `README.md:7` has `<!-- TODO -->`.
- **`store.py` placeholder** — Expected for v2, ships empty in v1.
- **`_erase_display(mode=1)` untested** — `parser.py:225`. Modes 0 and 2 covered.
- **`_append_entry` corruption recovery untested** — `parser.py:437`. Bad-header salvage logic.

## Clean

- **226 tests pass** (0.13s) — 7 new validation tests, 2 new load-path tests, 1 dead test removed
- **Ruff lint clean** — zero warnings
- **All 5 shell scripts pass `bash -n` syntax check**
- **Package imports and CLI entry point work correctly**
- **No secrets, API keys, or credentials in code**
- **No unused dependencies** — zero runtime deps, dev deps (pytest, ruff) both used
- **No FIXME/HACK markers** in source files (1 TODO in wrapper.sh from cross-check)
- **No state drift** — STATE.md, CLAUDE.md, PLAN.md all accurate vs codebase
- **All PLAN.md section 1–5 deliverables exist** — no gaps
- **Version strings consistent** — `__init__.py` and `pyproject.toml` both `1.0.0`
- **Symlink-based install** — alias and hook use stable `~/.companion-capture/bin/` paths
- **No file permission issues** — defaults are appropriate
- **SIGUSR1 handler uses safe flag pattern** — no I/O in handler
- **SIGTERM handler uses safe flag pattern** — sets `shutdown` flag, loop exits cleanly
- **UTF-8 chunk boundaries handled** — byte carry buffer prevents multi-byte char corruption
- **Config name validation** — rejects shell injection, path traversal, empty, whitespace-only
- **Shell eval hardened** — `shlex.quote()` on all string values
- **`.gitignore` correctly excludes `.env`, runtime files, build artifacts**
