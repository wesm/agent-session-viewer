"""Tests for main module export functionality."""

import pytest

from agent_session_viewer.main import (
    sanitize_filename,
    sanitize_role_class,
    sanitize_agent_class,
    escape_html,
    generate_export_html,
)


class TestSanitizeFilename:
    """Tests for filename sanitization for Content-Disposition header."""

    def test_normal_filename(self):
        """Normal filenames should pass through."""
        assert sanitize_filename("my-project-2025.html") == "my-project-2025.html"

    def test_removes_control_characters(self):
        """Control characters should be removed."""
        assert sanitize_filename("file\x00name.html") == "filename.html"
        assert sanitize_filename("file\x1fname.html") == "filename.html"
        assert sanitize_filename("file\x7fname.html") == "filename.html"

    def test_replaces_quotes(self):
        """Double quotes should be replaced with single quotes."""
        assert sanitize_filename('my "project".html') == "my 'project'.html"

    def test_replaces_backslash(self):
        """Backslashes should be replaced with underscores."""
        assert sanitize_filename("path\\file.html") == "path_file.html"

    def test_removes_newlines(self):
        """Newlines and carriage returns should be removed."""
        assert sanitize_filename("file\nname.html") == "filename.html"
        assert sanitize_filename("file\rname.html") == "filename.html"
        assert sanitize_filename("file\r\nname.html") == "filename.html"

    def test_combined_sanitization(self):
        """Multiple issues should all be handled."""
        assert sanitize_filename('bad\x00"file\\\r\n.html') == "bad'file_.html"


class TestSanitizeRoleClass:
    """Tests for role CSS class sanitization."""

    def test_allowed_roles(self):
        """Known roles should pass through."""
        assert sanitize_role_class("user") == "user"
        assert sanitize_role_class("assistant") == "assistant"

    def test_unknown_role(self):
        """Unknown roles should become 'unknown'."""
        assert sanitize_role_class("admin") == "unknown"
        assert sanitize_role_class("system") == "unknown"
        assert sanitize_role_class("") == "unknown"

    def test_injection_attempt(self):
        """Injection attempts should be blocked."""
        assert sanitize_role_class("user onclick=alert(1)") == "unknown"
        assert sanitize_role_class("<script>") == "unknown"


class TestSanitizeAgentClass:
    """Tests for agent CSS class sanitization."""

    def test_allowed_agents(self):
        """Known agents should pass through."""
        assert sanitize_agent_class("claude") == "claude"
        assert sanitize_agent_class("codex") == "codex"

    def test_unknown_agent(self):
        """Unknown agents should default to 'claude'."""
        assert sanitize_agent_class("gpt-4") == "claude"
        assert sanitize_agent_class("") == "claude"

    def test_injection_attempt(self):
        """Injection attempts should be blocked."""
        assert sanitize_agent_class("claude onclick=alert(1)") == "claude"


class TestEscapeHtml:
    """Tests for HTML escaping."""

    def test_escapes_special_chars(self):
        """HTML special characters should be escaped."""
        assert escape_html("<script>") == "&lt;script&gt;"
        assert escape_html('a & b "c"') == "a &amp; b &quot;c&quot;"

    def test_empty_string(self):
        """Empty string should return empty."""
        assert escape_html("") == ""
        assert escape_html(None) == ""

    def test_normal_text(self):
        """Normal text should pass through."""
        assert escape_html("Hello world") == "Hello world"


class TestGenerateExportHtml:
    """Tests for HTML export generation."""

    def test_preserves_custom_role_in_display(self):
        """Custom roles should be preserved in display text (escaped)."""
        session = {"project": "test", "agent": "claude", "message_count": 1}
        messages = [{"role": "custom-role", "content": "test", "timestamp": "2025-01-01T00:00:00Z"}]

        html = generate_export_html(session, messages)

        # CSS class should be sanitized to 'unknown'
        assert 'class="message unknown' in html
        # Display text should show the original (escaped)
        assert ">custom-role<" in html

    def test_preserves_custom_agent_in_display(self):
        """Custom agents should be preserved in display text (escaped)."""
        session = {"project": "test", "agent": "gpt-4", "message_count": 1}
        messages = []

        html = generate_export_html(session, messages)

        # CSS class should default to 'claude'
        assert 'class="agent-name claude"' in html
        # Display text should show the original
        assert ">gpt-4<" in html

    def test_escapes_malicious_role(self):
        """Malicious role values should be escaped in display."""
        session = {"project": "test", "agent": "claude", "message_count": 1}
        messages = [{"role": "<script>alert(1)</script>", "content": "test", "timestamp": ""}]

        html = generate_export_html(session, messages)

        # Should be escaped, not raw
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_url_encoding_in_filename(self):
        """Project names with special chars should produce valid filenames."""
        # This tests the sanitize_filename function indirectly
        filename = sanitize_filename("my project#1.html")
        assert '"' not in filename
        assert '\n' not in filename
