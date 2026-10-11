"""Crawler: breadth-first crawl of a logged-in site using Playwright.

Integrates Mozilla Readability for main-content extraction and
BeautifulSoup for structured data (tables, code blocks, headings).
Handles dynamic content, pagination, and produces clean JSON.
"""

import json
import random
import re
import signal
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import typer
from playwright.sync_api import sync_playwright

from sitescrape.extractor import extract, content_to_json
from sitescrape.logger import ScrapeReportHandler, get_logger
from sitescrape.models import PageData, CrawlResult, ExtractedContent
from sitescrape.retry import RateLimiter, retry
from sitescrape.shutdown import install_signal_handlers, is_shutdown_requested, ProgressSaver

# Retry configuration
base_delay: float = 1.0  # seconds before first retry
backoff_factor: float = 2.0  # multiplier for each subsequent retry

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


# --- pagination helpers ---

# Selectors that suggest "next page" functionality
_NEXT_PAGE_SELECTORS = [
    'a[rel="next"]',
    'a[aria-label="Next"]',
    'a[aria-label="Next page"]',
    'a[title="Next"]',
    'a[title="Next page"]',
    '.pagination a.next',
    '.paginated a.next',
    '.page-numbers a.next',
    '.wp-page-next a',
    'a.next',
    'a[aria-label*="next"]',
]

# Selectors for infinite scroll indicators
_SCROLL_SELECTORS = [
    '.load-more',
    '.load-more-btn',
    '.infinite-scroll-btn',
    '[data-action="load-more"]',
    '.load-more-trigger',
]


def _extract_next_page_url(page, base_url: str) -> str | None:
    """Find the 'next page' link on the current page."""
    try:
        for selector in _NEXT_PAGE_SELECTORS:
            next_link = page.query_selector(selector)
            if next_link:
                href = next_link.get_attribute("href")
                if href:
                    normalised = _normalise(href, base_url)
                    if normalised:
                        return normalised
    except Exception:
        pass
    return None


def _detect_infinite_scroll(page) -> bool:
    """Check if the page uses infinite scroll (has load-more elements)."""
    try:
        for selector in _SCROLL_SELECTORS:
            if page.query_selector(selector):
                return True
    except Exception:
        pass
    return False


def _wait_for_dynamic_content(page, timeout: int = 5000) -> bool:
    """Wait for network idle and dynamic content to settle.

    Returns True if we got content, False if timed out.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
        return True
    except Exception:
        # Fall back to DOM content loaded
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
            return True
        except Exception:
            return False


def _wait_for_element(page, selector: str, timeout: int = 5000) -> bool:
    """Wait for a specific element to appear."""
    try:
        page.wait_for_selector(selector, timeout=timeout)
        return True
    except Exception:
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
    pagination_pages: int = 0  # Pages discovered via pagination
    dynamic_content_waits: int = 0  # Infinite scroll / dynamic waits


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
    extract: bool = typer.Option(
        True, "--extract/--no-extract", help="Extract structured content via Readability."
    ),
    include: list[str] = typer.Option(
        None, "--include", help="Only include URLs matching these path prefixes or regexes."
    ),
    exclude: list[str] = typer.Option(
        None, "--exclude", help="Skip URLs matching these path prefixes or regexes."
    ),
    auth_dir: str = typer.Option("./auth", "--auth-dir", help="Directory containing state.json."),
    cache_dir: str = typer.Option("./cache", "--cache-dir", help="Directory to save crawled pages."),
    refresh: bool = typer.Option(False, "--refresh", help="Re-crawl pages already in manifest."),
    retries: int = typer.Option(3, "--retries", help="Max retry attempts per page."),
    rate_limit: float = typer.Option(
        0.5, "--rate-limit", help="Requests per second to throttle to."
    ),
) -> None:
    """Breadth-first crawl of a site using the saved session state.

    Features:
      - Structured JSON logging (Pino-compatible)
      - Exponential-backoff retry with jitter
      - Rate limiting (requests/sec throttle)
      - Health check before crawling
      - Graceful shutdown (SIGINT/SIGTERM)
      - Atomic manifest saves
      - Scrape report on completion
    """

    # ------------------------------------------------------------------
    # 1. Set up structured logging
    # ------------------------------------------------------------------
    log_handler = ScrapeReportHandler()

    log_file = Path(cache_dir) / "scraper.log"
    _logger = get_logger("sitescrape.crawler", level=20, logfile=str(log_file))
    _logger.addHandler(log_handler)

    # ------------------------------------------------------------------
    # 2. Graceful shutdown
    # ------------------------------------------------------------------
    install_signal_handlers()

    # ------------------------------------------------------------------
    # 3. Health check — verify auth state is valid before crawling
    # ------------------------------------------------------------------
    state_path = Path(auth_dir) / "state.json"
    if not state_path.is_file():
        _logger.error(
            "Auth state file not found",
            extra={"ctx": "health_check", "file": str(state_path)},
        )
        typer.echo(f"Error: auth state file not found at {state_path}", err=True)
        raise typer.Exit(1)

    _logger.info(
        "Auth state found — running health check …",
        extra={"ctx": "health_check"},
    )

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(storage_state=str(state_path))
            page = context.new_page()

            try:
                response = page.goto(
                    start_url, wait_until="domcontentloaded", timeout=15000
                )
                status = response.status if response else 0
                body_text = page.inner_text("body")[:500] if response else ""
                is_logged_in = (
                    status not in (401, 403)
                    and not _is_login_page(page)
                )

                if is_logged_in:
                    _logger.info(
                        "Health check passed — session appears valid.",
                        extra={"ctx": "health_check", "status": status},
                    )
                    typer.echo("✅ Auth health check passed.")
                else:
                    _logger.warning(
                        "Health check failed — session may be expired.",
                        extra={"ctx": "health_check", "status": status},
                    )
                    typer.echo(
                        "❌ Auth health check failed — session may be expired.\n"
                        "   Run `sitescrape login <url>` to refresh.\n"
                    )
                    browser.close()
                    raise typer.Exit(1)
            finally:
                browser.close()
    except (typer.Exit, SystemExit):
        raise
    except Exception as exc:
        _logger.warning(
            "Health check error (continuing anyway): %s", exc,
            extra={"ctx": "health_check", "error_type": type(exc).__name__},
        )
        typer.echo(f"⚠️  Health check encountered an error: {exc}")

    # ------------------------------------------------------------------
    # 4. Ensure output dir & setup progress saver
    # ------------------------------------------------------------------
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    manifest_file = cache_path / "manifest.json"
    manifest = Manifest.load(manifest_file)
    progress_saver = ProgressSaver(manifest_file)

    # ------------------------------------------------------------------
    # 5. Rate limiter & retry config
    # ------------------------------------------------------------------
    rate_limiter = RateLimiter(rate=rate_limit)

    # Retryable page fetch (decorated inline below)
    max_retries = retries

    _logger.info(
        "Crawl starting",
        extra={
            "ctx": "crawl_start",
            "start_url": start_url,
            "max_pages": max_pages,
            "retries": max_retries,
            "rate_limit": rate_limit,
        },
    )
    typer.echo(f"\nCrawling {start_url}")
    typer.echo(f"  max-pages={max_pages}, delay={delay}s, retries={max_retries}, "
               f"rate-limit={rate_limit}/s, cache={cache_path}\n")

    # ------------------------------------------------------------------
    # Crawl loop (reuses already-loaded manifest)
    # ------------------------------------------------------------------

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
    results: list[PageData] = []
    _errors: list[dict[str, Any]] = []

    # Seed
    cleaned_start = _strip_tracking(start_url)
    queue.append(cleaned_start)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()

        while queue and stats.saved + stats.failed < max_pages:
            # Check for shutdown request
            if is_shutdown_requested():
                _logger.info(
                    "Shutdown requested — stopping crawl.",
                    extra={"ctx": "shutdown", "saved": stats.saved, "failed": stats.failed},
                )
                typer.echo("\n⚠️  Shutdown requested — saving progress …")
                progress_saver.save_partial(
                    json.dumps(
                        [
                            {"url": e.url, "file": e.file, "title": e.title, "fetch_time": e.fetch_time}
                            for e in manifest.entries.values()
                        ],
                        indent=2,
                    )
                )
                break

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

            page_num = stats.saved + stats.failed + 1
            _logger.info(
                "Crawling page",
                extra={"ctx": "page_start", "page_num": page_num, "total_pages": max_pages,
                       "url": url},
            )
            typer.echo(f"[{page_num}] Crawling: {url}")

            # --- Retry loop for this page ---
            page_response = None
            page_html = ""
            page_title = ""
            page_status = 0
            attempt = 0
            page_failed = True

            while attempt < max_retries:
                attempt += 1
                rate_limiter.acquire()

                try:
                    if is_shutdown_requested():
                        raise KeyboardInterrupt("Shutdown requested")

                    _wait_for_dynamic_content(page)
                    page_response = page.goto(
                        url, wait_until="networkidle", timeout=30000
                    )
                    page_failed = False
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    _logger.warning(
                        "Page fetch attempt %d/%d failed: %s — %s",
                        attempt, max_retries, exc, url,
                        extra={"ctx": "fetch_error", "url": url, "retry": attempt},
                    )
                    if attempt < max_retries:
                        backoff = base_delay * (backoff_factor ** (attempt - 1))
                        jittered = backoff * (0.75 + random.random() * 0.5)
                        _logger.info(
                            "Retrying in %.1fs …", jittered,
                            extra={"ctx": "retry_delay", "url": url, "attempt": attempt},
                        )
                        time.sleep(jittered)
                    else:
                        _errors.append({
                            "url": url,
                            "attempt": attempt,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        })

            if page_failed:
                typer.echo(f"      ❌ Failed after {max_retries} retries: {url}")
                stats.failed += 1
                continue

            # Check for redirect to login page
            current_url = page.url
            if _is_login_page(page):
                _logger.warning(
                    "Redirected to login page — stopping crawl.",
                    extra={"ctx": "login_redirect", "url": url},
                )
                typer.echo(
                    f"      ⚠️  Redirected to login page. Stopping crawl.\n"
                    f"   Run `sitescrape login {start_url}` to refresh your session, then re-run crawl."
                )
                stats.login_detected = True
                break

            # Check for 401/403 (unauthorised)
            status = page_response.status if page_response else 0
            if status in (401, 403):
                _logger.warning(
                    "HTTP %d — unauthorised. Stopping crawl.", status,
                    extra={"ctx": "auth_error", "url": url, "status": status},
                )
                typer.echo(
                    f"      ⚠️  HTTP {status} — unauthorised. Stopping crawl.\n"
                    f"   Run `sitescrape login {start_url}` to refresh your session, then re-run crawl."
                )
                stats.login_detected = True
                break

            # Skip if redirect to login
            if "/login" in current_url.lower() or "/signin" in current_url.lower():
                _logger.warning(
                    "Redirected to login path — stopping crawl.",
                    extra={"ctx": "login_path", "url": current_url},
                )
                typer.echo(
                    f"      ⚠️  Redirected to login page. Stopping crawl.\n"
                    f"   Run `sitescrape login {start_url}` to refresh your session, then re-run crawl."
                )
                stats.login_detected = True
                break

            # Extract title and full HTML
            page_title = page.title() or ""
            try:
                page_html = page.content()
            except Exception:
                page_html = ""

            # --- Content extraction (Readability + BeautifulSoup) ---
            extracted: ExtractedContent = ExtractedContent()
            if extract:
                try:
                    extracted = extract(page_html, url)
                except Exception as exc:
                    _logger.warning(
                        "Content extraction failed: %s", exc,
                        extra={"ctx": "extract_error", "url": url},
                    )
                    typer.echo(f"      ⚠️  Extraction failed: {exc}", err=True)
                    extracted = ExtractedContent()

            # Check for avoid links before enqueuing children
            has_avoid = _has_avoid_links(page)
            if has_avoid:
                _logger.info(
                    "Page contains logout/avoid links.",
                    extra={"ctx": "avoid_links", "url": url},
                )
                typer.echo(f"      ⚠️  Page contains logout/avoid links — not following links on this page.")

            # --- Pagination handling ---
            next_page_url: str | None = None
            if extract:
                next_page_url = _extract_next_page_url(page, url)
                if next_page_url:
                    stats.pagination_pages += 1
                    if next_page_url not in visited:
                        queue.appendleft(next_page_url)
                        typer.echo(f"      🔗 Pagination: next page {next_page_url}")

                # Check for infinite scroll
                if _detect_infinite_scroll(page):
                    try:
                        page.mouse.wheel(0, 2000)
                        page.wait_for_timeout(2000)
                        stats.dynamic_content_waits += 1
                        _logger.info(
                            "Infinite scroll scrolled once.",
                            extra={"ctx": "infinite_scroll", "url": url},
                        )
                        typer.echo(f"      🔄 Infinite scroll: scrolled once", err=True)
                        page_html = page.content()
                        extracted = extract(page_html, url)
                    except Exception:
                        pass

            # --- Save page ---
            try:
                out_file.write_text(page_html, encoding="utf-8")
                stats.saved += 1
                manifest.entries[url] = ManifestEntry(
                    url=url,
                    file=str(out_file),
                    title=page_title,
                    fetch_time=time.strftime("%Y-%m-%dT%H:%M:%S"),
                )

                # Save structured JSON alongside HTML
                if extract:
                    json_file = Path(str(out_file).replace(".html", ".json"))
                    json_file.write_text(
                        content_to_json(extracted), encoding="utf-8"
                    )

                    page_data = PageData(
                        url=url,
                        title=page_title,
                        extracted=extracted,
                        raw_html_path=str(out_file),
                        metadata={
                            "status_code": status,
                            "fetch_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "html_size": len(page_html),
                            "json_size": json_file.stat().st_size,
                        },
                    )
                    results.append(page_data)

                    _logger.info(
                        "Page saved successfully",
                        extra={"ctx": "page_saved", "url": url, "html_size": len(page_html),
                               "json_size": json_file.stat().st_size},
                    )
                    typer.echo(
                        f"      ✅ Saved ({len(page_html)} bytes HTML, "
                        f"{json_file.stat().st_size} bytes JSON)  "
                        f"title: {page_title}"
                    )
                else:
                    typer.echo(f"      ✅ Saved ({len(page_html)} bytes HTML)  "
                               f"title: {page_title}")
            except Exception as exc:
                _logger.error(
                    "Failed to save page: %s", exc,
                    extra={"ctx": "save_error", "url": url},
                )
                _errors.append({
                    "url": url,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                })
                stats.failed += 1
                continue

            # Enqueue child links
            if not has_avoid:
                children = _extract_links(page, url)
                for child in children:
                    if child not in visited:
                        queue.append(child)

            # Delay between requests
            time.sleep(delay)

        browser.close()

    # ------------------------------------------------------------------
    # Post-crawl: save manifest & write scrape report
    # ------------------------------------------------------------------

    # Final manifest save
    progress_saver.save_partial(
        json.dumps(
            [
                {"url": e.url, "file": e.file, "title": e.title, "fetch_time": e.fetch_time}
                for e in manifest.entries.values()
            ],
            indent=2,
        )
    )
    _logger.info(
        "Manifest saved",
        extra={"ctx": "manifest_save", "entries": len(manifest.entries)},
    )

    scrape_stats = {
        "start_url": cleaned_start,
        "saved": stats.saved,
        "skipped": stats.skipped,
        "failed": stats.failed,
        "login_detected": stats.login_detected,
        "pagination_pages": stats.pagination_pages,
        "dynamic_content_waits": stats.dynamic_content_waits,
        "total_requests": stats.saved + stats.failed,
    }

    # Save full crawl result as JSON (only if extraction was enabled)
    report_path: Path | None = None

    if extract and results:
        result_file = cache_path / "crawl_result.json"
        result_data = {
            "start_url": cleaned_start,
            "pages": [
                {
                    "url": r.url,
                    "title": r.title,
                    "extracted": {
                        "title": r.extracted.title,
                        "summary": r.extracted.summary,
                        "text": r.extracted.text,
                        "headings": [
                            {"level": h.level, "text": h.text}
                            for h in r.extracted.headings
                        ],
                        "code_blocks": [
                            {"source": cb.source, "language": cb.language}
                            for cb in r.extracted.code_blocks
                        ],
                        "tables": [
                            {
                                "caption": t.caption,
                                "headers": t.headers,
                                "rows": [{"cells": c.cells} for c in t.rows],
                            }
                            for t in r.extracted.tables
                        ],
                        "links": r.extracted.links,
                        "images": r.extracted.images,
                        "pagination": getattr(r.extracted, "pagination", []),
                    },
                    "raw_html_path": r.raw_html_path,
                    "metadata": r.metadata,
                }
                for r in results
            ],
            "stats": scrape_stats,
        }
        result_file.write_text(
            json.dumps(result_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        report_path = result_file

    # Write structured scrape report (includes log summary + errors)
    scrape_report_path = cache_path / "scrape_report.json"
    log_handler.write_report(
        scrape_report_path,
        stats=scrape_stats,
        errors=_errors,
    )
    _logger.info(
        "Scrape report written",
        extra={"ctx": "report", "path": str(scrape_report_path)},
    )

    # Summary
    _logger.info(
        "Crawl complete",
        extra={"ctx": "summary", **scrape_stats},
    )

    print(f"\n{'='*50}")
    print(f"Crawl complete!")
    print(f"  Pages saved:        {stats.saved}")
    print(f"  Pages skipped:      {stats.skipped}")
    print(f"  Pages failed:       {stats.failed}")
    if extract:
        print(f"  Pagination found:   {stats.pagination_pages}")
        print(f"  Dynamic content:    {stats.dynamic_content_waits}")
    if stats.login_detected:
        print(f"\n  ⚠️  Login page detected — crawl stopped early."
              f"\n  Run `sitescrape login {start_url}` to refresh your session.")
    if _errors:
        print(f"\n  Errors logged:      {len(_errors)}")
    print(f"{'='*50}")
    if report_path:
        print(f"\nCrawl result: {report_path}")
    print(f"Scrape report: {scrape_report_path}")
    print(f"Log file:       {log_file}")
    print()
