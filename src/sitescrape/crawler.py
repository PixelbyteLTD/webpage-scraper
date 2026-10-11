"""Crawler: breadth-first crawl of a logged-in site using Playwright."""

import json
import os
import re
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import typer
from playwright.sync_api import sync_playwright

# --- helpers ---

_LOGIN_SELECTORS = [
    'input[type="password"]',
    'input[name="password"]',
    'input[name="passwd"]',
    'input[name="pass"]',
    '#password',
    '#login-password',
    '.password',
]

_LOGIN_TEXT_PATTERNS = [
    r"[Ll]og\s*[Ii]n",
    r"[Ss]ign\s*[Ii]n",
    r"[Ll]og\s*[Oo]ut",
    r"[Ll]og\s*[_-]?[Ii][Nn]",
    r"authenticate",
    r"sign[_-]?[_\s]*up",
]

_AVOID_LINK_TEXT_RE = re.compile(
    r"(logout|sign\s*out|signout|delete\s*account|sign\s*out|delete\s*profile|settings|sign\s*[-_]?[Ii]n|sign\s*[-_]?[Uu]p)",
    re.IGNORECASE,
)

# --- URL normalisation ---


def _strip_tracking(url: str) -> str:
    """Remove fragments and common tracking query params."""
    parsed = urllib.parse.urlparse(url)
    keep = {
        p for p in parsed.query.split("&")
        if not re.match(
            r"^(fbclid|gclid|utm_[^=]*|mc_(?:eid|rid)|wa_.*)$", p.split("=")[0], re.I
        )
    }
    clean_qs = "&".join(sorted(keep))
    cleaned = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_qs, "")
    )
    return cleaned


def _normalise(url: str, base: str) -> str | None:
    """Resolve relative URL against base, strip tracking, validate domain+path.

    Returns None if the URL is outside the start URL's domain or path prefix.
    """
    parsed_start = urlparse(base)
    if not parsed_start.scheme or not parsed_start.netloc:
        return None

    resolved = urljoin(base, url)
    resolved_parsed = urlparse(resolved)

    # Same domain only
    if resolved_parsed.netloc and resolved_parsed.netloc != parsed_start.netloc:
        return None

    # Use base's scheme if missing
    if not resolved_parsed.scheme:
        resolved = urljoin(base, resolved)
        resolved_parsed = urlparse(resolved)

    # Must be http/https
    if resolved_parsed.scheme not in ("http", "https"):
        return None

    # Strip tracking
    cleaned = _strip_tracking(resolved)
    cleaned_parsed = urlparse(cleaned)

    # Stay under the start path's directory by default
    start_path = parsed_start.path.rstrip("/") or "/"
    cleaned_path = cleaned_parsed.path.rstrip("/") or "/"

    # If start_path is root, allow everything under the domain
    if start_path == "/":
        pass
    else:
        # Use the parent directory as boundary (e.g. /products/item/1 → /products/)
        parent = start_path.rsplit("/", 1)[0] or "/"
        if not cleaned_path.startswith(parent + "/") and cleaned_path != parent:
            return None

    return cleaned


# --- manifest helpers ---


@dataclass
class ManifestEntry:
    url: str
    file: str
    title: str
    fetch_time: str  # ISO-ish


@dataclass
class Manifest:
    entries: dict[str, ManifestEntry] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        data = [
            {
                "url": e.url,
                "file": e.file,
                "title": e.title,
                "fetch_time": e.fetch_time,
            }
            for e in self.entries.values()
        ]
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def load(path: Path) -> "Manifest":
        if not path.is_file():
            return Manifest()
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = {}
        for item in data:
            entries[item["url"]] = ManifestEntry(**item)
        return Manifest(entries=entries)


# --- redirect / login detection ---


def _is_login_page(page) -> bool:
    """Heuristic: does the current page look like a login page?"""
    try:
        password_found = any(
            page.locator(sel).count() > 0 for sel in _LOGIN_SELECTORS
        )
    except Exception:
        password_found = False

    if password_found:
        return True

    body_text = ""
    try:
        body_text = page.inner_text("body")
    except Exception:
        pass

    page_title = ""
    try:
        page_title = page.title() or ""
    except Exception:
        pass

    preview = f"{page_title} {body_text[:500]}" if body_text else page_title

    for pattern in _LOGIN_TEXT_PATTERNS:
        if re.search(pattern, preview):
            return True

    return False


# --- crawler ---


def _has_avoid_links(page) -> bool:
    """Check whether the page has links that should be avoided."""
    try:
        links = page.query_selector_all("a[href]")
    except Exception:
        return False

    for link in links:
        text = link.inner_text().strip()
        href = link.get_attribute("href") or ""
        if _AVOID_LINK_TEXT_RE.search(text):
            return True
        # Also avoid hrefs that look like logout/logout/etc
        if _AVOID_LINK_TEXT_RE.search(href):
            return True
    return False


def _extract_links(page, base_url: str) -> list[str]:
    """Extract all <a href> from the page, normalised."""
    links: list[str] = []
    try:
        elements = page.query_selector_all("a[href]")
    except Exception:
        return links

    for el in elements:
        href = el.get_attribute("href")
        if not href:
            continue
        href = href.strip()
        if href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        normalised = _normalise(href, base_url)
        if normalised:
            links.append(normalised)
    return links


@dataclass
class CrawlStats:
    saved: int = 0
    skipped: int = 0
    failed: int = 0
    login_detected: bool = False


def _slugify_url(url: str) -> str:
    """Turn a URL into a safe filename."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    path = path.strip("/")
    if not path:
        path = "index"
    # Replace unsafe chars
    safe = re.sub(r"[^\w\-.]", "_", path)
    return safe + ".html"


def do_crawl(
    start_url: str = typer.Argument(..., help="URL to start crawling from"),
    max_pages: int = typer.Option(500, "--max-pages", help="Maximum pages to crawl."),
    delay: float = typer.Option(1.5, "--delay", help="Seconds between requests."),
    include: list[str] = typer.Option(
        None, "--include", help="Only include URLs matching these path prefixes or regexes."
    ),
    exclude: list[str] = typer.Option(
        None, "--exclude", help="Skip URLs matching these path prefixes or regexes."
    ),
    auth_dir: str = typer.Option("./auth", "--auth-dir", help="Directory containing state.json."),
    cache_dir: str = typer.Option("./cache", "--cache-dir", help="Directory to save crawled pages."),
    refresh: bool = typer.Option(False, "--refresh", help="Re-crawl pages already in manifest."),
) -> None:
    """Breadth-first crawl of a site using the saved session state."""

    # Validate auth state
    state_path = Path(auth_dir) / "state.json"
    if not state_path.is_file():
        typer.echo(f"Error: auth state file not found at {state_path}", err=True)
        raise typer.Exit(1)

    # Ensure output dir
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    # Load manifest
    manifest_file = cache_path / "manifest.json"
    manifest = Manifest.load(manifest_file)

    # Determine base domain/path
    parsed_start = urlparse(_strip_tracking(start_url))
    if not parsed_start.scheme or not parsed_start.netloc:
        typer.echo(f"Error: invalid start URL: {start_url}", err=True)
        raise typer.Exit(1)

    base_domain = parsed_start.netloc

    # Compile include/exclude patterns
    include_pats: list[re.Pattern] = []
    for pat in (include or []):
        try:
            include_pats.append(re.compile(pat))
        except re.error as exc:
            typer.echo(f"Warning: invalid --include pattern {pat!r}: {exc}", err=True)

    exclude_pats: list[re.Pattern] = []
    for pat in (exclude or []):
        try:
            exclude_pats.append(re.compile(pat))
        except re.error as exc:
            typer.echo(f"Warning: invalid --exclude pattern {pat!r}: {exc}", err=True)

    def _should_include(url: str) -> bool:
        if include_pats:
            return any(p.search(url) for p in include_pats)
        if exclude_pats:
            return not any(p.search(url) for p in exclude_pats)
        return True

    stats = CrawlStats()
    visited: set[str] = set()
    queue: deque[str] = deque()

    # Seed
    cleaned_start = _strip_tracking(start_url)
    queue.append(cleaned_start)

    print(f"\nCrawling {cleaned_start}")
    print(f"  max-pages={max_pages}, delay={delay}s, cache={cache_path}\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()

        while queue and stats.saved + stats.failed < max_pages:
            url = queue.popleft()

            if url in visited:
                stats.skipped += 1
                continue

            if not _should_include(url):
                stats.skipped += 1
                continue

            # Skip already cached unless --refresh
            if url in manifest.entries and not refresh:
                stats.skipped += 1
                continue

            visited.add(url)
            slug = _slugify_url(url)
            out_file = cache_path / slug

            print(f"[{stats.saved + stats.failed + 1}] Crawling: {url}")

            try:
                response = page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception as exc:
                print(f"      ❌ Failed: {exc}")
                stats.failed += 1
                continue

            # Check for redirect to login page
            current_url = page.url
            if _is_login_page(page):
                print(f"      ⚠️  Redirected to login page. Stopping crawl.\n"
                      f"   Run `sitescrape login {start_url}` to refresh your session, then re-run crawl.")
                stats.login_detected = True
                break

            # Check for 401/403 (unauthorised)
            status = response.status if response else 0
            if status in (401, 403):
                print(f"      ⚠️  HTTP {status} — unauthorised. Stopping crawl.\n"
                      f"   Run `sitescrape login {start_url}` to refresh your session, then re-run crawl.")
                stats.login_detected = True
                break

            # Skip if redirect to login
            if "/login" in current_url.lower() or "/signin" in current_url.lower():
                print(f"      ⚠️  Redirected to login page. Stopping crawl.\n"
                      f"   Run `sitescrape login {start_url}` to refresh your session, then re-run crawl.")
                stats.login_detected = True
                break

            # Extract title and links
            title = page.title() or ""
            try:
                html = page.content()
            except Exception:
                html = ""

            # Check for avoid links before enqueuing children
            has_avoid = _has_avoid_links(page)
            if has_avoid:
                print(f"      ⚠️  Page contains logout/avoid links — not following links on this page.")

            # Save
            try:
                out_file.write_text(html, encoding="utf-8")
                stats.saved += 1
                manifest.entries[url] = ManifestEntry(
                    url=url,
                    file=str(out_file),
                    title=title,
                    fetch_time=time.strftime("%Y-%m-%dT%H:%M:%S"),
                )
                print(f"      ✅ Saved ({len(html)} bytes)  title: {title}")
            except Exception as exc:
                print(f"      ❌ Failed to save: {exc}")
                stats.failed += 1
                continue

            # Enqueue child links
            if not has_avoid:
                children = _extract_links(page, url)
                for child in children:
                    if child not in visited:
                        queue.append(child)

            # Delay
            time.sleep(delay)

        browser.close()

    # Save manifest
    manifest.save(manifest_file)

    # Summary
    print(f"\n{'='*50}")
    print(f"Crawl complete!")
    print(f"  Pages saved:   {stats.saved}")
    print(f"  Pages skipped: {stats.skipped}")
    print(f"  Pages failed:  {stats.failed}")
    if stats.login_detected:
        print(f"\n  ⚠️  Login page detected — crawl stopped early.\n"
              f"  Run `sitescrape login {start_url}` to refresh your session.")
    print(f"{'='*50}\n")
