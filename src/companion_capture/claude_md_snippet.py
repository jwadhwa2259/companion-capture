"""CLAUDE.md snippet template for companion-capture.

Generates the instructions that teach Claude how to interact with
companion-capture's output files. Installed into ~/.claude/CLAUDE.md
by install.sh and removed by uninstall.sh.
"""

# Sentinel comments that bracket the managed section
START_MARKER = "<!-- companion-capture:start -->"
END_MARKER = "<!-- companion-capture:end -->"


def generate_snippet(companion_name: str) -> str:
    """Return the CLAUDE.md section for the given companion name."""
    name_lower = companion_name.lower()
    captures = f"{companion_name}-captures.md"
    debug = f"{companion_name}-debug.md"

    return f"""{START_MARKER}
## {companion_name} Companion

A small companion character sits beside the input box and occasionally comments in a speech bubble. Its messages are automatically captured to markdown files.

- **When the user asks "what did {name_lower} say?" or similar**, read BOTH `~/.claude/{captures}` and `~/.claude/{debug}`. Show the latest entries from each. These files are the source of truth for the companion's speech bubble messages. Always read them — never say you can't see what the companion said.
- **Act on findings, don't just summarize.** The companion's observations often flag real edge cases, missing error handling, or untested code paths. When you read a new entry, evaluate whether it's actionable — check the code, test the edge case, propose or apply a fix. Treat companion observations the same as if the user had pointed out the issue directly.
- **`[vibe]` entries** (in `{captures}`) are observations and reactions — persistent, rotated to archive after 7 days.
- **`[debug]` entries** (in `{debug}`) are technical concerns and edge case flags — reset each session.
- Auto-captured messages are surfaced in real-time by the PostToolUse hook. When new entries appear in tool output, acknowledge and evaluate them.
{END_MARKER}"""


def snippet_present(claude_md_content: str) -> bool:
    """Check if the companion-capture snippet is already in the content."""
    return START_MARKER in claude_md_content and END_MARKER in claude_md_content


def remove_snippet(claude_md_content: str) -> str:
    """Remove the companion-capture snippet from CLAUDE.md content."""
    start = claude_md_content.find(START_MARKER)
    end = claude_md_content.find(END_MARKER)
    if start == -1 or end == -1:
        return claude_md_content

    end += len(END_MARKER)
    # Remove trailing newlines after the block
    while end < len(claude_md_content) and claude_md_content[end] == "\n":
        end += 1

    # Remove leading newlines before the block
    while start > 0 and claude_md_content[start - 1] == "\n":
        start -= 1
    if start > 0:
        start += 1  # Keep one newline as separator

    return claude_md_content[:start] + claude_md_content[end:]
