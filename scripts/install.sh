#!/usr/bin/env bash
# install.sh — Idempotent installer for companion-capture.
# Creates config dir, wires shell alias, configures PostToolUse hook, validates Python.
#
# Usage:
#   bash companion-capture/scripts/install.sh
#
# Idempotent: every step checks before acting. Running twice produces same result.

PREFIX="[companion-capture]"

# Resolve install directory from script location
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"

# Track what happened for the summary
ACTIONS=()
SKIPPED=()

# --- 1. Shell detection ---

case "$SHELL" in
    */zsh)  RC_FILE="$HOME/.zshrc" ;;
    */bash) RC_FILE="$HOME/.bashrc" ;;
    *)
        echo "$PREFIX Error: unsupported shell '$SHELL'. Only bash and zsh are supported."
        exit 1
        ;;
esac
echo "$PREFIX Detected shell: $(basename "$SHELL") (rc: $RC_FILE)"

# --- 2. Python validation ---

if ! command -v python3 >/dev/null 2>&1; then
    echo "$PREFIX Error: python3 not found. Please install Python 3.9 or later."
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ -z "$PY_VERSION" ] || [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    echo "$PREFIX Error: Python 3.9+ required, found ${PY_VERSION:-unknown}."
    exit 1
fi

echo "$PREFIX ✓ Python $PY_VERSION detected"
ACTIONS+=("Python $PY_VERSION validated")

# --- 3. Config directory + skeleton config ---

CONFIG_DIR="$HOME/.companion-capture"
CONFIG_FILE="$CONFIG_DIR/config.json"

if ! mkdir -p "$CONFIG_DIR"; then
    echo "$PREFIX Error: cannot create config directory $CONFIG_DIR"
    exit 1
fi

if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" << 'CONFIGEOF'
{
  "companion_name": "Companion",
  "output_dir": "~/.claude/",
  "log_dir": "~/.companion-capture/logs/",
  "rotation_days": 7,
  "archive_days": 90,
  "debug": false
}
CONFIGEOF
    echo "$PREFIX ✓ Created $CONFIG_FILE"
    ACTIONS+=("Created config.json")
else
    echo "$PREFIX → Config already exists at $CONFIG_FILE (skipped)"
    SKIPPED+=("config.json (already exists)")
fi

# Create logs subdirectory
if ! mkdir -p "$CONFIG_DIR/logs"; then
    echo "$PREFIX Error: cannot create logs directory $CONFIG_DIR/logs"
    exit 1
fi

# --- 4. Symlink scripts into fixed bin directory ---
# Alias and hook always point to ~/.companion-capture/bin/ (stable paths).
# Symlinks resolve to the actual repo clone location.
# Moving the repo only requires re-running install.sh to update symlinks.

BIN_DIR="$CONFIG_DIR/bin"
if ! mkdir -p "$BIN_DIR"; then
    echo "$PREFIX Error: cannot create bin directory $BIN_DIR"
    exit 1
fi

SYMLINK_UPDATED=0
for script in wrapper.sh check.sh archive.sh; do
    TARGET="$INSTALL_DIR/scripts/$script"
    LINK="$BIN_DIR/$script"
    if [ ! -f "$TARGET" ]; then
        echo "$PREFIX Error: expected script not found: $TARGET"
        exit 1
    fi
    if [ -L "$LINK" ]; then
        EXISTING=$(readlink "$LINK")
        if [ "$EXISTING" = "$TARGET" ]; then
            continue
        fi
        # Symlink exists but points elsewhere — update it
        if ! ln -sf "$TARGET" "$LINK"; then
            echo "$PREFIX Error: failed to update symlink $LINK"
            exit 1
        fi
        SYMLINK_UPDATED=1
    else
        if ! ln -s "$TARGET" "$LINK"; then
            echo "$PREFIX Error: failed to create symlink $LINK"
            exit 1
        fi
        SYMLINK_UPDATED=1
    fi
done

if [ "$SYMLINK_UPDATED" -eq 1 ]; then
    echo "$PREFIX ✓ Symlinked scripts into $BIN_DIR"
    ACTIONS+=("Symlinked scripts to $BIN_DIR")
else
    echo "$PREFIX → Script symlinks already current (skipped)"
    SKIPPED+=("Script symlinks (already current)")
fi

# --- 5. Shell alias ---

ALIAS_LINE="alias claude='$BIN_DIR/wrapper.sh'"

if [ -f "$RC_FILE" ] && grep -qF "companion-capture" "$RC_FILE" && grep -qF "wrapper.sh" "$RC_FILE"; then
    # Check if alias already points to the bin dir
    if grep -qF "$BIN_DIR/wrapper.sh" "$RC_FILE"; then
        echo "$PREFIX → Alias already present in $RC_FILE (skipped)"
        SKIPPED+=("Shell alias (already present)")
    else
        # Old alias pointing to repo directly — update it
        # Remove old line and add new one
        TMPFILE=$(mktemp)
        while IFS= read -r line; do
            if echo "$line" | grep -qF "companion-capture" && echo "$line" | grep -qF "wrapper.sh"; then
                echo "$ALIAS_LINE" >> "$TMPFILE"
            else
                echo "$line" >> "$TMPFILE"
            fi
        done < "$RC_FILE"
        mv "$TMPFILE" "$RC_FILE"
        echo "$PREFIX ✓ Updated alias in $RC_FILE to use stable path"
        ACTIONS+=("Updated shell alias to stable bin path")
    fi
else
    # Ensure RC_FILE exists
    touch "$RC_FILE"
    printf '\n# companion-capture\n%s\n' "$ALIAS_LINE" >> "$RC_FILE"
    echo "$PREFIX ✓ Added alias to $RC_FILE"
    ACTIONS+=("Added shell alias to $RC_FILE")
fi

# --- 6. PostToolUse hook in settings.json ---

SETTINGS_FILE="$HOME/.claude/settings.json"
HOOK_CMD="bash $BIN_DIR/check.sh"

# Ensure ~/.claude/ exists
mkdir -p "$HOME/.claude"

HOOK_RESULT=$(python3 -c "
import json
import sys
from pathlib import Path

settings_path = Path('$SETTINGS_FILE')
hook_cmd = '''$HOOK_CMD'''

# Build the hook entry
hook_entry = {
    'matcher': 'Bash|Edit|Write|MultiEdit|Read',
    'hooks': [
        {
            'type': 'command',
            'command': hook_cmd
        }
    ]
}

# Load or create settings
if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, ValueError):
        print('ERROR', end='')
        sys.exit(0)
else:
    settings = {}

if not isinstance(settings, dict):
    print('ERROR', end='')
    sys.exit(0)

# Navigate to hooks.PostToolUse, creating as needed
if 'hooks' not in settings:
    settings['hooks'] = {}
if 'PostToolUse' not in settings['hooks']:
    settings['hooks']['PostToolUse'] = []

post_hooks = settings['hooks']['PostToolUse']

# Check if companion-capture hook already exists (and whether it needs updating)
existing_idx = None
needs_update = False
for i, entry in enumerate(post_hooks):
    hooks_list = entry.get('hooks', [])
    for h in hooks_list:
        cmd = h.get('command', '')
        if 'companion-capture' in cmd and 'check.sh' in cmd:
            existing_idx = i
            if cmd != hook_cmd:
                needs_update = True
            break
    if existing_idx is not None:
        break

if existing_idx is not None and not needs_update:
    print('SKIPPED', end='')
elif existing_idx is not None and needs_update:
    post_hooks[existing_idx] = hook_entry
    settings_path.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
    print('UPDATED', end='')
else:
    post_hooks.append(hook_entry)
    settings_path.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
    print('ADDED', end='')
" 2>/dev/null)

case "$HOOK_RESULT" in
    ADDED)
        echo "$PREFIX ✓ PostToolUse hook added to settings.json"
        ACTIONS+=("PostToolUse hook configured")
        ;;
    UPDATED)
        echo "$PREFIX ✓ PostToolUse hook updated to use stable bin path"
        ACTIONS+=("PostToolUse hook updated to stable path")
        ;;
    SKIPPED)
        echo "$PREFIX → PostToolUse hook already present in settings.json (skipped)"
        SKIPPED+=("PostToolUse hook (already present)")
        ;;
    ERROR)
        echo "$PREFIX Error: ~/.claude/settings.json contains invalid JSON. Fix it manually and re-run."
        ;;
    *)
        echo "$PREFIX Error: failed to configure PostToolUse hook."
        ;;
esac

# --- 7. CLAUDE.md companion instructions ---

CLAUDE_MD="$HOME/.claude/CLAUDE.md"

SNIPPET_RESULT=$(python3 -c "
from pathlib import Path
from companion_capture.claude_md_snippet import generate_snippet, snippet_present

claude_md = Path('$CLAUDE_MD')
companion_name = '''${COMPANION_NAME:-Companion}'''

if claude_md.exists():
    content = claude_md.read_text(encoding='utf-8')
    if snippet_present(content):
        print('SKIPPED', end='')
    else:
        snippet = generate_snippet(companion_name)
        content = content.rstrip() + '\n\n' + snippet + '\n'
        claude_md.write_text(content, encoding='utf-8')
        print('ADDED', end='')
else:
    snippet = generate_snippet(companion_name)
    claude_md.write_text(snippet + '\n', encoding='utf-8')
    print('CREATED', end='')
" 2>/dev/null)

case "$SNIPPET_RESULT" in
    ADDED)
        echo "$PREFIX ✓ Companion instructions added to ~/.claude/CLAUDE.md"
        ACTIONS+=("CLAUDE.md companion instructions added")
        ;;
    CREATED)
        echo "$PREFIX ✓ Created ~/.claude/CLAUDE.md with companion instructions"
        ACTIONS+=("CLAUDE.md created with companion instructions")
        ;;
    SKIPPED)
        echo "$PREFIX → Companion instructions already in ~/.claude/CLAUDE.md (skipped)"
        SKIPPED+=("CLAUDE.md instructions (already present)")
        ;;
    *)
        echo "$PREFIX Warning: failed to add companion instructions to CLAUDE.md."
        ;;
esac

# --- 8. Summary ---

echo ""
echo "$PREFIX ─── Installation complete ───"
echo ""

if [ ${#ACTIONS[@]} -gt 0 ]; then
    echo "$PREFIX Actions taken:"
    for a in "${ACTIONS[@]}"; do
        echo "$PREFIX   ✓ $a"
    done
fi

if [ ${#SKIPPED[@]} -gt 0 ]; then
    echo "$PREFIX Already configured:"
    for s in "${SKIPPED[@]}"; do
        echo "$PREFIX   → $s"
    done
fi

echo ""
echo "$PREFIX To activate the alias in your current shell, run:"
echo "$PREFIX   source $RC_FILE"
