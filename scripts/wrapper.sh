#!/usr/bin/env bash
# wrapper.sh — Session wrapper for companion-capture.
# Launches script(1) to capture TUI output, starts the parser, runs Claude Code.
# Ported from claude-keel.sh in Section 2.
#
# Usage:
#   ./wrapper.sh [claude args...]
#
# Setup (run once):
#   chmod +x ~/companion-capture/scripts/wrapper.sh
#   alias claude='~/companion-capture/scripts/wrapper.sh'
#   # Add the alias to ~/.zshrc to persist across shells

# Resolve through symlinks to find the real script location
# (install.sh symlinks this into ~/.companion-capture/bin/)
SOURCE="$0"
while [ -L "$SOURCE" ]; do
    DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    # Handle relative symlink targets
    case "$SOURCE" in /*) ;; *) SOURCE="$DIR/$SOURCE" ;; esac
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
PARSER="$SCRIPT_DIR/../src/companion_capture/parser.py"

# Load config via Python helper (shlex.quote prevents shell injection)
eval "$(python3 -c "
import shlex
from companion_capture.config import Config
c = Config.load()
q = shlex.quote
print(f'COMPANION_NAME={q(c.companion_name)}')
print(f'COMPANION_OUTPUT_DIR={q(c.output_dir)}')
print(f'COMPANION_LOG_DIR={q(c.log_dir)}')
print(f'COMPANION_ROTATION_DAYS={c.rotation_days}')
print(f'COMPANION_ARCHIVE_DAYS={c.archive_days}')
print(f'COMPANION_DEBUG={str(c.debug).lower()}')
print(f'COMPANION_CAPTURES_FILE={q(str(c.captures_file))}')
print(f'COMPANION_DEBUG_FILE={q(str(c.debug_file))}')
print(f'COMPANION_ARCHIVE_FILE={q(str(c.archive_file))}')
" 2>/dev/null)" || {
    echo "[companion-capture] Warning: config load failed — running Claude without capture"
    exec claude "$@"
}
export COMPANION_NAME COMPANION_OUTPUT_DIR COMPANION_LOG_DIR
export COMPANION_ROTATION_DAYS COMPANION_ARCHIVE_DAYS COMPANION_DEBUG
export COMPANION_CAPTURES_FILE COMPANION_DEBUG_FILE COMPANION_ARCHIVE_FILE

LOG_DIR="$COMPANION_LOG_DIR"
mkdir -p "$LOG_DIR"

# TODO(cross-check): session ID uses seconds precision — two sessions in the same
# second collide. Add PID or UUID suffix for collision resistance.
SESSION_LOG="$LOG_DIR/session_$(date +%Y%m%d_%H%M%S).log"
SESSION_ID=$(basename "$SESSION_LOG" .log | sed 's/^session_//')

# Clean up orphaned parsers (session dead, parser still alive)
# Active sibling sessions are left alone — only true orphans are killed
for pidfile in "$LOG_DIR"/.parser-*.pid; do
    [ -f "$pidfile" ] || continue
    OLD_PID=$(cat "$pidfile")
    OLD_PID=${OLD_PID:-0}
    OLD_SID=$(basename "$pidfile" .pid | sed 's/^\.parser-//')
    OLD_LOGREF="$LOG_DIR/.parser-${OLD_SID}.log"

    if kill -0 "$OLD_PID" 2>/dev/null && ps -p "$OLD_PID" -o args= 2>/dev/null | grep -qF "parser.py"; then
        # Parser alive — check if its session (script process) is still running
        if [ -f "$OLD_LOGREF" ]; then
            OLD_LOG=$(cat "$OLD_LOGREF")
            if pgrep -f "script.*$(basename "$OLD_LOG")" >/dev/null 2>&1; then
                # Session still active — leave this parser alone
                continue
            fi
        fi
        # Session dead, parser orphaned — kill + sweep
        kill "$OLD_PID" 2>/dev/null
        wait "$OLD_PID" 2>/dev/null
    fi

    # Sweep the orphaned/dead session's log so its messages aren't lost
    if [ -f "$OLD_LOGREF" ]; then
        OLD_LOG=$(cat "$OLD_LOGREF")
        [ -f "$OLD_LOG" ] && python3 "$PARSER" "$OLD_LOG" --sweep 2>/dev/null
    fi
    rm -f "$pidfile" "$OLD_LOGREF" "$LOG_DIR/.parser-heartbeat-${OLD_SID}"
done

# Archive entries older than rotation_days before starting
bash "$SCRIPT_DIR/archive.sh" 2>/dev/null

# Reset debug file — only if no sibling sessions are active
DEBUG_FILE="$COMPANION_DEBUG_FILE"
ACTIVE_SIBLINGS=0
for pidfile in "$LOG_DIR"/.parser-*.pid; do
    [ -f "$pidfile" ] || continue
    ACTIVE_SIBLINGS=$((ACTIVE_SIBLINGS + 1))
done
if [ "$ACTIVE_SIBLINGS" -eq 0 ]; then
    echo "# $COMPANION_NAME — Debug Observations

## Log
" > "$DEBUG_FILE"
fi

# Preflight: verify parser can load before committing to a session
if ! python3 -c "import ast; ast.parse(open('$PARSER').read())" 2>/dev/null; then
    echo "[companion-capture] Warning: parser failed preflight — running Claude without capture"
    exec claude "$@"
fi

# Export session ID so the hook can find session-specific heartbeat
export COMPANION_SESSION_ID="$SESSION_ID"

# Start the parser watching for companion messages in background
# Session-scoped PID file allows multiple parsers to coexist
PIDFILE="$LOG_DIR/.parser-${SESSION_ID}.pid"
LOGFILE_REF="$LOG_DIR/.parser-${SESSION_ID}.log"

python3 "$PARSER" "$SESSION_LOG" &
PARSER_PID=$!
echo "$PARSER_PID" > "$PIDFILE"
echo "$SESSION_LOG" > "$LOGFILE_REF"

# Verify it's still alive after a beat
sleep 0.3
if ! kill -0 "$PARSER_PID" 2>/dev/null; then
    echo "[companion-capture] Warning: parser died on startup — running Claude without capture"
    rm -f "$PIDFILE" "$LOGFILE_REF"
    exec claude "$@"
fi

cleanup() {
    kill "$PARSER_PID" 2>/dev/null
    wait "$PARSER_PID" 2>/dev/null
    rm -f "$PIDFILE" "$LOGFILE_REF" "$LOG_DIR/.parser-heartbeat-${SESSION_ID}"
    # Run end-of-session sweep to catch anything the live watcher missed
    python3 "$PARSER" "$SESSION_LOG" --sweep 2>/dev/null
    echo ""
    echo "Session ended. Captures saved to $COMPANION_CAPTURES_FILE"
}
trap cleanup EXIT

# Run Claude Code inside script to capture all terminal output
# -q suppresses "Script started/done" messages
# -F flushes on every write (prevents block-buffering lag for tail -f)
# All claude args are passed through (--continue, --resume, etc.)
script -q -F "$SESSION_LOG" claude "$@"
