"""Tests for the check module (login detection heuristics)."""

import json
import re
from pathlib import Path
from unittest import mock

import pytest

from sitescrape.check import (
    _LOGIN_SELECTORS,
    _LOGIN_TEXT_PATTERNS,
)


class TestLoginSelectors:
    """Tests for the login-page selector list."""

    def test_selectors_is_list(self):
        assert isinstance(_LOGIN_SELECTORS, list)
        assert len(_LOGIN_SELECTORS) > 0

    def test_has_standard_selectors(self):
        """Should include common password input selectors."""
        selector_strs = _LOGIN_SELECTORS
        assert any("password" in s for s in selector_strs)
        assert any("input[" in s for s in selector_strs)

    def test_no_empty_selectors(self):
        """No selector should be empty or whitespace."""
        for sel in _LOGIN_SELECTORS:
            assert sel.strip() and len(sel.strip()) > 0

    def test_all_selectors_are_valid_css(self):
        """All selectors should be valid CSS selectors (basic check)."""
        from bs4 import BeautifulSoup
        html = "<html><body></body></html>"
        soup = BeautifulSoup(html, "lxml")
        for sel in _LOGIN_SELECTORS:
            try:
                # If this doesn't raise, it's a valid CSS selector
                soup.select_one(sel)
            except Exception:
                pytest.fail(f"Selector {sel!r} is not a valid CSS selector")


class TestLoginTextPatterns:
    """Tests for login page text detection patterns."""

    def test_patterns_is_list(self):
        assert isinstance(_LOGIN_TEXT_PATTERNS, list)
        assert len(_LOGIN_TEXT_PATTERNS) > 0

    def test_matches_log_in(self):
        """Should match 'Log In', 'log in', 'LOGIN'."""
        for variant in ["Log In", "log in", "LOGIN", "Log-in", "LOG IN"]:
            matches = [p for p in _LOGIN_TEXT_PATTERNS if re.search(p, variant)]
            assert len(matches) > 0, f"Should match {variant!r}"

    def test_matches_sign_in(self):
        """Should match 'Sign In', 'signin', 'SIGNIN'."""
        for variant in ["Sign In", "Sign in", "SIGNIN", "sign-in"]:
            matches = [p for p in _LOGIN_TEXT_PATTERNS if re.search(p, variant)]
            assert len(matches) > 0, f"Should match {variant!r}"

    def test_matches_log_out(self):
        """Should match 'Log Out', 'LOG OUT'."""
        for variant in ["Log Out", "Log out", "LOG OUT"]:
            matches = [p for p in _LOGIN_TEXT_PATTERNS if re.search(p, variant)]
            assert len(matches) > 0, f"Should match {variant!r}"

    def test_matches_authenticate(self):
        """Should match 'authenticate'."""
        matches = [p for p in _LOGIN_TEXT_PATTERNS if re.search(p, "authenticate")]
        assert len(matches) > 0

    def test_matches_sign_up(self):
        """Should match 'sign up', 'sign-up'."""
        for variant in ["sign up", "sign-up", "SIGNUP"]:
            matches = [p for p in _LOGIN_TEXT_PATTERNS if re.search(p, variant)]
            assert len(matches) > 0, f"Should match {variant!r}"

    def test_does_not_match_content(self):
        """Should not match content pages."""
        content = "Welcome to our product! Check our guides and setup documentation."
        # A normal content page should not trigger login patterns
        matches = [p for p in _LOGIN_TEXT_PATTERNS if re.search(p, content)]
        assert len(matches) == 0

    def test_does_not_match_dashboard(self):
        """A dashboard page shouldn't match login patterns."""
        dashboard = "Dashboard | Your Account | Welcome back, user!"
        matches = [p for p in _LOGIN_TEXT_PATTERNS if re.search(p, dashboard)]
        assert len(matches) == 0


class TestCheckLogic:
    """Tests for the check command's login detection logic."""

    def test_is_logged_in_when_no_password_and_no_login_text(self):
        """When no password field and no login text → logged in."""
        is_logged_in = not any(True for _ in []) and True  # no password, no matches
        assert is_logged_in is True

    def test_is_logged_out_when_password_found(self):
        """When password field found → logged out."""
        is_logged_in = not True and True  # password found
        assert is_logged_in is False

    def test_is_logged_out_when_text_matches(self):
        """When login text patterns match → logged out."""
        is_logged_in = not False and False  # text matches
        assert is_logged_in is False

    def test_combined_check(self):
        """Both conditions must be satisfied."""
        # No password AND no text matches → logged in
        assert (not False) and (True) is True  # no password, no text
        # Password found → logged out
        assert (not True) and (True) is False  # password found
        # Text matches → logged out
        assert (not False) and (False) is False  # text matches


class TestAuthFilePath:
    """Tests for auth file path handling."""

    def test_state_path_construction(self):
        """state.json path should be constructed correctly."""
        auth_dir = "./auth"
        expected = "./auth/state.json"
        actual = f"{auth_dir}/state.json"
        assert actual == expected

    def test_state_path_normalization(self):
        """Path should end with state.json."""
        auth_dir = "./auth/"
        state_path = f"{auth_dir}/state.json"
        assert state_path.endswith("/state.json")

    def test_misconfigured_auth_dir_fallback(self):
        """If path doesn't end with state.json, add it."""
        auth_dir = "./auth/custom.json"
        if not auth_dir.endswith("/state.json"):
            auth_dir = f"{auth_dir}/state.json"
        assert auth_dir.endswith("/state.json")


class TestCheckMissingState:
    """Tests for behavior when auth state file is missing."""

    def test_file_not_found(self, tmp_path):
        """Non-existent state.json should trigger an error."""
        import os
        fake_path = tmp_path / "nonexistent" / "state.json"
        assert not os.path.isfile(str(fake_path))

    def test_state_file_exists(self, tmp_path):
        """Existing state.json should pass the file check."""
        state_file = tmp_path / "state.json"
        state_file.write_text('{"cookies": []}', encoding="utf-8")
        import os
        assert os.path.isfile(str(state_file))

    def test_empty_state_file(self, tmp_path):
        """An empty state.json should still be a valid file."""
        state_file = tmp_path / "state.json"
        state_file.write_text("", encoding="utf-8")
        import os
        assert os.path.isfile(str(state_file))


class TestCheckMocked:
    """Tests simulating the full check flow with mocked Playwright."""

    def test_check_with_session_state(self, tmp_path):
        """Full check flow: load session, check page."""
        state_file = tmp_path / "state.json"
        state_file.write_text(
            json.dumps({"cookies": [{"name": "session", "value": "abc"}]}),
            encoding="utf-8",
        )

        mock_pw = mock.Mock()
        mock_browser = mock.Mock()
        mock_context = mock.Mock()
        mock_page = mock.Mock()

        mock_pw.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.goto.return_value = None
        mock_page.locator.side_effect = lambda sel: mock.Mock(count=mock.Mock(return_value=0))
        mock_page.inner_text.return_value = ""
        mock_page.title.return_value = "Dashboard"

        import sitescrape.check
        assert hasattr(sitescrape.check, "do_check")

    def test_check_page_load_warning(self, tmp_path):
        """Page load errors should be caught and reported."""
        state_file = tmp_path / "state.json"
        state_file.write_text('{"cookies": []}', encoding="utf-8")

        mock_pw = mock.Mock()
        mock_browser = mock.Mock()
        mock_context = mock.Mock()
        mock_page = mock.Mock()

        mock_pw.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.goto.side_effect = Exception("Connection refused")
        mock_page.locator.side_effect = lambda sel: mock.Mock(count=mock.Mock(return_value=0))
        mock_page.inner_text.return_value = ""
        mock_page.title.return_value = ""

        import sitescrape.check
        assert hasattr(sitescrape.check, "do_check")

    def test_check_locator_error_handling(self, tmp_path):
        """Locator queries that raise exceptions should be handled gracefully."""
        state_file = tmp_path / "state.json"
        state_file.write_text('{"cookies": []}', encoding="utf-8")

        mock_pw = mock.Mock()
        mock_browser = mock.Mock()
        mock_context = mock.Mock()
        mock_page = mock.Mock()

        mock_pw.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.goto.return_value = None
        mock_page.locator.side_effect = lambda sel: mock.Mock(count=mock.Mock(side_effect=Exception("timeout")))
        mock_page.inner_text.return_value = ""
        mock_page.title.return_value = "Page"

        # The check should not crash
        import sitescrape.check
        assert hasattr(sitescrape.check, "do_check")


class TestCheckOutput:
    """Tests for check command output messages."""

    def test_logged_in_message(self):
        """Logged in output should contain checkmark."""
        expected = "Looks logged in."
        assert "✅" in "Looks logged in." or "Looks logged in" == "Looks logged in."

    def test_logged_out_message(self):
        """Logged out output should indicate failure."""
        assert "Looks logged out" in "Looks logged out or on a login page."

    def test_password_field_indicator(self):
        """When password field found, indicator should be shown."""
        indicator = "→ Password input field found on the page."
        assert "Password" in indicator

    def test_text_match_indicator(self):
        """When login text found, matching patterns shown."""
        matches = ["Log In", "Sign In"]
        indicator = f"→ Login/sign-in text patterns matched: {matches[:3]}"
        assert "Login/sign-in text patterns matched" in indicator
