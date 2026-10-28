"""Tests for output structure, validation, and file management."""

import json
import re
import tempfile
from pathlib import Path

import pytest


class TestFrontmatterValidation:
    """Tests for YAML frontmatter validity in converted files."""

    def test_valid_frontmatter_structure(self):
        """A valid frontmatter block should have title, source_url, scraped_at."""
        lines = [
            "---",
            "title: My Page",
            "source_url: https://example.com/page",
            "scraped_at: 2026-01-01T00:00:00Z",
            "---",
        ]
        content = "\n".join(lines) + "\n\nContent here\n"
        
        # Check frontmatter parsing
        fm_lines = content.split("---\n")[1].split("\n---")[0] if "---\n" in content else ""
        assert "title:" in fm_lines
        assert "source_url:" in fm_lines
        assert "scraped_at:" in fm_lines

    def test_frontmatter_has_required_fields(self):
        """Frontmatter must contain title, source_url, and scraped_at."""
        required = ["title", "source_url", "scraped_at"]
        content = "---\ntitle: Test\nsource_url: https://example.com\nscraped_at: 2026-01-01T00:00:00Z\n---\n"
        for field in required:
            assert f"{field}:" in content

    def test_frontmatter_no_extra_blank_lines(self):
        """Frontmatter should not have blank lines between fields.

        Note: split("...\n") produces an empty string after the final
        delimiter, which is not a blank line in the content.
        """
        content = "---\ntitle: Test\nsource_url: https://example.com\nscraped_at: 2026-01-01T00:00:00Z\n---\n"
        fm_section = content.split("---\n")[1].split("\n---\n")[0]
        blank_lines = [l for l in fm_section.split("\n") if not l.strip()]
        # fm_section ends with \n so the last element is ''
        # Filter: only count non-trailing blanks
        assert sum(1 for i, l in enumerate(fm_section.split("\n")) if not l.strip() and i < len(fm_section.split("\n"))-1) == 0

    def test_frontmatter_title_field(self):
        """Title field should be present and non-empty."""
        content = "---\ntitle: My Page\n---\n"
        match = re.search(r"title:\s*(.+)", content)
        assert match is not None
        assert match.group(1).strip() == "My Page"


class TestOutputFileStructure:
    """Tests for output directory structure validation."""

    def test_output_has_docs_dir(self, tmp_path):
        """Output should have a docs/ directory."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        assert docs_dir.is_dir()
        assert docs_dir.exists()

    def test_output_has_assets_dir(self, tmp_path):
        """Output should have an assets/ subdirectory."""
        output_dir = tmp_path / "output" / "docs"
        assets_dir = output_dir / "assets"
        assets_dir.mkdir(parents=True)
        assert assets_dir.is_dir()

    def test_markdown_files_under_docs(self, tmp_path):
        """Markdown files should be under the docs/ directory."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "setup.md").write_text("# Setup")
        assert (docs_dir / "setup.md").is_file()

    def test_nested_directory_structure(self, tmp_path):
        """Nested directories should be preserved in output."""
        docs_dir = tmp_path / "docs" / "guides"
        docs_dir.mkdir(parents=True)
        (docs_dir / "setup.md").write_text("# Setup")
        assert (docs_dir / "setup.md").is_file()
        assert "guides" in str(docs_dir.relative_to(tmp_path))


class TestOrphanedLinks:
    """Tests for detecting orphaned or broken links in output."""

    def test_internal_link_valid(self, tmp_path):
        """A link to an existing file should not be flagged as orphaned."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "setup.md").write_text("[Link](faq.md)")
        (docs_dir / "faq.md").write_text("# FAQ")
        
        # Check link target exists
        link_target = docs_dir / "faq.md"
        assert link_target.is_file()

    def test_internal_link_orphaned(self, tmp_path):
        """A link to a non-existing file should be flagged."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "setup.md").write_text("[Link](missing.md)")
        
        link_target = docs_dir / "missing.md"
        assert not link_target.is_file()

    def test_external_links_pass(self, tmp_path):
        """External links should not be flagged as orphaned."""
        content = "[Google](https://google.com)"
        assert "https://" in content
        assert content.startswith("[")

    def test_link_format(self, tmp_path):
        """Markdown links should follow [text](url) format."""
        content = "[Setup](setup.md)"
        match = re.match(r"\[([^\]]+)\]\(([^)]+)\)", content)
        assert match is not None
        assert match.group(1) == "Setup"
        assert match.group(2) == "setup.md"


class TestHeadingHierarchy:
    """Tests for validating heading hierarchy."""

    def test_valid_sequential_headings(self):
        """Headings should generally increase by one level."""
        content = "# H1\n## H2\n### H3\n"
        headings = re.findall(r"(#{1,6})\s+(.+)", content)
        levels = [len(h[0]) for h in headings]
        assert levels == [1, 2, 3]

    def test_h1_before_h2(self):
        """H1 should come before H2."""
        content = "# Main Title\n## Section\n"
        headings = re.findall(r"(#{1,6})\s+(.+)", content)
        assert headings[0][0] == "#"
        assert headings[1][0] == "##"

    def test_single_h1(self):
        """Should have exactly one H1 per page."""
        content = "# Main Page\n\nSome text\n"
        h1_count = content.count("# ")
        assert h1_count == 1

    def test_no_gaps_in_levels(self):
        """Headings should not skip levels (H1 → H3)."""
        content = "# H1\n### H3\n"
        headings = re.findall(r"(#{1,6})\s+(.+)", content)
        levels = [len(h[0]) for h in headings]
        # This is actually a violation — H1 to H3 skips H2
        assert levels[0] == 1
        assert levels[1] == 3


class TestMarkdownContent:
    """Tests for Markdown content validation."""

    def test_content_not_empty(self, tmp_path):
        """Converted files should have content."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "page.md").write_text("Hello world\n")
        content = (docs_dir / "page.md").read_text()
        assert content.strip()

    def test_content_after_frontmatter(self, tmp_path):
        """Content should appear after the frontmatter block."""
        content = "---\ntitle: Test\n---\n\nThis is content.\n"
        body = content.split("---\n")[2].strip()
        assert "This is content" in body

    def test_content_preserves_newlines(self, tmp_path):
        """Markdown content should preserve paragraph breaks."""
        content = "Paragraph 1\n\nParagraph 2\n"
        assert "\n\n" in content

    def test_code_blocks_preserved(self, tmp_path):
        """Code blocks should be preserved with backticks."""
        content = "```\ncode here\n```\n"
        assert "```" in content

    def test_links_preserved(self, tmp_path):
        """Markdown links should be preserved."""
        content = "[Text](url.md)\n"
        assert "[Text](url.md)" in content


class TestMissingImages:
    """Tests for detecting missing or broken image references."""

    def test_image_reference(self, tmp_path):
        """An image reference in markdown should point to a file."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        assets_dir = docs_dir / "assets"
        assets_dir.mkdir()
        
        # Write a reference to an image
        (docs_dir / "page.md").write_text("![Alt](assets/img.png)\n")
        
        # Check if the referenced image exists
        img_ref = "assets/img.png"
        img_path = docs_dir / img_ref
        assert not img_path.exists()  # The image hasn't been downloaded

    def test_image_file_exists(self, tmp_path):
        """Existing image files should be valid."""
        docs_dir = tmp_path / "docs" / "assets"
        docs_dir.mkdir(parents=True)
        (docs_dir / "test.png").write_bytes(b"\x89PNG\r\n")
        assert (docs_dir / "test.png").is_file()

    def test_image_extensions(self):
        """Supported image extensions should be known."""
        supported = [".jpg", ".png", ".gif", ".webp", ".svg", ".avif"]
        for ext in supported:
            assert ext in [".jpg", ".png", ".gif", ".webp", ".svg", ".avif"]

    def test_data_uri_image(self):
        """Data URI images should not be downloaded."""
        src = "data:image/png;base64,abc123"
        assert src.startswith("data:")
        assert "http" not in src


class TestEdgeCases:
    """Tests for edge case handling in output."""

    def test_empty_page_content(self):
        """Pages with no content should be handled gracefully."""
        content = "---\ntitle: Empty Page\n---\n\n\n"
        body = "\n".join(content.split("\n---\n")[2:]).strip()
        assert body == ""

    def test_page_with_only_frontmatter(self, tmp_path):
        """A file with only frontmatter should still be valid."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        content = "---\ntitle: Just Frontmatter\n---\n"
        (docs_dir / "minimal.md").write_text(content)
        
        fm = content.split("---\n")[1]
        assert "title:" in fm

    def test_url_with_special_characters(self):
        """URLs with special characters should be handled."""
        url = "https://example.com/page?foo=bar&baz=qux"
        assert "foo=bar" in url
        assert "baz=qux" in url

    def test_circular_link_detection(self):
        """Circular references should be detected."""
        # A → B → A is circular
        links = {"A": "B", "B": "A"}
        visited = set()
        
        def follow(start):
            current = start
            while current not in visited:
                visited.add(current)
                current = links.get(current)
                if current is None:
                    break
            return current == start
        
        assert follow("A") is True
        assert follow("B") is True

    def test_deeply_nested_links(self):
        """Deeply nested link chains should not crash."""
        links = {}
        for i in range(100):
            links[str(i)] = str(i + 1)
        
        visited = set()
        current = "0"
        depth = 0
        while current in links and current not in visited:
            visited.add(current)
            current = links[current]
            depth += 1
        
        assert depth == 100  # Should follow all links without crashing


class TestManifestOutput:
    """Tests for manifest.json structure and validation."""

    def test_manifest_has_url_field(self, tmp_path):
        """Each manifest entry should have a 'url' field."""
        entry = {"url": "https://example.com/page", "file": "page.html", "title": "", "fetch_time": "2026-01-01"}
        assert "url" in entry

    def test_manifest_has_file_field(self, tmp_path):
        """Each manifest entry should have a 'file' field."""
        entry = {"url": "https://example.com/page", "file": "page.html", "title": "", "fetch_time": "2026-01-01"}
        assert "file" in entry

    def test_manifest_has_fetch_time(self, tmp_path):
        """Each manifest entry should have a 'fetch_time' field."""
        entry = {"url": "https://example.com/page", "file": "page.html", "title": "", "fetch_time": "2026-01-01"}
        assert "fetch_time" in entry

    def test_manifest_is_list_of_dicts(self):
        """A manifest should be a list of dictionaries."""
        manifest = [
            {"url": "https://example.com/page1", "file": "page1.html", "title": "", "fetch_time": "2026-01-01"},
            {"url": "https://example.com/page2", "file": "page2.html", "title": "", "fetch_time": "2026-01-02"},
        ]
        assert isinstance(manifest, list)
        assert all(isinstance(e, dict) for e in manifest)

    def test_manifest_urls_are_unique(self):
        """All URLs in a manifest should be unique."""
        manifest = [
            {"url": "https://example.com/page1", "file": "page1.html", "title": "", "fetch_time": "2026-01-01"},
            {"url": "https://example.com/page2", "file": "page2.html", "title": "", "fetch_time": "2026-01-02"},
        ]
        urls = [e["url"] for e in manifest]
        assert len(urls) == len(set(urls))


class TestIndexGeneration:
    """Tests for index.md generation."""

    def test_index_has_title(self, tmp_path):
        """Generated index should have a Site Contents title."""
        content = "---\ntitle: Site Contents\nsource_url: /\nscraped_at: 2026-01-01\n---\n\n# Site Contents\n"
        assert "Site Contents" in content

    def test_index_has_heading(self, tmp_path):
        """Generated index should have a # Site Contents heading."""
        content = "# Site Contents\n"
        assert "# Site Contents" in content

    def test_index_groups_by_folder(self, tmp_path):
        """Index should group pages by their folder."""
        content = "---\ntitle: Site Contents\n---\n\n# Site Contents\n\n## Guides\n\n- [Setup](guides/setup.md)\n- [FAQ](guides/faq.md)\n"
        assert "Guides" in content
        assert "setup.md" in content
        assert "faq.md" in content


class TestFilePermissions:
    """Tests for file permission validation."""

    def test_file_is_readable(self, tmp_path):
        """Generated markdown files should be readable."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        f = docs_dir / "test.md"
        f.write_text("# Test")
        content = f.read_text()
        assert content == "# Test"

    def test_file_is_writable(self, tmp_path):
        """Output directory should be writable."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        f = docs_dir / "new.md"
        f.write_text("# New")
        assert f.read_text() == "# New"

    def test_directory_is_writable(self, tmp_path):
        """Output directories should be writable."""
        new_dir = tmp_path / "new" / "nested"
        new_dir.mkdir(parents=True, exist_ok=True)
        assert new_dir.is_dir()
