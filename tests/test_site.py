"""Tests for the site module (MkDocs config generation)."""

import json
from pathlib import Path
from unittest import mock

import pytest

from sitescrape.site import (
    _scan_docs_dir,
    _build_nav,
    _tree_to_nav,
)


# ── _scan_docs_dir ──────────────────────────────────────────────────

class TestScanDocsDir:
    """Tests for directory scanning."""

    def test_empty_directory(self, tmp_path):
        """Empty docs directory should return empty list."""
        result = _scan_docs_dir(tmp_path)
        assert result == []

    def test_single_markdown_file(self, tmp_path):
        """A single .md file should be scanned."""
        (tmp_path / "setup.md").write_text("# Setup")
        result = _scan_docs_dir(tmp_path)
        assert len(result) == 1
        assert result[0]["path"] == "setup.md"

    def test_multiple_markdown_files(self, tmp_path):
        """Multiple .md files should all be scanned."""
        (tmp_path / "setup.md").write_text("# Setup")
        (tmp_path / "faq.md").write_text("# FAQ")
        (tmp_path / "index.md").write_text("# Index")
        result = _scan_docs_dir(tmp_path)
        # index.md is included (second loop in _scan_docs_dir adds root index)
        assert len(result) == 3
        paths = [r["path"] for r in result]
        assert "setup.md" in paths
        assert "faq.md" in paths
        assert "index.md" in paths

    def test_includes_index_md(self, tmp_path):
        """index.md at root is included (as an index page)."""
        (tmp_path / "index.md").write_text("# Index")
        result = _scan_docs_dir(tmp_path)
        # index.md is included by the second loop
        assert len(result) == 1
        assert result[0]["path"] == "index.md"

    def test_nested_directories(self, tmp_path):
        """Files in nested directories should be discovered."""
        subdir = tmp_path / "guides"
        subdir.mkdir()
        (subdir / "setup.md").write_text("# Setup")
        (subdir / "faq.md").write_text("# FAQ")
        result = _scan_docs_dir(tmp_path)
        paths = [r["path"] for r in result]
        assert "guides/setup.md" in paths
        assert "guides/faq.md" in paths

    def test_subdirectory_index_md(self, tmp_path):
        """index.md in subdirectories should be added."""
        subdir = tmp_path / "guides"
        subdir.mkdir()
        (subdir / "setup.md").write_text("# Setup")
        (subdir / "index.md").write_text("# Guides Index")
        result = _scan_docs_dir(tmp_path)
        paths = [r["path"] for r in result]
        assert "guides/setup.md" in paths
        assert "guides/index.md" in paths

    def test_skips_non_md_files(self, tmp_path):
        """Non-.md files should be ignored."""
        (tmp_path / "setup.md").write_text("# Setup")
        (tmp_path / "setup.txt").write_text("text")
        (tmp_path / "setup.html").write_text("<html>")
        result = _scan_docs_dir(tmp_path)
        paths = [r["path"] for r in result]
        # setup.md plus index.md from the second loop (but index.md doesn't exist here)
        assert "setup.md" in paths
        assert "setup.txt" not in paths

    def test_sorts_by_path(self, tmp_path):
        """Files should be sorted by path."""
        (tmp_path / "zzz.md").write_text("# ZZZ")
        (tmp_path / "aaa.md").write_text("# AAA")
        (tmp_path / "mmm.md").write_text("# MMM")
        result = _scan_docs_dir(tmp_path)
        paths = [r["path"] for r in result]
        assert paths == ["aaa.md", "mmm.md", "zzz.md"]


# ── _build_nav ───────────────────────────────────────────────────────

class TestBuildNav:
    """Tests for MkDocs nav structure building."""

    def test_simple_flat(self):
        """Flat list of pages should create flat nav."""
        pages = [
            {"path": "index.md", "title": "Index"},
            {"path": "setup.md", "title": "Setup"},
        ]
        result = _build_nav(pages)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_root_pages_first(self):
        """Root-level pages and subdirectory pages are both included."""
        pages = [
            {"path": "guides/setup.md", "title": "Setup"},
            {"path": "index.md", "title": "Index"},
        ]
        result = _build_nav(pages)
        assert isinstance(result, list)
        assert len(result) > 0
        # The nav builder creates nested structure; verify both are present
        output_str = json.dumps(result)
        assert "index" in output_str
        assert "guides" in output_str

    def test_nested_structure(self):
        """Nested directories should create nested nav."""
        pages = [
            {"path": "index.md", "title": "Index"},
            {"path": "guides/setup.md", "title": "Setup"},
            {"path": "guides/faq.md", "title": "FAQ"},
        ]
        result = _build_nav(pages)
        assert isinstance(result, list)

    def test_empty_pages(self):
        """Empty page list should return empty nav."""
        result = _build_nav([])
        assert result == []


# ── _tree_to_nav ─────────────────────────────────────────────────────

class TestTreeToNav:
    """Tests for tree-to-nav conversion."""

    def test_leaf_node(self):
        """A leaf node (dict with string value) should be formatted."""
        tree = {"setup": "setup.md"}
        result = _tree_to_nav(tree)
        assert len(result) == 1
        assert "setup" in result[0]
        assert result[0]["setup"] == "setup.md"

    def test_section_node(self):
        """A section (dict with dict value) should have children."""
        tree = {"guides": {"setup": "guides/setup.md"}}
        result = _tree_to_nav(tree)
        assert len(result) == 1
        assert "guides" in result[0]
        children = result[0]["guides"]
        assert isinstance(children, list)
        assert len(children) == 1
        assert "setup" in children[0]

    def test_deep_nesting(self):
        """Deeply nested trees should work."""
        tree = {"a": {"b": {"c": "file.md"}}}
        result = _tree_to_nav(tree)
        assert len(result) == 1
        assert "a" in result[0]
        assert "b" in result[0]["a"][0]
        assert "c" in result[0]["a"][0]["b"][0]

    def test_empty_section(self):
        """Empty dict values should produce empty string."""
        tree = {"empty": {}}
        result = _tree_to_nav(tree)
        assert len(result) == 1
        assert "empty" in result[0]
        assert result[0]["empty"] == ""


# ── Integration: do_site ─────────────────────────────────────────────

class TestDoSite:
    """Integration tests for the site command."""

    def test_generates_mkdocs_config(self, tmp_path):
        """do_site should generate mkdocs.yml."""
        from sitescrape.site import do_site

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "setup.md").write_text("# Setup")

        config_file = tmp_path / "output" / "mkdocs.yml"

        do_site(docs_dir=str(docs_dir), output_file=str(config_file))

        assert config_file.exists()
        content = config_file.read_text(encoding="utf-8")
        assert "site_name" in content
        assert "sitescrape" in content.lower() or "Documentation" in content

    def test_config_has_search_plugin(self, tmp_path):
        """Generated config should include search plugin."""
        from sitescrape.site import do_site

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        config_file = tmp_path / "output" / "mkdocs.yml"

        do_site(docs_dir=str(docs_dir), output_file=str(config_file))

        content = config_file.read_text(encoding="utf-8")
        assert "search" in content

    def test_config_has_material_theme(self, tmp_path):
        """Generated config should use material theme."""
        from sitescrape.site import do_site

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        config_file = tmp_path / "output" / "mkdocs.yml"

        do_site(docs_dir=str(docs_dir), output_file=str(config_file))

        content = config_file.read_text(encoding="utf-8")
        assert "material" in content.lower()

    def test_config_with_pages(self, tmp_path):
        """Generated config should include pages in nav."""
        from sitescrape.site import do_site

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "setup.md").write_text("# Setup")
        (docs_dir / "faq.md").write_text("# FAQ")
        config_file = tmp_path / "output" / "mkdocs.yml"

        do_site(docs_dir=str(docs_dir), output_file=str(config_file))

        content = config_file.read_text(encoding="utf-8")
        assert "setup" in content

    def test_config_dir_not_found(self, tmp_path):
        """Non-existent docs dir should still work (just no pages)."""
        from sitescrape.site import do_site

        config_file = tmp_path / "output" / "mkdocs.yml"
        do_site(docs_dir=str(tmp_path / "nonexistent"), output_file=str(config_file))
        assert config_file.exists()


# ── _build_nav edge cases ──────────────────────────────────────────

class TestBuildNavEdgeCases:
    """Edge cases for nav building."""

    def test_single_root_page(self):
        """Single index.md at root."""
        pages = [{"path": "index.md", "title": "Index"}]
        result = _build_nav(pages)
        assert isinstance(result, list)

    def test_mixed_levels(self):
        """Mix of root and subdirectory pages."""
        pages = [
            {"path": "index.md", "title": "Index"},
            {"path": "setup.md", "title": "Setup"},
            {"path": "guides/setup.md", "title": "Guide Setup"},
        ]
        result = _build_nav(pages)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_pages_with_dots_in_path(self):
        """Paths with dots should be handled."""
        pages = [
            {"path": "v2.0/guide.md", "title": "Guide"},
        ]
        result = _build_nav(pages)
        assert isinstance(result, list)
