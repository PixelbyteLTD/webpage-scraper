"""Tests for the crawler module (URL normalisation, manifest helpers, etc)."""

import json
from pathlib import Path
from unittest import mock

import pytest
import re

from sitescrape.crawler import (
    _strip_tracking,
    _normalise,
    Manifest,
    ManifestEntry,
    CrawlStats,
    _is_login_page,
    _has_avoid_links,
    _extract_links,
    _slugify_url,
    _LOGIN_SELECTORS,
    _LOGIN_TEXT_PATTERNS,
    _AVOID_LINK_TEXT_RE,
)


# ── _strip_tracking ──────────────────────────────────────────────────

class TestStripTracking:
    """Tests for URL tracking parameter stripping."""

    def test_strips_utm_source(self):
        url = "https://example.com/page?utm_source=google"
        result = _strip_tracking(url)
        assert "utm_source" not in result

    def test_strips_utm_campaign(self):
        url = "https://example.com/page?utm_campaign=spring-sale&utm_medium=email"
        result = _strip_tracking(url)
        assert "utm_campaign" not in result
        assert "utm_medium" not in result

    def test_strips_fbclid(self):
        url = "https://example.com/page?fbclid=abc123"
        result = _strip_tracking(url)
        assert "fbclid" not in result

    def test_strips_gclid(self):
        url = "https://example.com/page?gclid=xyz789"
        result = _strip_tracking(url)
        assert "gclid" not in result

    def test_keeps_important_params(self):
        url = "https://example.com/page?id=42&name=test"
        result = _strip_tracking(url)
        assert "id=42" in result
        assert "name=test" in result

    def test_strips_fragment(self):
        url = "https://example.com/page#section"
        result = _strip_tracking(url)
        assert "#" not in result
        assert "section" not in result

    def test_empty_query_string(self):
        url = "https://example.com/page"
        result = _strip_tracking(url)
        assert "?" not in result or result.endswith("?")

    def test_multiple_tracking_params(self):
        url = "https://example.com/page?utm_source=google&fbclid=abc&gclid=xyz&mc_eid=123"
        result = _strip_tracking(url)
        for param in ["utm_source", "fbclid", "gclid", "mc_eid"]:
            assert param not in result

    def test_preserves_url_without_tracking(self):
        url = "https://example.com/page?foo=bar"
        result = _strip_tracking(url)
        assert "foo=bar" in result

    def test_case_insensitive_tracking(self):
        url = "https://example.com/page?UTM_SOURCE=google"
        result = _strip_tracking(url)
        assert "UTM_SOURCE" not in result


# ── _normalise ───────────────────────────────────────────────────────

class TestNormalise:
    """Tests for URL normalisation against a base URL."""

    def test_same_domain(self):
        result = _normalise("/page", "https://example.com/")
        assert result == "https://example.com/page"

    def test_relative_url_resolved(self):
        result = _normalise("guides/setup", "https://example.com/")
        assert "guides/setup" in result

    def test_different_domain_rejected(self):
        result = _normalise("https://other.com/page", "https://example.com/")
        assert result is None

    def test_same_domain_different_subdomain_rejected(self):
        result = _normalise("https://sub.example.com/page", "https://example.com/")
        assert result is None

    def test_non_http_rejected(self):
        result = _normalise("ftp://example.com/page", "https://example.com/")
        assert result is None

    def test_malformed_base_rejected(self):
        result = _normalise("/page", "not-a-url")
        assert result is None

    def test_strips_tracking_from_resolved(self):
        result = _normalise("/page?utm_source=google", "https://example.com/")
        assert "utm_source" not in result

    def test_keeps_same_domain_path(self):
        result = _normalise("/docs/intro", "https://example.com/docs/")
        assert result is not None

    def test_root_url(self):
        result = _normalise("/", "https://example.com/")
        assert result is not None
        assert "example.com" in result

    def test_http_base(self):
        result = _normalise("/page", "http://example.com/")
        assert result is not None
        assert "http://" in result


# ── Manifest / ManifestEntry ────────────────────────────────────────

class TestManifestEntry:
    """Tests for the ManifestEntry dataclass."""

    def test_basic_fields(self):
        entry = ManifestEntry(
            url="https://example.com/page",
            file="page.html",
            title="Page Title",
            fetch_time="2026-01-01T00:00:00",
        )
        assert entry.url == "https://example.com/page"
        assert entry.file == "page.html"
        assert entry.title == "Page Title"
        assert entry.fetch_time == "2026-01-01T00:00:00"

    def test_empty_title(self):
        entry = ManifestEntry(
            url="https://example.com/",
            file="index.html",
            title="",
            fetch_time="2026-01-01T00:00:00",
        )
        assert entry.title == ""


class TestManifest:
    """Tests for the Manifest class."""

    def test_empty_manifest(self):
        m = Manifest()
        assert len(m.entries) == 0

    def test_save_and_load(self, tmp_path):
        data = [
            {"url": "https://example.com/a", "file": "a.html", "title": "A", "fetch_time": "2026-01-01"},
            {"url": "https://example.com/b", "file": "b.html", "title": "B", "fetch_time": "2026-01-02"},
        ]
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(data) + "\n")

        m = Manifest.load(manifest_path)
        assert len(m.entries) == 2
        assert "https://example.com/a" in m.entries
        assert "https://example.com/b" in m.entries

    def test_load_empty_file(self, tmp_path):
        """Loading an empty manifest file returns empty manifest."""
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text("[]")

        m = Manifest.load(manifest_path)
        assert len(m.entries) == 0

    def test_load_nonexistent_returns_empty(self, tmp_path):
        m = Manifest.load(tmp_path / "nonexistent.json")
        assert len(m.entries) == 0

    def test_save_roundtrip(self, tmp_path):
        m = Manifest()
        m.entries["https://example.com/page"] = ManifestEntry(
            url="https://example.com/page",
            file="page.html",
            title="Test",
            fetch_time="2026-01-01T00:00:00",
        )
        m.save(tmp_path / "manifest.json")

        # Verify file was created
        assert (tmp_path / "manifest.json").exists()

        # Load and verify
        loaded = Manifest.load(tmp_path / "manifest.json")
        assert len(loaded.entries) == 1

    def test_save_empty_manifest(self, tmp_path):
        m = Manifest()
        m.save(tmp_path / "manifest.json")
        data = json.loads((tmp_path / "manifest.json").read_text())
        assert data == []


# ── CrawlStats ───────────────────────────────────────────────────────

class TestCrawlStats:
    """Tests for CrawlStats dataclass."""

    def test_default_values(self):
        stats = CrawlStats()
        assert stats.saved == 0
        assert stats.skipped == 0
        assert stats.failed == 0
        assert stats.login_detected is False

    def test_increment_saved(self):
        stats = CrawlStats()
        stats.saved = 5
        assert stats.saved == 5

    def test_all_fields_accessible(self):
        stats = CrawlStats(
            saved=10,
            skipped=3,
            failed=2,
            login_detected=True,
        )
        assert stats.saved == 10
        assert stats.skipped == 3
        assert stats.failed == 2
        assert stats.login_detected is True


# ── _slugify_url ─────────────────────────────────────────────────────

class TestSlugifyUrl:
    """Tests for crawler's _slugify_url function."""

    def test_simple(self):
        result = _slugify_url("https://example.com/guides/setup")
        assert result == "guides_setup.html"

    def test_root(self):
        result = _slugify_url("https://example.com/")
        assert result == "index.html"

    def test_special_chars(self):
        result = _slugify_url("https://example.com/page with spaces")
        assert "spaces.html" in result

    def test_query_string_stripped(self):
        result = _slugify_url("https://example.com/page?foo=bar")
        assert "html" in result

    def test_empty_path(self):
        result = _slugify_url("https://example.com")
        assert result == "index.html"


# ── _is_login_page ───────────────────────────────────────────────────

class TestIsLoginPage:
    """Tests for the login page detection heuristic."""

    def test_with_password_field(self):
        mock_page = mock.Mock()
        mock_page.locator.return_value.count.return_value = 1
        assert _is_login_page(mock_page) is True

    def test_without_password_field(self):
        mock_page = mock.Mock()
        mock_page.locator.return_value.count.return_value = 0

        # No password field, check text patterns
        mock_page.inner_text.return_value = ""
        mock_page.title.return_value = "Home"

        # Create a mock that tracks how many times locator was called
        locator_calls = []

        def mock_locator(selector):
            class MockLocator:
                def count(self):
                    locator_calls.append(selector)
                    return 0
            return MockLocator()

        mock_page.locator.side_effect = mock_locator
        assert _is_login_page(mock_page) is False

    def test_login_text_detected(self):
        mock_page = mock.Mock()
        mock_page.locator.return_value.count.return_value = 0
        mock_page.inner_text.return_value = "Please Log In"
        mock_page.title.return_value = "Login"

        def mock_locator(selector):
            class MockLocator:
                def count(self):
                    return 0
            return MockLocator()

        mock_page.locator.side_effect = mock_locator
        assert _is_login_page(mock_page) is True


# ── _has_avoid_links ─────────────────────────────────────────────────

class TestHasAvoidLinks:
    """Tests for avoid links detection."""

    def test_has_logout_link(self):
        mock_page = mock.Mock()
        mock_link = mock.Mock()
        mock_link.inner_text.return_value = "Logout"
        mock_link.get_attribute.return_value = "/logout"
        mock_page.query_selector_all.return_value = [mock_link]
        assert _has_avoid_links(mock_page) is True

    def test_no_avoid_links(self):
        mock_page = mock.Mock()
        mock_link = mock.Mock()
        mock_link.inner_text.return_value = "Next"
        mock_link.get_attribute.return_value = "/next"
        mock_page.query_selector_all.return_value = [mock_link]
        assert _has_avoid_links(mock_page) is False

    def test_empty_page(self):
        mock_page = mock.Mock()
        mock_page.query_selector_all.return_value = []
        assert _has_avoid_links(mock_page) is False

    def test_query_error_returns_false(self):
        mock_page = mock.Mock()
        mock_page.query_selector_all.side_effect = Exception("error")
        assert _has_avoid_links(mock_page) is False


# ── _extract_links ───────────────────────────────────────────────────

class TestExtractLinks:
    """Tests for link extraction."""

    def test_basic_links(self):
        mock_page = mock.Mock()
        link1 = mock.Mock()
        link1.get_attribute.return_value = "/page1"
        link2 = mock.Mock()
        link2.get_attribute.return_value = "/page2"
        mock_page.query_selector_all.return_value = [link1, link2]

        result = _extract_links(mock_page, "https://example.com/")
        assert len(result) == 2

    def test_skips_javascript_links(self):
        mock_page = mock.Mock()
        link = mock.Mock()
        link.get_attribute.return_value = "javascript:void(0)"
        mock_page.query_selector_all.return_value = [link]

        result = _extract_links(mock_page, "https://example.com/")
        assert result == []

    def test_skips_mailto_links(self):
        mock_page = mock.Mock()
        link = mock.Mock()
        link.get_attribute.return_value = "mailto:test@example.com"
        mock_page.query_selector_all.return_value = [link]

        result = _extract_links(mock_page, "https://example.com/")
        assert result == []

    def test_skips_tel_links(self):
        mock_page = mock.Mock()
        link = mock.Mock()
        link.get_attribute.return_value = "tel:+1234567890"
        mock_page.query_selector_all.return_value = [link]

        result = _extract_links(mock_page, "https://example.com/")
        assert result == []

    def test_empty_page(self):
        mock_page = mock.Mock()
        mock_page.query_selector_all.return_value = []
        result = _extract_links(mock_page, "https://example.com/")
        assert result == []

    def test_query_error_returns_empty(self):
        mock_page = mock.Mock()
        mock_page.query_selector_all.side_effect = Exception("error")
        result = _extract_links(mock_page, "https://example.com/")
        assert result == []


# ── _AVOID_LINK_TEXT_RE ──────────────────────────────────────────────

class TestAvoidLinkRegex:
    """Tests for the avoid link text regex pattern."""

    def test_matches_logout(self):
        assert _AVOID_LINK_TEXT_RE.search("Logout") is not None
        assert _AVOID_LINK_TEXT_RE.search("logout") is not None

    def test_matches_sign_out(self):
        assert _AVOID_LINK_TEXT_RE.search("Sign Out") is not None
        assert _AVOID_LINK_TEXT_RE.search("sign out") is not None

    def test_matches_signout(self):
        assert _AVOID_LINK_TEXT_RE.search("signout") is not None

    def test_matches_delete_account(self):
        assert _AVOID_LINK_TEXT_RE.search("Delete Account") is not None
        assert _AVOID_LINK_TEXT_RE.search("delete account") is not None

    def test_matches_sign_in(self):
        assert _AVOID_LINK_TEXT_RE.search("Sign In") is not None
        assert _AVOID_LINK_TEXT_RE.search("sign in") is not None

    def test_matches_sign_up(self):
        assert _AVOID_LINK_TEXT_RE.search("Sign Up") is not None
        assert _AVOID_LINK_TEXT_RE.search("sign up") is not None

    def test_does_not_match_content(self):
        assert _AVOID_LINK_TEXT_RE.search("Read more") is None
        assert _AVOID_LINK_TEXT_RE.search("Next page") is None
        assert _AVOID_LINK_TEXT_RE.search("Documentation") is None


# ── Integration: Manifest round-trip ─────────────────────────────────

class TestManifestIntegration:
    """Integration tests for manifest save/load cycle."""

    def test_multi_page_manifest(self, tmp_path):
        """Multiple pages should round-trip correctly."""
        m = Manifest()
        m.entries["https://example.com/"] = ManifestEntry(
            url="https://example.com/", file="index.html", title="Home",
            fetch_time="2026-01-01T00:00:00",
        )
        m.entries["https://example.com/guides/setup"] = ManifestEntry(
            url="https://example.com/guides/setup", file="guides_setup.html",
            title="Setup Guide", fetch_time="2026-01-01T00:01:00",
        )

        m.save(tmp_path / "manifest.json")
        loaded = Manifest.load(tmp_path / "manifest.json")

        assert len(loaded.entries) == 2
        assert loaded.entries["https://example.com/"].title == "Home"
        assert loaded.entries["https://example.com/guides/setup"].title == "Setup Guide"
