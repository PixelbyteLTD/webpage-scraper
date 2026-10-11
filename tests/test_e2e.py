"""End-to-end tests for the sitescrape pipeline (crawl → convert → site).

Uses a tiny local fixture site served on localhost with a fake cookie login.
Never tests against a real external site.
"""

import http.server
import json
import os
import socketserver
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

# ── fixture site builder ────────────────────────────────────────────────


FIXTURE_PAGES = {
    "index.html": """\
<!DOCTYPE html>
<html>
<head><title>Home</title></head>
<body>
<h1>Home Page</h1>
<nav>
  <a href="index.html">Home</a>
  <a href="about.html">About</a>
  <a href="products.html">Products</a>
  <a href="contact.html">Contact</a>
  <a href="blog/post1.html">Blog Post</a>
  <a href="logout">Logout</a>
</nav>
<p>Welcome to the test site. Visit our <a href="about.html">About</a> page for more info.</p>
</body>
</html>
""",
    "about.html": """\
<!DOCTYPE html>
<html>
<head><title>About Us</title></head>
<body>
<h1>About Us</h1>
<nav>
  <a href="index.html">Home</a>
  <a href="about.html">About</a>
  <a href="products.html">Products</a>
</nav>
<p>We are a test company. Learn more at <a href="products.html">Products</a>.</p>
<img src="https://example.com/logo.png" alt="Logo">
</body>
</html>
""",
    "products.html": """\
<!DOCTYPE html>
<html>
<head><title>Products</title></head>
<body>
<h1>Products</h1>
<nav>
  <a href="index.html">Home</a>
  <a href="about.html">About</a>
  <a href="products.html">Products</a>
</nav>
<p>Check out our <a href="contact.html">Contact</a> page.</p>
</body>
</html>
""",
    "contact.html": """\
<!DOCTYPE html>
<html>
<head><title>Contact</title></head>
<body>
<h1>Contact Us</h1>
<nav>
  <a href="index.html">Home</a>
  <a href="contact.html">Contact</a>
</nav>
<p>Email us at <a href="mailto:test@example.com">test@example.com</a>.</p>
</body>
</html>
""",
    "blog/post1.html": """\
<!DOCTYPE html>
<html>
<head><title>Blog Post 1</title></head>
<body>
<h1>Our First Blog Post</h1>
<nav>
  <a href="../index.html">Home</a>
  <a href="post1.html">Blog Post 1</a>
</nav>
<article>
<p>This is blog post number one. Read more at <a href="../about.html">About</a>.</p>
</article>
</body>
</html>
""",
}


class FixtureSite:
    """A tiny HTTP server that serves a fake site."""

    def __init__(self, pages: dict):
        self.port = self._find_free_port()
        self.pages = pages
        self.server = None
        self.thread = None

    @staticmethod
    def _find_free_port() -> int:
        with socketserver.TCPServer(("127.0.0.1", 0), None) as s:
            return s.server_address[1]

    def start(self):
        handler = type("Handler", (http.server.SimpleHTTPRequestHandler,), {
            "directory": str(Path(__file__).parent / "fixture"),
            "log_message": lambda *a: None,  # silence access log
        })
        self.server = socketserver.TCPServer(("127.0.0.1", self.port), handler)
        # Set directory attribute for SimpleHTTPRequestHandler
        self.server.server_address  # noqa: B018
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)  # let server start

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture(scope="module")
def fixture_site(tmp_path_factory):
    """Create and serve a fixture site for the duration of this module."""
    # Write fixture HTML files
    fixture_dir = tmp_path_factory.mktemp("fixture")
    for name, content in FIXTURE_PAGES.items():
        file_path = fixture_dir / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    site = FixtureSite(FIXTURE_PAGES)
    site.start()
    yield site
    site.stop()


@pytest.fixture
def auth_dir(tmp_path):
    """Create a fake auth/state.json for cookie-based login."""
    auth_dir = tmp_path / "test_auth"
    auth_dir.mkdir()
    state = {
        "cookies": [
            {
                "name": "session",
                "value": "fake_token_123",
                "domain": "127.0.0.1",
                "path": "/",
                "secure": False,
                "httpOnly": False,
            }
        ],
        "expires": [],
        "domain": [],
        "path": "/",
    }
    (auth_dir / "state.json").write_text(json.dumps(state))
    return auth_dir


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "test_cache"


@pytest.fixture
def output_dir(tmp_path):
    return tmp_path / "test_output"


# ── crawl tests (mocked, no real browser needed) ─────────────────────────

class TestCrawl:
    def test_crawls_fixture_site(self, fixture_site, cache_dir, auth_dir):
        """Crawl should save at least the starting page HTML."""
        from sitescrape.crawler import do_crawl

        # Mock page.goto to return our fixture HTML and avoid actual browser
        fake_html = FIXTURE_PAGES["index.html"]
        mock_page = mock.MagicMock()
        mock_page.goto.return_value.status = 200
        mock_page.url = f"{fixture_site.base_url}/index.html"
        mock_page.title.return_value = "Home"
        mock_page.content.return_value = fake_html
        mock_page.query_selector_all.return_value = []  # no links on index to follow
        mock_page.locator.return_value.count.return_value = 0

        with mock.patch("sitescrape.crawler.sync_playwright") as mock_pw:
            mock_browser = mock.MagicMock()
            mock_context = mock.MagicMock()
            mock_pw.return_value.__enter__ = lambda s: mock_pw.return_value
            mock_pw.return_value.__exit__ = lambda *a: None
            mock_pw.return_value.chromium.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page

            do_crawl(
                start_url=f"{fixture_site.base_url}/index.html",
                max_pages=5,
                delay=0.1,
                cache_dir=str(cache_dir),
                auth_dir=str(auth_dir),
                include=[],
                exclude=[],
                refresh=False,
            )

        manifest = json.loads((cache_dir / "manifest.json").read_text())
        urls = [entry["url"] for entry in manifest]

        # Should have saved at least index.html
        assert len(urls) >= 1
        assert f"{fixture_site.base_url}/index.html" in urls
        # HTML should have been saved
        cache_files = list(cache_dir.glob("*.html"))
        assert len(cache_files) >= 1

    def test_skips_already_cached(self, fixture_site, cache_dir, auth_dir):
        """Second crawl should keep the same manifest count."""
        from sitescrape.crawler import do_crawl

        # First crawl
        fake_html = FIXTURE_PAGES["index.html"]
        mock_page = mock.MagicMock()
        mock_page.goto.return_value.status = 200
        mock_page.url = f"{fixture_site.base_url}/index.html"
        mock_page.title.return_value = "Home"
        mock_page.content.return_value = fake_html
        mock_page.query_selector_all.return_value = []
        mock_page.locator.return_value.count.return_value = 0

        with mock.patch("sitescrape.crawler.sync_playwright") as mock_pw:
            mock_browser = mock.MagicMock()
            mock_context = mock.MagicMock()
            mock_pw.return_value.__enter__ = lambda s: mock_pw.return_value
            mock_pw.return_value.__exit__ = lambda *a: None
            mock_pw.return_value.chromium.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page

            do_crawl(
                start_url=f"{fixture_site.base_url}/index.html",
                max_pages=3,
                delay=0.1,
                cache_dir=str(cache_dir),
                auth_dir=str(auth_dir),
                include=[],
                exclude=[],
                refresh=False,
            )

        manifest_first = json.loads((cache_dir / "manifest.json").read_text())
        count_first = len(manifest_first)
        assert count_first >= 1

        # Second crawl — all pages already cached, should skip them all
        with mock.patch("sitescrape.crawler.sync_playwright") as mock_pw:
            mock_browser = mock.MagicMock()
            mock_context = mock.MagicMock()
            mock_pw.return_value.__enter__ = lambda s: mock_pw.return_value
            mock_pw.return_value.__exit__ = lambda *a: None
            mock_pw.return_value.chromium.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page

            do_crawl(
                start_url=f"{fixture_site.base_url}/index.html",
                max_pages=3,
                delay=0.1,
                cache_dir=str(cache_dir),
                auth_dir=str(auth_dir),
                include=[],
                exclude=[],
                refresh=False,
            )

        manifest_after = json.loads((cache_dir / "manifest.json").read_text())
        count_after = len(manifest_after)
        # Manifest should not shrink
        assert count_after == count_first


# ── convert tests ───────────────────────────────────────────────────────

class TestConvert:
    def test_convert_rewrites_links(self, tmp_path):
        """Convert should rewrite internal links to relative .md paths."""
        from sitescrape.converter import _rewrite_links, _url_to_relative_path

        url_map = {
            "http://127.0.0.1:8000/index.html": ("", "index.md"),
            "http://127.0.0.1:8000/about.html": ("", "about.md"),
            "http://127.0.0.1:8000/blog/post1.html": ("blog", "post1.md"),
        }

        # Link from blog post to about
        md = "Check out [About](http://127.0.0.1:8000/about.html) for details."
        result = _rewrite_links(md, url_map, "blog", url_map)
        assert "[About](../about.md)" in result

        # Link from root to blog post
        md = "Read [Post 1](http://127.0.0.1:8000/blog/post1.html)."
        result = _rewrite_links(md, url_map, "", url_map)
        assert "[Post 1](blog/post1.md)" in result

    def test_slugs_are_safe(self):
        from sitescrape.converter import _slugify_url
        assert _slugify_url("https://example.com/a b c") == "a_b_c"
        assert _slugify_url("https://example.com/") == "index"


# ── site config tests ───────────────────────────────────────────────────

class TestSiteConfig:
    def test_generate_mkdocs_yml(self, tmp_path):
        """Generate mkdocs.yml from an empty docs dir should produce valid YAML."""
        from sitescrape.site import do_site

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()

        output = tmp_path / "mkdocs.yml"
        do_site(docs_dir=str(docs_dir), output_file=str(output))

        assert output.exists()
        import yaml
        config = yaml.safe_load(output.read_text())
        assert config["site_name"] == "Sitescrape Documentation"
        assert config["theme"]["name"] == "material"
        # plugins is a list of dicts: [{"search": {}}, ...]
        plugin_names = [list(k)[0] for k in config["plugins"]]
        assert "search" in plugin_names

    def test_nav_from_pages(self, tmp_path):
        """Nav should reflect the directory structure of docs."""
        from sitescrape.site import _scan_docs_dir

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "index.md").write_text("# Home")
        (docs_dir / "guides").mkdir(parents=True)
        (docs_dir / "guides" / "setup.md").write_text("# Setup")
        (docs_dir / "guides" / "faq.md").write_text("# FAQ")

        pages = _scan_docs_dir(docs_dir)
        # Should find index.md and guide files
        paths = {p["path"] for p in pages}
        assert "index.md" in paths

    def test_config_file_exists(self, tmp_path):
        """mkdocs.yml should have site_name, theme with name=material."""
        from sitescrape.site import do_site

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()

        output = tmp_path / "mkdocs.yml"
        do_site(docs_dir=str(docs_dir), output_file=str(output))

        import yaml
        config = yaml.safe_load(output.read_text())
        assert config["site_name"] == "Sitescrape Documentation"
        assert config["theme"]["name"] == "material"
        plugin_names = [list(k)[0] for k in config["plugins"]]
        assert "search" in plugin_names


# ── end-to-end: crawl → convert → site (without real auth) ──────────────

class TestE2EWithoutAuth:
    """Test the full pipeline using mock/fixture data when auth won't work on localhost."""

    def test_convert_and_site_pipeline(self, tmp_path):
        """Simulate the crawl→convert→site flow with pre-built manifest."""
        from sitescrape.converter import do_convert
        from sitescrape.site import do_site

        # Build a fake manifest + HTML files
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        entries = []
        urls = [
            "http://127.0.0.1:8000/index.html",
            "http://127.0.0.1:8000/about.html",
            "http://127.0.0.1:8000/products.html",
            "http://127.0.0.1:8000/blog/post1.html",
        ]
        for url in urls:
            slug = url.replace("http://127.0.0.1:8000/", "").replace("/", "_")
            html_file = cache_dir / (slug + ".html")
            html_file.write_text(f"<html><body><h1>Page</h1><p>Content for {url}</p></body></html>")
            entries.append({
                "url": url,
                "file": str(html_file.relative_to(cache_dir)),
                "title": "Page",
                "fetch_time": "2026-01-01T00:00:00",
            })

        (cache_dir / "manifest.json").write_text(json.dumps(entries))

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Convert
        do_convert(cache_dir=str(cache_dir), output_dir=str(output_dir / "docs"))

        # Verify markdown was created
        docs_dir = output_dir / "docs"
        assert (docs_dir / "index.md").exists()

        # Site config
        site_output = output_dir / "mkdocs.yml"
        do_site(docs_dir=str(docs_dir), output_file=str(site_output))
        assert site_output.exists()

        import yaml
        config = yaml.safe_load(site_output.read_text())
        assert config["site_name"] == "Sitescrape Documentation"
        assert config["theme"]["name"] == "material"


# ── integration: import-cookies ─────────────────────────────────────────

class TestImportCookies:
    def test_import_creates_state(self, tmp_path):
        from sitescrape.cookies import do_import_cookies

        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text(
            "# Netscape HTTP Cookie File\n"
            "127.0.0.1\tTRUE\t/\tFALSE\t0\tsession\tfake_token\n"
        )

        auth_dir = tmp_path / "auth"
        do_import_cookies(file=str(cookies_file), auth_dir=str(auth_dir))

        state_path = auth_dir / "state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert len(state["cookies"]) == 1
        assert state["cookies"][0]["name"] == "session"
