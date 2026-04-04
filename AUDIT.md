# Audit — 2026-04-04

v2.0 (Sections 1–10) full codebase audit. 3,648 lines source, 411 tests, 0.33s. Updated after cross-check.

## Critical (fix now)

None.

## Warning (fix soon)

- **Unprotected file write** — `cli.py:801` `redact_markdown_file()` calls `filepath.write_text()` without try/except. Disk error during redact silently drops partial results.
- ~~Ruff lint errors (2)~~ — Fixed (cross-check): `test_cli_query.py:319` renamed `l` → `line`; `test_recall.py:141` removed unused `store` variable.
- ~~Cooldown marker burns timer on missing DB~~ — Fixed (cross-check): `recall.py` reordered DB existence check before cooldown touch.
- ~~Cooldown fails open on I/O error~~ — Fixed (cross-check): `_check_cooldown()` returns False on OSError (fail closed).
- ~~`echo` mangles JSON in check.sh~~ — Fixed (cross-check): replaced `echo` with `printf '%s'` for safe JSON piping.
- ~~raw_text newlines in recall output~~ — Fixed (cross-check): `_sanitize_text()` strips newlines and control chars before formatting.
- **`watch_live()` is 188 lines** — `parser.py:576`. Core runtime loop with signal handlers, non-blocking reads, dedup. Extract scan/decision logic into helper.
- **`cli.py` is 988 lines** — combines doctor + migrate + import + query + redact + main. Split into submodules.
- **Zero test coverage on `watch_live()`** — Core runtime loop, highest-risk untested code.
- **`cli.py main()` untested** — No tests for: no subcommand, `--version`, unknown subcommand, argparse routing.
- **`fake_expanduser` duplicated** — across `test_doctor.py` (8x) and `test_migrate.py` (2x). Extract to `tests/conftest.py`.
- **`strip_ansi()` is dead code** — `parser.py:43`. Never called by production paths. Remove or document as utility.
- **Session ID collision** — `wrapper.sh:56` uses seconds precision. Two sessions in same second collide. Add PID or UUID suffix.

## Info (nice to have)

- **Magic numbers** — `SCAN_INTERVAL=5.0`, `min_col=40`, `±20` window, `> 8` msg length, `> 0.5` word overlap (2 locations: `parser.py:535`, `parser.py:562`), `600s` heartbeat stale, `65536` chunk size — extract as named constants.
- **README lacks v2 features** — SQLite store, import, search/recent/stats, redact, recall hook not documented in README.md.
- **README demo GIF placeholder** — `README.md:9` has `<!-- TODO -->`.
- **`_erase_display(mode=1)` untested** — `parser.py:226`. Modes 0 and 2 covered.
- **`_append_entry` corruption recovery untested** — `parser.py:425`. Bad-header salvage logic.
- **2 TODOs in source** — `importer.py:169` (dry_run read-only store), `wrapper.sh:56` (session ID collision).

## Clean

- **411 tests pass** (0.33s) — full coverage of config, store, parser extraction, importer, doctor, migrate, query CLI, redact, recall
- **All Python files compile** — `py_compile` passes on all 7 modules
- **All shell scripts pass `bash -n`** — check.sh, wrapper.sh, archive.sh, install.sh, uninstall.sh
- **SQL injection safe** — all queries use parameterized `?` placeholders (store.py)
- **Shell injection safe** — `shlex.quote()` in wrapper.sh, `companion_name` validated via regex
- **Path traversal safe** — `companion_name` blocks `/`, `\`, shell metacharacters
- **No secrets or credentials** in code
- **No unused dependencies** — zero runtime deps, dev deps (pytest, ruff) both used
- **SIGUSR1 + SIGTERM handlers safe** — flag pattern, no I/O in signal handlers
- **UTF-8 chunk boundaries handled** — byte carry buffer in `watch_live()`
- **SQLite properly configured** — WAL mode, busy_timeout=5000, FTS5 with LIKE fallback
- **Dual-write architecture sound** — markdown primary, SQLite additive, SQLite failure silent
- **Recall hook properly isolated** — opt-in, cooldown enforced, never crashes, Read-only trigger
- **Config validation thorough** — name regex, path expansion, type coercion, env var precedence
- **Privacy controls working** — exclude_patterns, excluded_projects, redact with dry-run safety
- **No state drift** — STATE.md, CLAUDE.md, PLAN.md all accurate vs codebase
- **All PLAN.md section 1–10 deliverables exist** — no gaps
