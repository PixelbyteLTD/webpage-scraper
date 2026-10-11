"""Tests for the sitescrape converter module."""

import json
import textwrap
from pathlib import Path
from unittest import mock

import pytest
import markdownify
import trafilatura
from bs4 import BeautifulSoup

from sitescrape.converter import (
    _slugify_url,
    _compute_image_key,
    _image_ext,
    _download_images,
    _build_url_to_md_map,
    _rewrite_links,
    _extract_title,
    _extract_main_content,
    _url_to_relative_path,
    _load_manifest,
    _generate_index,
    _relative_md_path,
)


# ── _slugify_url ─────────────────────────────────────────────────────


class TestSlugifyUrl:
    def test_simple_path(self):
        assert _slugify_url("https://example.com/guides/setup") == "guides_setup"

    def test_root(self):
        assert _slugify_url("https://example.com/") == "index"

    def special_chars(self):
        assert _slugify_url("https://example.com/a/b c") == "a_b_c"


# ── _compute_image_key ──────────────────────────────────────────────


class TestImageKey:
    def test_deterministic(self):
        k1 = _compute_image_key("https://example.com/img.png")
        k2 = _compute_image_key("https://example.com/img.png")
        assert k1 == k2

    def test_ext_jpg(self):
        assert _image_ext("https://example.com/photo.jpg") == ".jpg"

    def test_ext_unknown(self):
        assert _image_ext("https://example.com/file.abc") == ".png"


# ── _download_images ────────────────────────────────────────────────


class TestDownloadImages:
    def test_skips_data_uri(self, tmp_path):
        html = '<img src="data:image/png;base64,abc">'
        result = _download_images(html, tmp_path / "assets")
        assert "data:image/png" in result

    def test_rewrites_http_url(self, tmp_path):
        assets_dir = tmp_path / "assets"
        html = '<img src="https://example.com/img.png">'
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock.Mock()
            mock_resp.read.return_value = b"x" * 100
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = lambda *a: None
            result = _download_images(html, assets_dir)
        assert "img.png" in result  # key ends with .png


# ── _build_url_to_md_map ────────────────────────────────────────────


class TestUrlToMdMap:
    def test_simple(self):
        entries = {
            "https://example.com/guides/setup": {},
            "https://example.com/guides/faq": {},
        }
        m = _build_url_to_md_map(entries)
        assert m["https://example.com/guides/setup"] == ("guides", "setup.md")
        assert m["https://example.com/guides/faq"] == ("guides", "faq.md")


# ── _rewrite_links ──────────────────────────────────────────────────


class TestRewriteLinks:
    def test_rewrite_internal(self):
        url_map = {"https://example.com/guides/setup": ("guides", "setup.md"),
                   "https://example.com/guides/faq": ("guides", "faq.md")}
        md = "[Setup](https://example.com/guides/setup)"
        result = _rewrite_links(md, url_map, "guides", url_map)
        # Same directory → no ../ needed
        assert "(setup.md)" in result

    def test_rewrite_from_root(self):
        url_map = {"https://example.com/guides/setup": ("guides", "setup.md")}
        md = "[Setup](https://example.com/guides/setup)"
        result = _rewrite_links(md, url_map, "", url_map)
        assert "(guides/setup.md)" in result

    def test_keep_external(self):
        url_map = {}
        md = "[Google](https://google.com)"
        assert _rewrite_links(md, url_map, "", url_map) == md


# ── _extract_title ──────────────────────────────────────────────────


class TestExtractTitle:
    def test_from_title_tag(self):
        html = "<html><head><title>My Page</title></head></html>"
        assert _extract_title(html) == "My Page"

    def test_from_h1(self):
        html = "<html><body><h1>Big Heading</h1></body></html>"
        assert _extract_title(html) == "Big Heading"

    def test_default(self):
        html = "<html><body>No title</body></html>"
        assert _extract_title(html) == "Untitled"


# ── _extract_main_content ───────────────────────────────────────────


class TestExtractMainContent:
    def test_extract_with_trafilatura(self):
        html = """
        <html><body>
        <nav>skip this</nav>
        <article>Main content here</article>
        <footer>skip this too</footer>
        </body></html>
        """
        result = _extract_main_content(html)
        assert "Main content here" in result

    def test_fallback_empty(self):
        result = _extract_main_content("<html><body></body></html>")
        assert result == ""


# ── _url_to_relative_path ───────────────────────────────────────────


class TestUrlToRelativePath:
    def test_nested(self):
        d, f = _url_to_relative_path("https://example.com/guides/setup")
        assert d == "guides"
        assert f == "setup.md"

    def test_root(self):
        d, f = _url_to_relative_path("https://example.com/")
        assert d == ""
        assert f == "index.md"


# ── _load_manifest ──────────────────────────────────────────────────


class TestLoadManifest:
    def test_load(self, tmp_path):
        manifest_data = [
            {"url": "https://example.com/page", "file": "page.html", "title": "Page", "fetch_time": "2026-01-01"}
        ]
        (tmp_path / "manifest.json").write_text(json.dumps(manifest_data))
        result = _load_manifest(tmp_path)
        assert len(result) == 1
        assert "https://example.com/page" in result

    def test_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _load_manifest(tmp_path)


# ── _generate_index ─────────────────────────────────────────────────


class TestGenerateIndex:
    def test_creates_index(self, tmp_path):
        processed = [
            ("Home", "", "https://example.com/"),
            ("Setup", "guides", "https://example.com/guides/setup"),
            ("FAQ", "guides", "https://example.com/guides/faq"),
        ]
        entries = {e[2]: {"fetch_time": "2026-01-01"} for e in processed}
        url_map = {
            "https://example.com/": ("", "index.md"),
            "https://example.com/guides/setup": ("guides", "setup.md"),
            "https://example.com/guides/faq": ("guides", "faq.md"),
        }

        _generate_index(tmp_path, processed, entries, url_map)
        index = (tmp_path / "index.md").read_text()
        assert "Site Contents" in index
        assert "Home" in index
        assert "Setup" in index
        assert "FAQ" in index
        assert "(index.md)" in index
        assert "(guides/setup.md)" in index
        assert "(guides/faq.md)" in index
