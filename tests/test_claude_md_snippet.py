"""Tests for companion_capture.claude_md_snippet."""

from companion_capture.claude_md_snippet import (
    START_MARKER,
    END_MARKER,
    generate_snippet,
    snippet_present,
    remove_snippet,
)


class TestGenerateSnippet:
    def test_contains_markers(self) -> None:
        snippet = generate_snippet("Keel")
        assert snippet.startswith(START_MARKER)
        assert snippet.endswith(END_MARKER)

    def test_uses_companion_name(self) -> None:
        snippet = generate_snippet("Goose")
        assert "## Goose Companion" in snippet
        assert "Goose-captures.md" in snippet
        assert "Goose-debug.md" in snippet

    def test_lowercase_name_in_prompt(self) -> None:
        snippet = generate_snippet("Keel")
        assert "what did keel say?" in snippet

    def test_contains_act_on_findings(self) -> None:
        snippet = generate_snippet("Keel")
        assert "Act on findings" in snippet
        assert "don't just summarize" in snippet

    def test_contains_both_file_types(self) -> None:
        snippet = generate_snippet("Keel")
        assert "[vibe]" in snippet
        assert "[debug]" in snippet


class TestSnippetPresent:
    def test_present(self) -> None:
        content = f"# My CLAUDE.md\n\n{generate_snippet('Keel')}\n"
        assert snippet_present(content) is True

    def test_not_present(self) -> None:
        content = "# My CLAUDE.md\n\nSome other content\n"
        assert snippet_present(content) is False

    def test_partial_markers(self) -> None:
        content = f"# My CLAUDE.md\n\n{START_MARKER}\n"
        assert snippet_present(content) is False


class TestRemoveSnippet:
    def test_removes_snippet(self) -> None:
        snippet = generate_snippet("Keel")
        content = f"# Header\n\n{snippet}\n\n## Footer\n"
        cleaned = remove_snippet(content)
        assert START_MARKER not in cleaned
        assert END_MARKER not in cleaned
        assert "# Header" in cleaned
        assert "## Footer" in cleaned

    def test_no_snippet_unchanged(self) -> None:
        content = "# My CLAUDE.md\n\nNo snippet here\n"
        assert remove_snippet(content) == content

    def test_removes_cleanly_at_end(self) -> None:
        snippet = generate_snippet("Keel")
        content = f"# Header\n\nSome rules\n\n{snippet}\n"
        cleaned = remove_snippet(content)
        assert cleaned.strip() == "# Header\n\nSome rules"

    def test_idempotent(self) -> None:
        snippet = generate_snippet("Keel")
        content = f"# Header\n\n{snippet}\n"
        cleaned = remove_snippet(content)
        assert remove_snippet(cleaned) == cleaned
