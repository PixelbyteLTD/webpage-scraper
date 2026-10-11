"""Tests for the cookies import module."""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from sitescrape.cookies import _parse_netscape_cookie_line, do_import_cookies


class TestParseNetscapeCookieLine:
    """Tests for _parse_netscape_cookie_line function."""

    def test_parses_standard_line(self):
        """A standard Netscape-format line should parse correctly."""
        line = ".example.com\tTRUE\t/\tTRUE\t0\tSESSION_ID\tabc123"
        result = _parse_netscape_cookie_line(line)
        assert result is not None
        assert result["name"] == "SESSION_ID"
        assert result["value"] == "abc123"
        assert result["domain"] == ".example.com"
        assert result["path"] == "/"
        assert result["secure"] is True
        assert result["httpOnly"] is True

    def test_parses_insecure_cookie(self):
        """Non-secure cookies should have secure=False."""
        line = ".example.com\tFALSE\t/\tFALSE\t0\ttoken\txyz789"
        result = _parse_netscape_cookie_line(line)
        assert result is not None
        assert result["secure"] is False
        assert result["httpOnly"] is False

    def test_rejects_short_line(self):
        """Lines with fewer than 7 fields should return None."""
        assert _parse_netscape_cookie_line("short\tline") is None
        assert _parse_netscape_cookie_line("only_three_fields_here") is None

    def test_case_insensitive_true_false(self):
        """TRUE/FALSE should be case-insensitive."""
        line_lower = ".example.com\ttrue\t/\ttrue\t0\tk\tv"
        line_upper = ".example.com\tTRUE\t/\tTRUE\t0\tk\tv"
        
        r1 = _parse_netscape_cookie_line(line_lower)
        r2 = _parse_netscape_cookie_line(line_upper)
        
        assert r1 is not None
        assert r2 is not None
        assert r1["secure"] is r2["secure"] is True

    def test_returns_same_site_none(self):
        """Parsed cookies should always have sameSite='None'."""
        line = ".example.com\tTRUE\t/\tTRUE\t0\tk\tv"
        result = _parse_netscape_cookie_line(line)
        assert result["sameSite"] == "None"

    def test_handles_explicit_none_values(self):
        """Cookie values that are empty strings should still parse."""
        line = ".example.com\tTRUE\t/\tTRUE\t0\tname\t"
        result = _parse_netscape_cookie_line(line)
        assert result is not None
        assert result["value"] == ""

    def test_handles_various_path_formats(self):
        """Various path formats should be preserved."""
        for path in ["/", "/api", "/app/", "/app/v2/users"]:
            line = f".example.com\tTRUE\t{path}\tTRUE\t0\tk\tv"
            result = _parse_netscape_cookie_line(line)
            assert result["path"] == path


class TestImportCookiesDoImportCookies:
    """Tests for do_import_cookies function."""

    def test_imports_cookies_from_file(self, tmp_path):
        """Cookies should be parsed and saved to state.json."""
        cookies_txt = tmp_path / "cookies.txt"
        auth_dir = tmp_path / "auth"
        
        cookies_txt.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.com\tTRUE\t/\tTRUE\t0\tSESSION\tabc123\n"
            ".example.com\tTRUE\t/\tFALSE\t0\tcsrf\txyz789\n",
            encoding="utf-8",
        )

        do_import_cookies(str(cookies_txt), auth_dir=str(auth_dir))

        state_file = auth_dir / "state.json"
        assert state_file.exists()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert len(state["cookies"]) == 2
        assert state["cookies"][0]["name"] == "SESSION"
        assert state["cookies"][1]["name"] == "csrf"

    def test_skips_comments(self, tmp_path):
        """Lines starting with # should be skipped."""
        cookies_txt = tmp_path / "cookies.txt"
        auth_dir = tmp_path / "auth"
        
        cookies_txt.write_text(
            "# Comment 1\n"
            "# Comment 2\n"
            ".example.com\tTRUE\t/\tTRUE\t0\tk\tv\n",
            encoding="utf-8",
        )

        do_import_cookies(str(cookies_txt), auth_dir=str(auth_dir))

        state_file = auth_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert len(state["cookies"]) == 1

    def test_skips_blank_lines(self, tmp_path):
        """Empty lines should be ignored."""
        cookies_txt = tmp_path / "cookies.txt"
        auth_dir = tmp_path / "auth"
        
        cookies_txt.write_text(
            "\n"
            ".example.com\tTRUE\t/\tTRUE\t0\tk1\tv1\n"
            "\n"
            ".example.com\tTRUE\t/\tTRUE\t0\tk2\tv2\n"
            "\n",
            encoding="utf-8",
        )

        do_import_cookies(str(cookies_txt), auth_dir=str(auth_dir))

        state_file = auth_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert len(state["cookies"]) == 2

    def test_missing_file_raises(self, tmp_path):
        """Importing a non-existent file should raise typer.Exit."""
        import typer

        with pytest.raises(typer.Exit):
            do_import_cookies("/nonexistent/file.txt", auth_dir=str(tmp_path / "auth"))

    def test_creates_auth_directory(self, tmp_path):
        """Auth directory should be created if it doesn't exist."""
        cookies_txt = tmp_path / "cookies.txt"
        auth_dir = tmp_path / "new_auth_dir"
        
        cookies_txt.write_text(
            ".example.com\tTRUE\t/\tTRUE\t0\tk\tv\n",
            encoding="utf-8",
        )

        assert not auth_dir.exists()
        do_import_cookies(str(cookies_txt), auth_dir=str(auth_dir))
        assert auth_dir.exists()

    def test_output_state_structure(self, tmp_path):
        """state.json should have the correct Playwright structure."""
        cookies_txt = tmp_path / "cookies.txt"
        auth_dir = tmp_path / "auth"
        
        cookies_txt.write_text(
            ".example.com\tTRUE\t/\tTRUE\t0\tk\tv\n",
            encoding="utf-8",
        )

        do_import_cookies(str(cookies_txt), auth_dir=str(auth_dir))

        state_file = auth_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        
        # Verify structure matches Playwright's expected format
        assert "cookies" in state
        assert "expires" in state
        assert "domain" in state
        assert "path" in state
        assert state["path"] == "/"

    def test_empty_cookies_file(self, tmp_path):
        """An empty cookie file should produce an empty cookies list."""
        cookies_txt = tmp_path / "cookies.txt"
        auth_dir = tmp_path / "auth"
        
        cookies_txt.write_text("# Only comments\n# And blank lines\n", encoding="utf-8")

        do_import_cookies(str(cookies_txt), auth_dir=str(auth_dir))

        state_file = auth_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert len(state["cookies"]) == 0

    def test_multiple_domains(self, tmp_path):
        """Cookies from different domains should all be preserved."""
        cookies_txt = tmp_path / "cookies.txt"
        auth_dir = tmp_path / "auth"
        
        cookies_txt.write_text(
            ".domain1.com\tTRUE\t/\tTRUE\t0\tk1\tv1\n"
            ".domain2.com\tFALSE\t/\tFALSE\t0\tk2\tv2\n"
            ".domain3.com\tTRUE\t/api\tTRUE\t0\tk3\tv3\n",
            encoding="utf-8",
        )

        do_import_cookies(str(cookies_txt), auth_dir=str(auth_dir))

        state_file = auth_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert len(state["cookies"]) == 3


class TestNetscapeFormatEdgeCases:
    """Edge cases for Netscape format parsing."""

    def test_whitespace_in_cookie_value(self):
        """Whitespace in cookie values should be preserved."""
        line = ".example.com\tTRUE\t/\tTRUE\t0\tk\thello world"
        result = _parse_netscape_cookie_line(line)
        assert result["value"] == "hello world"

    def test_unicode_cookie_value(self):
        """Unicode characters in cookie values should be preserved."""
        line = ".example.com\tTRUE\t/\tTRUE\t0\tk\tcafé"
        result = _parse_netscape_cookie_line(line)
        assert result["value"] == "café"

    def test_url_encoded_value(self):
        """URL-encoded values should be preserved as-is."""
        line = ".example.com\tTRUE\t/\tTRUE\t0\ttoken\tabc%3Ddef"
        result = _parse_netscape_cookie_line(line)
        assert result["value"] == "abc%3Ddef"

    def test_long_cookie_values(self):
        """Very long cookie values should be handled."""
        long_value = "x" * 10000
        line = f".example.com\tTRUE\t/\tTRUE\t0\tk\t{long_value}"
        result = _parse_netscape_cookie_line(line)
        assert result["value"] == long_value

    def test_special_characters_in_name(self):
        """Cookie names with special characters should work."""
        line = ".example.com\tTRUE\t/\tTRUE\t0\tsess-id_123\tvalue"
        result = _parse_netscape_cookie_line(line)
        assert result["name"] == "sess-id_123"
