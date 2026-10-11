"""Data models for sitescrape output.

Defines the ScrapedPage type and other shared data structures
used across the scraper pipeline and consumed by downstream
consumers (e.g. the React Native docs app).
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScrapedPage:
    """Represents a single crawled-and-converted page.

    This is the primary data structure consumed by the React Native
    docs app (Phase 2). The JSON index and tarball formats are both
    serializations of this model.
    """

    slug: str
    """File-system-safe path relative to output root,
    without the .md extension. Examples: "index", "guides/setup"."""

    title: str
    """Human-readable page title from the front matter."""

    source_url: str
    """The original source URL from which this page was crawled."""

    scraped_at: str
    """When the page was scraped, ISO 8601 format."""

    content: str = ""
    """Markdown body only — no YAML front matter."""

    assets: list[str] = field(default_factory=list)
    """Relative asset paths for images referenced by this page,
    e.g. ["assets/a1b2c3d4e5f6.png"]."""

    breadcrumbs: list[str] = field(default_factory=list)
    """Breadcrumb trail from root to this page,
    e.g. ["Home", "Guides", "Setup"]."""

    headings: list[dict] = field(default_factory=list)
    """Table of contents extracted from h1-h6 elements,
    e.g. [{"level": 2, "text": "Installation"}, ...]."""

    @property
    def display_title(self) -> str:
        """Human-readable display title (slug with hyphens/underscores replaced)."""
        if self.title:
            return self.title
        return self.slug.replace("-", " ").replace("_", " ").title()


@dataclass
class ScrapedIndex:
    """Top-level JSON index structure for packaged output."""

    version: str = "1.0"
    """Schema version."""

    generated_at: str = ""
    """ISO 8601 timestamp of package generation."""

    pages: list[ScrapedPage] = field(default_factory=list)
    """Array of ScrapedPage objects."""

    @property
    def page_count(self) -> int:
        """Number of pages in this index."""
        return len(self.pages)

    def page_by_slug(self, slug: str) -> Optional[ScrapedPage]:
        """Look up a page by its slug."""
        for page in self.pages:
            if page.slug == slug:
                return page
        return None

    def pages_by_directory(self, directory: str) -> list[ScrapedPage]:
        """Get all pages in a directory level."""
        return [p for p in self.pages if p.slug.startswith(directory + "/")]

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "pages": [
                {
                    "slug": p.slug,
                    "title": p.title,
                    "sourceUrl": p.source_url,
                    "scrapedAt": p.scraped_at,
                    "content": p.content,
                    "assets": p.assets,
                    "breadcrumbs": p.breadcrumbs,
                    "headings": p.headings,
                }
                for p in self.pages
            ],
        }


@dataclass
class ManifestEntry:
    """A single entry in the crawl manifest (cache/manifest.json).

    This is the raw crawl output before conversion.
    """

    url: str
    """The URL that was crawled."""

    file: str
    """Filename of the cached HTML file."""

    title: str
    """Page title at crawl time (may be empty)."""

    fetch_time: str
    """Timestamp when the page was fetched, ISO-ish format."""
