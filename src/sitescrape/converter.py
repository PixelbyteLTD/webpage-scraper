"""Convert cached pages into organised Markdown files."""

import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import markdownify
import trafilatura
import typer
from bs4 import BeautifulSoup


# ── helpers ──────────────────────────────────────────────────────────


def _slugify_url(url: str) -> str:
    """Turn a URL into a safe directory/filename component."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    path = path.strip("/")
    if not path:
        return "index"
    safe = re.sub(r"[^\w\-.]", "_", path)
    return safe


def _compute_image_key(url: str) -> str:
    """Deterministic safe name for a cached image."""
    h = hashlib.md5(url.encode()).hexdigest()[:12]
    ext = _image_ext(url) or ".png"
    return f"{h}{ext}"


_IMAGE_EXTENSIONS = {
    ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png",
    ".gif": "gif", ".webp": "webp", ".svg": "svg",
    ".avif": "avif",
}


def _image_ext(url: str) -> str:
    parsed = urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    return ext if ext in _IMAGE_EXTENSIONS else ".png"


# ── image downloader ────────────────────────────────────────────────


def _download_images(html: str, assets_dir: Path) -> str:
    """Replace <img> src attributes with local paths and download the files."""
    soup = BeautifulSoup(html, "lxml")

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue

        # Skip data URIs and non-HTTP URLs
        if src.startswith(("data:", "about:", "javascript:")):
            continue
        if not src.startswith(("http://", "https://")):
            continue

        key = _compute_image_key(src)
        local_path = assets_dir / key

        if not local_path.exists():
            try:
                import urllib.request

                req = urllib.request.Request(
                    src,
                    headers={"User-Agent": "Mozilla/5.0 sitescrape/0.1"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    local_path.write_bytes(resp.read())
            except Exception:
                continue

        img["src"] = str(local_path)

    return str(soup)


# ── link rewriter ───────────────────────────────────────────────────


def _build_url_to_md_map(entries: dict) -> dict[str, tuple[str, str]]:
    """Map URL -> (directory, filename) for link rewriting."""
    mapping = {}
    for url in entries:
        directory, filename = _url_to_relative_path(url)
        mapping[url] = (directory, filename)
    return mapping


def _relative_md_path(from_directory: str, to_url: str, url_map: dict) -> str:
    """Compute a relative .md path from one file's directory to another."""
    to_dir, to_file = url_map.get(to_url, (None, None))
    if to_dir is None or to_file is None:
        return to_url  # external or missing

    # Remove .md from to_file for the path component
    to_name = to_file[:-3] if to_file.endswith(".md") else to_file

    if not from_directory:
        return f"{to_dir}/{to_name}" if to_dir else to_name

    from_parts = from_directory.split("/")
    to_parts = ([p for p in to_dir.split("/") if p] if to_dir else [])

    # Walk up from from_directory until common ancestor
    up_count = 0
    for i in range(min(len(from_parts), len(to_parts))):
        if from_parts[i] == to_parts[i]:
            up_count = i + 1
        else:
            break

    up = "../" * (len(from_parts) - up_count)
    down = "/".join(to_parts[up_count:])
    rel = up + (down + "/" if down else "") + to_name
    return rel


def _rewrite_links(markdown: str, url_to_md: dict[str, str],
                   file_directory: str, url_map: dict) -> str:
    """Convert absolute/internal links to relative .md references."""
    def _replace_link(match):
        url = match.group(2).strip()  # group(2) is the URL inside ()
        alt_text = match.group(1)      # group(1) is the text inside []

        if url in url_to_md:
            target = _relative_md_path(file_directory, url, url_map)
            return f"[{alt_text}]({target}.md)"
        return match.group(0)  # keep as-is for external links

    # Match [text](url) patterns
    pattern = r"\[([^\]]+)\]\(([^)]+)\)"
    result = re.sub(pattern, _replace_link, markdown)
    return result


# ── content extraction ──────────────────────────────────────────────


def _extract_title(html: str) -> str:
    """Extract page title from HTML."""
    soup = BeautifulSoup(html, "lxml")
    # Try <title>
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        return title_tag.string.strip()
    # Try <h1>
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    return "Untitled"


def _extract_main_content(html: str) -> str:
    """Extract main content from HTML, stripping nav/headers/footers."""
    soup = BeautifulSoup(html, "lxml")

    # Remove navigation elements
    for tag in soup.find_all(["nav", "header", "footer", "aside",
                               "script", "style", "noscript"]):
        tag.decompose()

    # Also remove common non-content containers
    for tag in soup.find_all(class_=re.compile(
            r"cookie[-_]?banner|modal|sidebar|skip[-_]?link|advertisement|ads",
            re.I)):
        tag.decompose()

    # Prefer <main>, <article>, or the first substantial div/section
    main_tag = (soup.find("main") or
                soup.find("article") or
                soup.find("div", class_=re.compile(r"content|main", re.I)) or
                soup.find("section", class_=re.compile(r"content|main", re.I)))

    if main_tag:
        content = markdownify.markdownify(
            str(main_tag),
            heading_style="ATX",
            strip=["script", "style", "img"],
            wrap=True,
            wrap_width=200,
        ).strip()
    else:
        # Fallback: trafilatura on the stripped HTML
        cleaned = str(soup)
        content = trafilatura.extract(
            cleaned, include_comments=False, include_tables=True
        )
        if content:
            content = content.strip()
        else:
            content = ""

    return content


# ── directory mapping ───────────────────────────────────────────────


def _url_to_relative_path(url: str) -> tuple[str, str]:
    """Convert URL to (directory, filename) relative to output root.

    e.g. /guides/setup -> ("guides", "setup.md")
         /guides/     -> ("guides", "index.md")
         /            -> ("", "index.md")
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    parts = [p for p in path.split("/") if p]

    if not parts:
        return ("", "index.md")

    filename = parts[-1] + ".md"
    directory = "/".join(parts[:-1]) if len(parts) > 1 else ""
    return (directory, filename)


# ── manifest reader ─────────────────────────────────────────────────


def _load_manifest(cache_dir: Path) -> dict:
    """Load manifest.json and return dict of url -> entry."""
    manifest_path = cache_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest.json not found in {cache_dir}")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {item["url"]: item for item in data}


# ── main convert function ───────────────────────────────────────────


def do_convert(
    cache_dir: str = typer.Option("./cache", "--cache-dir",
                                    help="Directory containing crawled pages and manifest.json."),
    output_dir: str = typer.Option("./output/docs", "--output-dir",
                                    help="Directory to write Markdown files."),
) -> None:
    """Convert cached HTML pages into organised Markdown files."""
    cache_path = Path(cache_dir)
    output_path = Path(output_dir)
    assets_path = output_path / "assets"

    # Load manifest
    try:
        entries = _load_manifest(cache_path)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    if not entries:
        typer.echo("No pages found in manifest. Run `sitescrape crawl` first.")
        raise typer.Exit(1)

    # Ensure output dirs
    output_path.mkdir(parents=True, exist_ok=True)
    assets_path.mkdir(parents=True, exist_ok=True)

    # Build URL -> (dir, filename) mapping
    url_map = _build_url_to_md_map(entries)

    # Track which URLs we processed (for index generation)
    processed = []  # list of (title, directory, url)

    stats = {"converted": 0, "errors": 0}

    for url, entry in entries.items():
        html_file = cache_path / entry["file"]
        if not html_file.is_file():
            print(f"  ⚠️  Skipping {url}: HTML file missing")
            stats["errors"] += 1
            continue

        try:
            html = html_file.read_text(encoding="utf-8")

            # Extract title (from HTML, not manifest which may be empty)
            title = _extract_title(html) or entry.get("title", "Untitled")

            # Download images first (on full HTML)
            html_with_local_imgs = _download_images(html, assets_path)

            # Extract main content
            content = _extract_main_content(html_with_local_imgs)
            if not content:
                print(f"  ⚠️  Skipping {url}: no extractable content")
                stats["errors"] += 1
                continue

            # Compute output path & directory
            directory, filename = _url_to_relative_path(url)
            file_path = output_path / directory / filename

            # Rewrite links to relative .md paths (needs file_directory)
            content = _rewrite_links(content, url_map, directory, url_map)

            # Generate YAML front matter (clean, no extra blank lines)
            scraped_at = entry.get("fetch_time",
                                   datetime.now(timezone.utc).strftime(
                                       "%Y-%m-%dT%H:%M:%SZ"))
            front_matter = (
                f"---\n"
                f"title: {title}\n"
                f"source_url: {url}\n"
                f"scraped_at: {scraped_at}\n"
                f"---\n\n"
            )

            # Create directory and write file
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(front_matter + content + "\n",
                                 encoding="utf-8")

            stats["converted"] += 1
            processed.append((title, directory, url))
            print(f"  ✅ {url} -> {file_path}")

        except Exception as exc:
            print(f"  ❌ {url}: {exc}")
            stats["errors"] += 1

    # Generate index.md — contents page grouped by folder
    _generate_index(output_path, processed, entries, url_map)

    # Summary
    print(f"\n{'='*50}")
    print(f"Conversion complete!")
    print(f"  Pages converted: {stats['converted']}")
    print(f"  Errors:          {stats['errors']}")
    print(f"  Output:          {output_path}")
    print(f"{'='*50}\n")


def _generate_index(output_path: Path, processed: list,
                    entries: dict, url_map: dict) -> None:
    """Generate output/docs/index.md as a contents page grouped by folder."""
    # Group by directory
    by_folder: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for title, directory, url in processed:
        by_folder[directory or "_root"].append((title, directory, url))

    # Sort folders: _root first, then alphabetically
    sorted_folders = sorted(by_folder.keys(),
                            key=lambda k: (k != "_root", k))

    lines = [
        "---",
        "title: Site Contents",
        "source_url: /",
        f"scraped_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "---",
        "",
        "# Site Contents",
        "",
    ]

    for folder in sorted_folders:
        if folder == "_root":
            lines.append("## Root Pages")
            lines.append("")
            folder_entries = by_folder[folder]
        else:
            folder_display = folder.replace("_", " ").title()
            lines.append(f"## {folder_display}")
            lines.append("")
            folder_entries = by_folder[folder]

        for title, directory, url in sorted(folder_entries, key=lambda x: x[0]):
            # Compute relative link from index.md (which is at output_path root)
            target = _relative_md_path("", url, url_map)
            lines.append(f"- [{title}]({target}.md)")
            lines.append("")

    index_path = output_path / "index.md"
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  📋 Generated index.md")
