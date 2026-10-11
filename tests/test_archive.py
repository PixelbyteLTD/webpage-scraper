"""Tests for the sitescrape archive (package) module."""

import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sitescrape.archive import (
    _collect_files,
    _create_json_index,
    _create_tarball,
    _discover_from_filesystem,
    _extract_breadcrumbs,
    _get_assets_from_content,
    _generate_json_index,
    _has_secrets,
    _is_excluded,
    do_archive,
)
from sitescrape.models import ScrapedIndex, ScrapedPage


# ── Exclusion helpers ──────────────────────────────────────────────────


class TestExclusion:
    def test_auth_dir_excluded(self, tmp_path):
        # _is_excluded needs a base path to compute relative paths
        base = tmp_path / "docs"
        base.mkdir()
        auth_dir = base / "auth"
        auth_dir.mkdir()
        path = auth_dir / "state.json"
        assert _is_excluded(path, base)

    def test_cache_dir_excluded(self, tmp_path):
        base = tmp_path / "docs"
        base.mkdir()
        cache_dir = base / "cache"
        cache_dir.mkdir()
        path = cache_dir / "manifest.json"
        assert _is_excluded(path, base)

    def test_pycache_excluded(self, tmp_path):
        base = tmp_path / "src"
        base.mkdir()
        pycache = base / "__pycache__"
        pycache.mkdir()
        path = pycache / "module.cpython-311.pyc"
        assert _is_excluded(path, base)

    def test_git_dir_excluded(self, tmp_path):
        base = tmp_path / "repo"
        base.mkdir()
        git_dir = base / ".git"
        git_dir.mkdir()
        path = git_dir / "config"
        assert _is_excluded(path, base)

    def test_normal_md_included(self, tmp_path):
        path = tmp_path / "docs" / "index.md"
        assert not _is_excluded(path)

    def test_asset_png_included(self, tmp_path):
        path = tmp_path / "assets" / "img.png"
        assert not _is_excluded(path)


class TestSecretDetection:
    def test_env_file_detected(self, tmp_path):
        assert _has_secrets(tmp_path / ".env")
        assert _has_secrets(tmp_path / "config.env")

    def test_secret_file_detected(self, tmp_path):
        assert _has_secrets(tmp_path / "secrets.yml")
        assert _has_secrets(tmp_path / "my-secret.txt")

    def test_token_file_detected(self, tmp_path):
        assert _has_secrets(tmp_path / "api-token.json")
        assert _has_secrets(tmp_path / "bearer_token")

    def test_normal_file_not_detected(self, tmp_path):
        assert not _has_secrets(tmp_path / "index.md")
        assert not _has_secrets(tmp_path / "setup.md")


# ── Asset extraction ──────────────────────────────────────────────────


class TestAssetExtraction:
    def test_extracts_asset_paths(self):
        content = '![img](assets/a1b2.png) and [img2](assets/f7e8.jpg)'
        assets = _get_assets_from_content(content)
        assert "a1b2.png" in assets
        assert "f7e8.jpg" in assets

    def test_no_assets(self):
        content = "Just plain text with no images."
        assert _get_assets_from_content(content) == []

    def test_deduplicates(self):
        content = '![img](assets/a1b2.png) [img2](assets/a1b2.png)'
        assets = _get_assets_from_content(content)
        assert assets.count("a1b2.png") == 1


# ── Breadcrumbs ──────────────────────────────────────────────────────


class TestBreadcrumbs:
    def test_root_slug(self):
        assert _extract_breadcrumbs("index") == ["Home"]
        assert _extract_breadcrumbs("") == ["Home"]

    def test_nested_slug(self):
        assert _extract_breadcrumbs("guides/setup") == ["Guides", "Setup"]

    def test_deep_slug(self):
        assert _extract_breadcrumbs("reference/api/auth") == [
            "Reference", "Api", "Auth"
        ]

    def test_underscored_slug(self):
        # Underscores become spaces, title-cased
        assert _extract_breadcrumbs("my_faq") == ["My Faq"]


# ── JSON index generation ─────────────────────────────────────────────


class TestGenerateJsonIndex:
    def test_from_filesystem_fallback(self, tmp_path):
        """When no index.md exists, discover pages from filesystem."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "index.md").write_text("---\ntitle: Home\n---\n\nHome")
        guides = docs / "guides"
        guides.mkdir()
        (guides / "setup.md").write_text("---\ntitle: Setup\n---\n\ncontent here")
        (guides / "faq.md").write_text("---\ntitle: FAQ\n---\n\nfaq content")

        index = _generate_json_index(docs)

        assert index["version"] == "1.0"
        assert index["generated_at"] != ""
        # Only non-index content pages are discovered via filesystem
        assert len(index["pages"]) >= 2

        slugs = [p["slug"] for p in index["pages"]]
        assert "guides/setup" in slugs
        assert "guides/faq" in slugs

    def test_from_index_md(self, tmp_path):
        """When index.md exists, parse it for page discovery."""
        docs = tmp_path / "docs"
        docs.mkdir()

        # Create index.md with links
        (docs / "index.md").write_text(
            "---\ntitle: Contents\nsource_url: /\nscraped_at: 2026-01-01\n---\n\n"
            "# Contents\n\n- [Home](index.md)\n- [Setup](guides/setup.md)\n"
        )

        guides = docs / "guides"
        guides.mkdir()
        (guides / "setup.md").write_text(
            "---\ntitle: Setup Guide\nsource_url: /guides/setup\nscraped_at: 2026-01-01\n---\n\nSetup content"
        )

        index = _generate_json_index(docs)
        slugs = [p["slug"] for p in index["pages"]]

        assert "index" in slugs
        assert "guides/setup" in slugs

        # Check page fields
        for p in index["pages"]:
            if p["slug"] == "guides/setup":
                assert p["title"] == "Setup Guide"
                assert p["sourceUrl"] == "/guides/setup"
                assert p["content"] == "Setup content"

    def test_empty_directory(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        index = _generate_json_index(docs)
        assert index["pages"] == []

    def test_discover_from_filesystem(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "index.md").write_text("---\ntitle: Home\n---\n\nHome")
        (docs / "about.md").write_text("---\ntitle: About\n---\n\nAbout us")

        pages = _discover_from_filesystem(docs)
        slugs = {p["slug"] for p in pages}
        # index.md is skipped (it's the TOC), but about.md is included
        assert "about" in slugs


# ── _create_json_index ─────────────────────────────────────────────────


class TestCreateJsonIndex:
    def test_creates_file(self, tmp_path):
        index_data = {
            "version": "1.0",
            "generated_at": "2026-01-01T00:00:00Z",
            "pages": [
                {
                    "slug": "index",
                    "title": "Home",
                    "sourceUrl": "/",
                    "scrapedAt": "2026-01-01",
                    "content": "",
                    "assets": [],
                    "breadcrumbs": ["Home"],
                }
            ],
        }
        _create_json_index(index_data, str(tmp_path / "out.json"))
        assert (tmp_path / "out.json").exists()

        loaded = json.loads((tmp_path / "out.json").read_text())
        assert loaded["version"] == "1.0"
        assert len(loaded["pages"]) == 1

    def test_auto_generated_name(self, tmp_path):
        index_data = {"version": "1.0", "generated_at": "", "pages": []}
        output_file = tmp_path / "auto-output.json"
        _create_json_index(index_data, str(output_file))
        assert output_file.exists()


# ── _create_tarball ─────────────────────────────────────────────────────


class TestCreateTarball:
    def test_creates_tarball(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "index.md").write_text("# Home")
        (docs / "about.md").write_text("# About")

        index_data = {
            "version": "1.0",
            "generated_at": "2026-01-01T00:00:00Z",
            "pages": [
                {"slug": "index", "title": "Home", "sourceUrl": "/", "scrapedAt": "", "content": "", "assets": [], "breadcrumbs": ["Home"]}
            ],
        }
        files = [docs / "index.md", docs / "about.md"]

        _create_tarball(docs, files, index_data, str(tmp_path / "out.tar.gz"))

        tar_path = tmp_path / "out.tar.gz"
        assert tar_path.exists()
        assert tar_path.stat().st_size > 0

        # Verify contents
        with tarfile.open(str(tar_path), "r:gz") as tar:
            names = tar.getnames()
            assert "index.json" in names
            assert "index.md" in names
            assert "about.md" in names

    def test_tarball_auto_name(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "index.md").write_text("# Home")
        index_data = {"version": "1.0", "generated_at": "", "pages": []}

        output_file = tmp_path / "auto.tar.gz"
        _create_tarball(docs, [], index_data, str(output_file))

        assert output_file.exists()
        assert output_file.stat().st_size > 0


# ── ScrapedPage model ──────────────────────────────────────────────────


class TestScrapedPage:
    def test_basic_creation(self):
        page = ScrapedPage(
            slug="guides/setup",
            title="Setup Guide",
            source_url="https://example.com/guides/setup",
            scraped_at="2026-01-01T00:00:00Z",
            content="Setup content",
        )
        assert page.slug == "guides/setup"
        assert page.title == "Setup Guide"
        assert page.assets == []
        assert page.breadcrumbs == []

    def test_display_title_from_slug(self):
        page = ScrapedPage(
            slug="my-test-page",
            title="",
            source_url="",
            scraped_at="",
        )
        assert page.display_title == "My Test Page"

    def test_display_title_from_title(self):
        page = ScrapedPage(
            slug="foo",
            title="Real Title",
            source_url="",
            scraped_at="",
        )
        assert page.display_title == "Real Title"


class TestScrapedIndex:
    def test_creation(self):
        index = ScrapedIndex(
            version="1.0",
            generated_at="2026-01-01T00:00:00Z",
            pages=[
                ScrapedPage(
                    slug="index",
                    title="Home",
                    source_url="/",
                    scraped_at="2026-01-01",
                )
            ],
        )
        assert index.page_count == 1

    def test_page_by_slug(self):
        index = ScrapedIndex(
            pages=[
                ScrapedPage(slug="index", title="Home", source_url="/", scraped_at=""),
                ScrapedPage(slug="guides/setup", title="Setup", source_url="/setup", scraped_at=""),
            ]
        )
        found = index.page_by_slug("guides/setup")
        assert found is not None
        assert found.title == "Setup"

    def test_page_by_slug_not_found(self):
        index = ScrapedIndex(pages=[])
        assert index.page_by_slug("missing") is None

    def test_pages_by_directory(self):
        index = ScrapedIndex(
            pages=[
                ScrapedPage(slug="index", title="Home", source_url="/", scraped_at=""),
                ScrapedPage(slug="guides/setup", title="Setup", source_url="/setup", scraped_at=""),
                ScrapedPage(slug="guides/faq", title="FAQ", source_url="/faq", scraped_at=""),
                ScrapedPage(slug="reference/api", title="API", source_url="/api", scraped_at=""),
            ]
        )
        guides = index.pages_by_directory("guides")
        assert len(guides) == 2
        assert all(p.slug.startswith("guides/") for p in guides)

    def test_to_dict(self):
        index = ScrapedIndex(
            version="2.0",
            generated_at="2026-01-01T00:00:00Z",
            pages=[
                ScrapedPage(
                    slug="index",
                    title="Home",
                    source_url="/",
                    scraped_at="2026-01-01",
                    content="Hello",
                    assets=["assets/img.png"],
                )
            ],
        )
        d = index.to_dict()
        assert d["version"] == "2.0"
        assert len(d["pages"]) == 1
        assert d["pages"][0]["slug"] == "index"


# ── do_archive integration ─────────────────────────────────────────────


class TestDoArchive:
    def test_json_format(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "index.md").write_text("title: Home\n\nHello")

        out = tmp_path / "out.json"
        do_archive(input_dir=str(docs), format="json-index", output=str(out))

        assert out.exists()
        data = json.loads(out.read_text())
        assert "pages" in data

    def test_tarball_format(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "index.md").write_text("# Home")

        out = tmp_path / "out.tar.gz"
        do_archive(input_dir=str(docs), format="tarball", output=str(out))

        assert out.exists()
        with tarfile.open(str(out), "r:gz") as tar:
            names = tar.getnames()
            assert "index.json" in names

    def test_invalid_input(self, tmp_path):
        from typer import Exit
        with pytest.raises(Exit):
            do_archive(input_dir=str(tmp_path / "nonexistent"), format="tarball")

    def test_invalid_format(self, tmp_path):
        from typer import Exit
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "index.md").write_text("# Home")

        with pytest.raises(Exit):
            do_archive(input_dir=str(docs), format="invalid")
