"""Integration tests: full scrape cycle on a mock site."""

import json
from pathlib import Path
from unittest import mock

import pytest

from sitescrape.crawler import Manifest, ManifestEntry, _slugify_url, _strip_tracking
from sitescrape.converter import (
    _load_manifest,
    _extract_title,
    _extract_main_content,
    _url_to_relative_path,
    _generate_index,
)


class TestMockSiteData:
    """Verify the e2e test data is well-formed."""

    def test_e2e_manifest_exists(self):
        manifest_path = Path("tests/e2e_manifest.json")
        assert manifest_path.exists(), "e2e_manifest.json not found"

    def test_e2e_manifest_is_valid_json(self):
        manifest_path = Path("tests/e2e_manifest.json")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)

    def test_e2e_manifest_has_required_fields(self):
        manifest_path = Path("tests/e2e_manifest.json")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in data:
            assert "url" in entry
            assert "file" in entry
            assert "fetch_time" in entry

    def test_e2e_html_files_exist(self, tmp_path):
        manifest_path = Path("tests/e2e_manifest.json")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in data:
            html_path = Path("tests/e2e_cache") / entry["file"]
            assert html_path.exists(), f"HTML file not found: {html_path}"


class TestFullScrapeCycle:
    """Test a complete crawl → convert cycle using mock data."""

    def test_load_manifest_from_e2e_cache(self):
        """Load manifest from the e2e_cache directory."""
        cache_dir = Path("tests/e2e_cache")
        manifest_path = cache_dir / "manifest.json"
        assert manifest_path.exists()

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(data) > 0

        entries = {item["url"]: item for item in data}
        assert len(entries) == len(data)  # No duplicate URLs

    def test_convert_roundtrip(self, tmp_path):
        """Simulate the full scrape → convert cycle on mock data."""
        # 1. Setup mock cache directory with HTML pages
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        output_dir = tmp_path / "output" / "docs"

        manifest_data = [
            {
                "url": "https://example.com/",
                "file": "index.html",
                "title": "Home",
                "fetch_time": "2026-01-01T00:00:00",
            },
            {
                "url": "https://example.com/guides/setup",
                "file": "setup.html",
                "title": "Setup Guide",
                "fetch_time": "2026-01-01T00:01:00",
            },
            {
                "url": "https://example.com/guides/faq",
                "file": "faq.html",
                "title": "FAQ",
                "fetch_time": "2026-01-01T00:02:00",
            },
        ]

        manifest_path = cache_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_data) + "\n")

        # Write mock HTML files
        for entry in manifest_data:
            html_path = cache_dir / entry["file"]
            html_path.write_text(
                f"<html><head><title>{entry['title']}</title></head>"
                f"<body><article>Main content for {entry['title']}</article></body></html>",
                encoding="utf-8",
            )

        # 2. Load manifest (same as converter does)
        entries = _load_manifest(cache_dir)
        assert len(entries) == 3

        # 3. Verify URL-to-path mapping
        d, f = _url_to_relative_path("https://example.com/guides/setup")
        assert d == "guides"
        assert f == "setup.md"

        d, f = _url_to_relative_path("https://example.com/")
        assert d == ""
        assert f == "index.md"

        # 4. Verify title extraction
        html = '<html><head><title>My Page</title></head><body><h1>Wrong</h1></body></html>'
        title = _extract_title(html)
        assert title == "My Page"

        # 5. Verify content extraction
        html = '<html><body><nav>skip</nav><article>Real content</article></body></html>'
        content = _extract_main_content(html)
        assert "Real content" in content

        # 6. Simulate file creation
        output_dir.mkdir(parents=True, exist_ok=True)
        for url, entry in entries.items():
            html_file = cache_dir / entry["file"]
            if not html_file.is_file():
                continue
            html = html_file.read_text(encoding="utf-8")
            title = _extract_title(html)
            content = _extract_main_content(html)
            directory, filename = _url_to_relative_path(url)
            file_path = output_dir / directory / filename

            from sitescrape.converter import _build_url_to_md_map, _rewrite_links
            from datetime import datetime, timezone

            url_map = _build_url_to_md_map(entries)
            content = _rewrite_links(content, url_map, directory, url_map)

            scraped_at = entry.get(
                "fetch_time",
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            front_matter = (
                f"---\ntitle: {title}\nsource_url: {url}\nscraped_at: {scraped_at}\n---\n\n"
            )
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(front_matter + content + "\n", encoding="utf-8")

        # 7. Generate index
        processed = []
        for url, entry in entries.items():
            directory, filename = _url_to_relative_path(url)
            file_path = output_dir / directory / filename
            title = _extract_title((cache_dir / entry["file"]).read_text(encoding="utf-8"))
            processed.append((title, directory, url))

        _generate_index(output_dir, processed, entries, {})

        # 8. Verify output
        index_path = output_dir / "index.md"
        assert index_path.exists()
        index_content = index_path.read_text(encoding="utf-8")
        assert "Site Contents" in index_content

        # Verify each file was created
        for url, entry in entries.items():
            directory, filename = _url_to_relative_path(url)
            file_path = output_dir / directory / filename
            assert file_path.exists(), f"Missing output: {file_path}"

    def test_crawl_normalise_integration(self):
        """Crawler URL normalisation should match converter URL mapping."""
        from sitescrape.crawler import _normalise
        from sitescrape.converter import _url_to_relative_path

        # Normalize URL
        normalised = _normalise("/guides/setup", "https://example.com/")
        assert normalised is not None

        # Map to relative path
        directory, filename = _url_to_relative_path(normalised)
        assert directory == "guides"
        assert filename == "setup.md"


class TestMockedCrawlToConvert:
    """Full mock of crawl → convert pipeline."""

    def test_mocked_page_crawled_and_converted(self, tmp_path):
        """Simulate a crawled page being converted to markdown."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        output_dir = tmp_path / "output" / "docs"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Simulate a crawled page
        html = """
        <html>
        <head><title>Product Overview</title></head>
        <body>
            <nav><a href="/logout">Logout</a></nav>
            <header>Site Header</header>
            <main>
                <h1>Product Overview</h1>
                <p>This is the product description.</p>
                <h2>Features</h2>
                <ul><li>Feature 1</li><li>Feature 2</li></ul>
            </main>
            <footer>Footer text</footer>
        </body>
        </html>
        """

        # Save as crawled page
        page_file = cache_dir / "product.html"
        page_file.write_text(html, encoding="utf-8")

        # Create manifest
        manifest = {
            "url": "https://example.com/products",
            "file": "product.html",
            "title": "Product Overview",
            "fetch_time": "2026-01-01T12:00:00",
        }
        manifest_path = cache_dir / "manifest.json"
        manifest_path.write_text(json.dumps([manifest]) + "\n")

        # Convert
        entries = _load_manifest(cache_dir)
        assert len(entries) == 1

        url = "https://example.com/products"
        entry = entries[url]

        html = page_file.read_text(encoding="utf-8")
        title = _extract_title(html)
        content = _extract_main_content(html)

        assert "Product Overview" in title
        assert "product description" in content.lower() or "product" in content.lower()

        directory, filename = _url_to_relative_path(url)
        file_path = output_dir / directory / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)

        from sitescrape.converter import _build_url_to_md_map, _rewrite_links
        from datetime import datetime, timezone

        url_map = _build_url_to_md_map(entries)
        content = _rewrite_links(content, url_map, directory, url_map)

        scraped_at = entry.get("fetch_time", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        front_matter = (
            f"---\ntitle: {title}\nsource_url: {url}\nscraped_at: {scraped_at}\n---\n\n"
        )
        file_path.write_text(front_matter + content + "\n", encoding="utf-8")

        assert file_path.exists()
        output_text = file_path.read_text(encoding="utf-8")
        assert "title: Product Overview" in output_text
        assert "source_url: https://example.com/products" in output_text


class TestManifestDataclassRoundTrip:
    """Test Manifest dataclass save/load round-trip as integration."""

    def test_multi_page_manifest_round_trip(self, tmp_path):
        """Multiple pages should survive a full save/load cycle."""
        m = Manifest()
        urls = [
            "https://example.com/",
            "https://example.com/guides/setup",
            "https://example.com/guides/faq",
            "https://example.com/api/v1",
        ]
        for i, url in enumerate(urls):
            m.entries[url] = ManifestEntry(
                url=url,
                file=f"page_{i}.html",
                title=f"Page {i}",
                fetch_time=f"2026-01-01T00:{i:02d}:00",
            )

        manifest_path = tmp_path / "manifest.json"
        m.save(manifest_path)

        loaded = Manifest.load(manifest_path)

        assert len(loaded.entries) == len(urls)
        for url in urls:
            assert url in loaded.entries
            assert loaded.entries[url].url == url
            assert loaded.entries[url].title == f"Page {urls.index(url)}"


class TestE2EOutputValidation:
    """Validate the actual e2e output from the project."""

    def test_e2e_output_has_index(self):
        """e2e_output should have an index.md."""
        index_path = Path("tests/e2e_output/index.md")
        assert index_path.exists()
        content = index_path.read_text(encoding="utf-8")
        assert "Site Contents" in content

    def test_e2e_output_has_guides(self):
        """e2e_output should have a guides/ subdirectory."""
        guides_path = Path("tests/e2e_output/guides")
        assert guides_path.is_dir()

    def test_e2e_output_has_markdown_files(self):
        """e2e_output should contain .md files."""
        md_files = list(Path("tests/e2e_output").rglob("*.md"))
        assert len(md_files) > 0

    def test_e2e_output2_has_markdown(self):
        """e2e_output2 should also contain .md files."""
        md_files = list(Path("tests/e2e_output2").rglob("*.md"))
        assert len(md_files) > 0
