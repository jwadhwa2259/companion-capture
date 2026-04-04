#!/usr/bin/env bash
# uninstall.sh — Clean removal of companion-capture.
# Reverses install.sh: removes alias, hook, config dir (with confirmation).
#
# Usage:
#   bash companion-capture/scripts/uninstall.sh
#
# Does NOT remove the companion-capture source directory itself.

PREFIX="[companion-capture]"

ACTIONS=()
SKIPPED=()

# --- 1. Shell detection ---

case "$SHELL" in
    */zsh)  RC_FILE="$HOME/.zshrc" ;;
    */bash) RC_FILE="$HOME/.bashrc" ;;
    *)
        echo "$PREFIX Warning: unsupported shell '$SHELL'. Skipping alias removal."
        RC_FILE=""
        ;;
esac

# --- 2. Remove shell alias ---

if [ -n "$RC_FILE" ] && [ -f "$RC_FILE" ]; then
    if grep -qF "companion-capture" "$RC_FILE" && grep -qF "wrapper.sh" "$RC_FILE"; then
        # Remove the alias line and the comment marker above it
        # Handles both old (direct repo path) and new (bin/ symlink) alias formats
        TMPFILE=$(mktemp)
        SKIP_NEXT=0
        while IFS= read -r line; do
            # If this line is the comment marker, check if next line is the alias
            if [ "$line" = "# companion-capture" ]; then
                SKIP_NEXT=1
                continue
            fi
            if [ "$SKIP_NEXT" -eq 1 ]; then
                SKIP_NEXT=0
                # Check if this is the alias line — skip it
                if echo "$line" | grep -qF "companion-capture" && echo "$line" | grep -qF "wrapper.sh"; then
                    continue
                else
                    # The comment was there but next line isn't the alias; preserve both
                    echo "# companion-capture" >> "$TMPFILE"
                    echo "$line" >> "$TMPFILE"
                    continue
                fi
            fi
            # Also catch alias lines without the comment marker above
            if echo "$line" | grep -qF "companion-capture" && echo "$line" | grep -qF "wrapper.sh"; then
                continue
            fi
            echo "$line" >> "$TMPFILE"
        done < "$RC_FILE"
        mv "$TMPFILE" "$RC_FILE"
        echo "$PREFIX ✓ Removed alias from $RC_FILE"
        ACTIONS+=("Removed alias from $RC_FILE")
    else
        echo "$PREFIX → No alias found in $RC_FILE (skipped)"
        SKIPPED+=("Shell alias (not found)")
    fi
else
    echo "$PREFIX → Shell rc file not found, skipping alias removal"
    SKIPPED+=("Shell alias (rc file not found)")
fi

# --- 3. Remove PostToolUse hook from settings.json ---

SETTINGS_FILE="$HOME/.claude/settings.json"

if [ -f "$SETTINGS_FILE" ]; then
    HOOK_RESULT=$(python3 -c "
import json
import sys
from pathlib import Path

settings_path = Path('$SETTINGS_FILE')
try:
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
except (json.JSONDecodeError, ValueError):
    print('ERROR', end='')
    sys.exit(0)

post_hooks = settings.get('hooks', {}).get('PostToolUse', [])
if not post_hooks:
    print('NOT_FOUND', end='')
    sys.exit(0)

# Filter out any hook entry where a command contains companion-capture
original_count = len(post_hooks)
filtered = []
for entry in post_hooks:
    hooks_list = entry.get('hooks', [])
    is_companion = False
    for h in hooks_list:
        cmd = h.get('command', '')
        if 'companion-capture' in cmd and 'check.sh' in cmd:
            is_companion = True
            break
    if not is_companion:
        filtered.append(entry)

if len(filtered) == original_count:
    print('NOT_FOUND', end='')
    sys.exit(0)

settings['hooks']['PostToolUse'] = filtered

# Clean up empty structures
if not settings['hooks']['PostToolUse']:
    del settings['hooks']['PostToolUse']
if not settings['hooks']:
    del settings['hooks']

settings_path.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
print('REMOVED', end='')
" 2>/dev/null)

    case "$HOOK_RESULT" in
        REMOVED)
            echo "$PREFIX ✓ Removed PostToolUse hook from settings.json"
            ACTIONS+=("Removed PostToolUse hook")
            ;;
        NOT_FOUND)
            echo "$PREFIX → No companion-capture hook found in settings.json (skipped)"
            SKIPPED+=("PostToolUse hook (not found)")
            ;;
        ERROR)
            echo "$PREFIX Warning: settings.json contains invalid JSON. Hook not removed."
            ;;
        *)
            echo "$PREFIX Warning: failed to process settings.json."
            ;;
    esac
else
    echo "$PREFIX → No settings.json found (skipped)"
    SKIPPED+=("PostToolUse hook (settings.json not found)")
fi

# --- 4. Remove CLAUDE.md companion instructions ---

CLAUDE_MD="$HOME/.claude/CLAUDE.md"

if [ -f "$CLAUDE_MD" ]; then
    SNIPPET_RESULT=$(python3 -c "
from pathlib import Path
from companion_capture.claude_md_snippet import snippet_present, remove_snippet

claude_md = Path('$CLAUDE_MD')
content = claude_md.read_text(encoding='utf-8')
if snippet_present(content):
    cleaned = remove_snippet(content)
    claude_md.write_text(cleaned, encoding='utf-8')
    print('REMOVED', end='')
else:
    print('NOT_FOUND', end='')
" 2>/dev/null)

    case "$SNIPPET_RESULT" in
        REMOVED)
            echo "$PREFIX ✓ Removed companion instructions from ~/.claude/CLAUDE.md"
            ACTIONS+=("Removed CLAUDE.md instructions")
            ;;
        NOT_FOUND)
            echo "$PREFIX → No companion instructions in ~/.claude/CLAUDE.md (skipped)"
            SKIPPED+=("CLAUDE.md instructions (not found)")
            ;;
        *)
            echo "$PREFIX Warning: failed to remove companion instructions from CLAUDE.md."
            ;;
    esac
else
    echo "$PREFIX → No ~/.claude/CLAUDE.md found (skipped)"
    SKIPPED+=("CLAUDE.md instructions (file not found)")
fi

# --- 5. Config directory removal (with confirmation) ---

CONFIG_DIR="$HOME/.companion-capture"

if [ -d "$CONFIG_DIR" ]; then
    echo ""
    echo "$PREFIX Config directory: $CONFIG_DIR"
    echo "$PREFIX This contains your config.json, logs, and runtime files."
    read -p "$PREFIX Delete $CONFIG_DIR and all contents? [y/N] " CONFIRM
    case "$CONFIRM" in
        [yY]|[yY][eE][sS])
            rm -rf "$CONFIG_DIR"
            echo "$PREFIX ✓ Removed $CONFIG_DIR"
            ACTIONS+=("Removed config directory")
            ;;
        *)
            echo "$PREFIX → Kept $CONFIG_DIR"
            SKIPPED+=("Config directory (user declined)")
            ;;
    esac
else
    echo "$PREFIX → No config directory at $CONFIG_DIR (skipped)"
    SKIPPED+=("Config directory (not found)")
fi

# --- 5. Summary ---

echo ""
echo "$PREFIX ─── Uninstall complete ───"
echo ""

if [ ${#ACTIONS[@]} -gt 0 ]; then
    echo "$PREFIX Actions taken:"
    for a in "${ACTIONS[@]}"; do
        echo "$PREFIX   ✓ $a"
    done
fi

if [ ${#SKIPPED[@]} -gt 0 ]; then
    echo "$PREFIX Already clean:"
    for s in "${SKIPPED[@]}"; do
        echo "$PREFIX   → $s"
    done
fi

echo ""
echo "$PREFIX To apply changes in your current shell, run:"
echo "$PREFIX   source $RC_FILE"
echo "$PREFIX Note: capture files in ~/.claude/ were not removed."
