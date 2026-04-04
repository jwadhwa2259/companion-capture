#!/usr/bin/env bash
# check.sh — PostToolUse hook for companion-capture.
# Sends SIGUSR1 to flush parser, checks heartbeat, surfaces new captures to Claude.
# Ported from keel-check.sh in Section 2.
#
# Called by a Claude Code PostToolUse hook.
# Checks both captures (vibes) and debug markdown files.
# Only surfaces entries matching the current project (prevents cross-session leaking).
# Skips entirely if neither file has been modified since last check (mtime guard).
# Markers are project-scoped so multi-session reads don't skip each other's entries.

CAPTURES_FILE="${COMPANION_CAPTURES_FILE:-$HOME/.claude/Companion-captures.md}"
DEBUG_FILE="${COMPANION_DEBUG_FILE:-$HOME/.claude/Companion-debug.md}"
LOG_DIR="${COMPANION_LOG_DIR:-$HOME/.companion-capture/logs}"
NAME="${COMPANION_NAME:-Companion}"

# Current project name — must match the tag in entries to be surfaced
PROJECT=$(basename "${PWD}")

# Signal ALL running parsers to flush pending messages before we read
for pidfile in "$LOG_DIR"/.parser-*.pid; do
    [ -f "$pidfile" ] || continue
    PARSER_PID=$(cat "$pidfile")
    PARSER_PID=${PARSER_PID:-0}
    if kill -0 "$PARSER_PID" 2>/dev/null; then
        kill -USR1 "$PARSER_PID" 2>/dev/null
    fi
done
sleep 0.2  # brief pause for flush to hit disk

# Check session-specific parser heartbeat — warn if stale (>600s / 10min) or missing
if [ -n "$COMPANION_SESSION_ID" ]; then
    HEARTBEAT="$LOG_DIR/.parser-heartbeat-${COMPANION_SESSION_ID}"
    if [ -f "$HEARTBEAT" ]; then
        BEAT_AGE=$(( $(date +%s) - $(stat -f %m "$HEARTBEAT" 2>/dev/null || echo 0) ))
        if [ "$BEAT_AGE" -gt 600 ]; then
            echo "[Warning: $NAME parser heartbeat is ${BEAT_AGE}s stale — it may have crashed]"
        fi
    fi
fi

# --- Check a single capture file for new entries ---
check_file() {
    local FILE="$1"
    local LABEL="$2"
    # Project-scoped markers so sessions don't advance each other's counters
    local MARKER="$LOG_DIR/.last-read-$(basename "$FILE")-${PROJECT}"
    local MTIME_MARKER="$LOG_DIR/.last-mtime-$(basename "$FILE")-${PROJECT}"

    [ -f "$FILE" ] || return

    # Fast path: skip if mtime unchanged
    local CURRENT_MTIME
    CURRENT_MTIME=$(stat -f %m "$FILE" 2>/dev/null)
    local LAST_MTIME=0
    [ -f "$MTIME_MARKER" ] && LAST_MTIME=$(cat "$MTIME_MARKER")
    LAST_MTIME=${LAST_MTIME:-0}
    [ "$CURRENT_MTIME" = "$LAST_MTIME" ] && return

    # File changed — check for new lines, filtered to current project
    local TOTAL
    TOTAL=$(wc -l < "$FILE")
    local LAST=0
    [ -f "$MARKER" ] && LAST=$(cat "$MARKER")
    LAST=${LAST:-0}
    # Validate as integer — corrupted markers default to 0 (re-read all)
    case "$LAST" in
        ''|*[!0-9]*) LAST=0 ;;
    esac

    if [ "$TOTAL" -gt "$LAST" ]; then
        local NEW
        # -F for literal match — project names with regex chars ([], () etc) are safe
        NEW=$(tail -n +"$((LAST + 1))" "$FILE" | grep -E '^\- `\[(debug|vibe)\]` `[0-9]{2}:[0-9]{2}` ' | grep -F "\`${PROJECT}\`")
        if [ -n "$NEW" ]; then
            echo "[$NAME ${LABEL}]"
            echo "$NEW"
        fi
    fi

    echo "$TOTAL" > "$MARKER"
    echo "$CURRENT_MTIME" > "$MTIME_MARKER"
}

check_file "$CAPTURES_FILE" "vibe"
check_file "$DEBUG_FILE" "debug"
