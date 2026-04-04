#!/usr/bin/env bash
# archive.sh — Capture rotation for companion-capture.
# Moves captures older than rotation_days to archive, prunes archive at archive_days.
# Ported from keel-archive.sh in Section 2.
#
# Run from wrapper.sh at session start.

CAPTURES_FILE="${COMPANION_CAPTURES_FILE:-$HOME/.claude/Companion-captures.md}"
ARCHIVE_FILE="${COMPANION_ARCHIVE_FILE:-$HOME/.claude/Companion-archive.md}"
LOG_DIR="${COMPANION_LOG_DIR:-$HOME/.companion-capture/logs}"
NAME="${COMPANION_NAME:-Companion}"
OUTPUT_DIR="${COMPANION_OUTPUT_DIR:-$HOME/.claude}"
ROTATION_DAYS="${COMPANION_ROTATION_DAYS:-7}"
ARCHIVE_DAYS="${COMPANION_ARCHIVE_DAYS:-90}"

# Validate numeric values — reject non-integer input from env vars
case "$ROTATION_DAYS" in ''|*[!0-9]*) ROTATION_DAYS=7 ;; esac
case "$ARCHIVE_DAYS" in ''|*[!0-9]*) ARCHIVE_DAYS=90 ;; esac

# Clean up session logs older than 3 days + stale .tmp files from interrupted writes
if [ -d "$LOG_DIR" ]; then
    find "$LOG_DIR" -name "session_*.log" -mtime +3 -delete 2>/dev/null
fi
CAPTURES_DIR=$(dirname "$CAPTURES_FILE")
rm -f "$CAPTURES_DIR/${NAME}-captures.tmp" "$CAPTURES_DIR/${NAME}-archive.tmp"

# Prune archive entries older than archive_days
if [ -f "$ARCHIVE_FILE" ]; then
    ARCHIVE_CUTOFF=$(date -v-${ARCHIVE_DAYS}d +%Y-%m-%d 2>/dev/null || date -d "${ARCHIVE_DAYS} days ago" +%Y-%m-%d 2>/dev/null)
    if [ -n "$ARCHIVE_CUTOFF" ]; then
        python3 -c "
import os
from pathlib import Path
from collections import Counter

name = os.environ.get('COMPANION_NAME', 'Companion')
output_dir = os.environ.get('COMPANION_OUTPUT_DIR', os.path.expanduser('~/.claude'))

archive = Path('$ARCHIVE_FILE')
companion_md = Path(output_dir) / f'{name}.md'
cutoff = '$ARCHIVE_CUTOFF'

lines = archive.read_text().splitlines()
keep, prune = [], []
include = True
for line in lines:
    if line.startswith('### '):
        date = line.replace('### ', '').strip()
        include = date >= cutoff
    if include:
        keep.append(line)
    else:
        prune.append(line)

# Summarize what's being pruned before discarding
debug_count = sum(1 for l in prune if '[debug]' in l)
vibe_count = sum(1 for l in prune if '[vibe]' in l)
if debug_count + vibe_count > 0:
    # Extract top keywords from debug entries
    from re import findall
    words = []
    for l in prune:
        if '[debug]' in l:
            sep = l.find(' — ')
            if sep != -1:
                words.extend(w.lower() for w in findall(r'[a-z]{4,}', l[sep:]))
    top = [w for w, _ in Counter(words).most_common(5)]
    summary = f'- **Digest (pruned {cutoff} and older):** {debug_count} debug, {vibe_count} vibe. Themes: {\", \".join(top)}.'
    # Append digest to companion .md log section
    if companion_md.exists():
        with open(companion_md, 'a') as f:
            f.write(summary + '\n')

# Atomic rewrite: write to temp, then rename
tmp = archive.with_suffix('.tmp')
tmp.write_text('\n'.join(keep).rstrip() + '\n')
tmp.rename(archive)
" 2>/dev/null
    fi
fi

[ -f "$CAPTURES_FILE" ] || exit 0

CUTOFF=$(date -v-${ROTATION_DAYS}d +%Y-%m-%d 2>/dev/null || date -d "${ROTATION_DAYS} days ago" +%Y-%m-%d 2>/dev/null)
[ -z "$CUTOFF" ] && exit 0

# Initialize archive if needed
[ -f "$ARCHIVE_FILE" ] || echo "# ${NAME} — Archive" > "$ARCHIVE_FILE"

python3 - "$CAPTURES_FILE" "$ARCHIVE_FILE" "$CUTOFF" << 'PYEOF'
import sys
from pathlib import Path

captures_path = Path(sys.argv[1])
archive_path = Path(sys.argv[2])
cutoff = sys.argv[3]

content = captures_path.read_text()

# Split into sections at ### date headers
lines = content.split("\n")
header_lines = []
sections = []  # (date_str, section_lines)
current_date = None
current_lines = []

in_log = False
for line in lines:
    if line.startswith("## Log"):
        in_log = True
        header_lines.append(line)
        continue

    if not in_log:
        header_lines.append(line)
        continue

    if line.startswith("### "):
        if current_date:
            sections.append((current_date, current_lines))
        current_date = line.replace("### ", "").strip()
        current_lines = [line]
    else:
        current_lines.append(line)

if current_date:
    sections.append((current_date, current_lines))

# Split into keep vs archive
keep = []
archive = []
for date_str, sec_lines in sections:
    if date_str >= cutoff:
        keep.append(sec_lines)
    else:
        archive.append(sec_lines)

if not archive:
    sys.exit(0)

# Append old sections to archive
archive_content = archive_path.read_text().rstrip()
for sec in archive:
    archive_content += "\n\n" + "\n".join(sec)
# Atomic rewrite: temp file then rename
tmp = archive_path.with_suffix('.tmp')
tmp.write_text(archive_content.rstrip() + "\n")
tmp.rename(archive_path)

# Rewrite captures — also atomic
new_content = "\n".join(header_lines) + "\n"
for sec in keep:
    new_content += "\n" + "\n".join(sec)
tmp = captures_path.with_suffix('.tmp')
tmp.write_text(new_content.rstrip() + "\n")
tmp.rename(captures_path)

archived_count = sum(1 for sec in archive for line in sec if line.startswith("- "))
print(f"Archived {archived_count} entries older than {cutoff}")
PYEOF
