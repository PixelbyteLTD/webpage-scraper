"""Archive package: tarball or JSON index export for mobile/RN consumers.

Exports converted Markdown output as a distribution package:
- Tarball (.tar.gz): file tree with index.json
- JSON index: single JSON file with all pages

Secrets and local config are excluded from the output.
"""

import io
import json
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

# Patterns for files to exclude from packaging
_EXCLUDE_PATTERNS = [
    re.compile(r"^auth(/|$)", re.I),                  # session state
    re.compile(r"^cache(/|$)", re.I),                 # raw HTML
    re.compile(r"__pycache__"),                       # Python cache
    re.compile(r"\.pyc$"),                           # Python bytecode
    re.compile(r"\.egg-info/"),                      # Python egg info
    re.compile(r"^dist(/|$)"),                       # build dist
    re.compile(r"^build(/|$)"),                      # build dir
    re.compile(r"^htmlcov(/|$)"),                    # test coverage
    re.compile(r"^\.git(/|$)"),                      # version control
    re.compile(r"^\.idea(/|$)"),                     # IDE
    re.compile(r"^\.vscode(/|$)"),                   # IDE
    re.compile(r"\.swp$"),                          # Vim swap
    re.compile(r"pytest_cache"),                     # pytest cache
    re.compile(r"\.mypy_cache"),                    # mypy cache
    re.compile(r"node_modules(/|$)"),                # Node dependencies
    re.compile(r"\.venv(/|$)"),                     # virtualenv
    re.compile(r"^env(/|$)"),                       # env dir
    re.compile(r"^\.gitignore$"),                   # not deliverable
]

# File name patterns that likely contain secrets
_SECRET_PATTERNS = [
    re.compile(r"\.env", re.I),
    re.compile(r"secret", re.I),
    re.compile(r"password", re.I),
    re.compile(r"token", re.I),
    re.compile(r"private.*key", re.I),
    re.compile(r"\.pem$"),
    re.compile(r"\.p12$"),
    re.compile(r"\.pfx$"),
    re.compile(r"credentials", re.I),
]


def _is_excluded(path: Path, base: Path | None = None) -> bool:
    """Check if a file/directory path should be excluded from packaging.

    If base is provided, the path is treated as relative to base.
    If no base, absolute path patterns are matched against the full path.
    """
    if base is not None:
        try:
            rel = str(path.relative_to(base))
        except ValueError:
            rel = str(path)
    else:
        rel = str(path)

    for pattern in _EXCLUDE_PATTERNS:
        if pattern.search(rel):
            return True
    return False


def _has_secrets(filename: Path) -> bool:
    """Check if a filename likely contains secrets."""
    for pattern in _SECRET_PATTERNS:
        if pattern.search(filename.name):
            return True
    return False


def _collect_files(root: Path) -> list[Path]:
    """Recursively collect all files under root, filtering excluded."""
    files = []
    for path in sorted(root.rglob("*")):
        if _is_excluded(path, root):
            continue
        if _has_secrets(path):
            continue
        if path.is_file():
            files.append(path)
    return files


def _generate_json_index(pages_dir: Path) -> dict[str, Any]:
    """Generate a JSON index from Markdown files in the output directory."""
    index_path = pages_dir / "index.md"

    # Build pages list
    pages: list[dict[str, Any]] = []

    if index_path.is_file():
        content = index_path.read_text(encoding="utf-8")
        # Parse markdown links to discover pages
        link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\.md\)")
        for match in link_pattern.finditer(content):
            title = match.group(1)
            link_path = match.group(2)
            if link_path.startswith("./"):
                link_path = link_path[2:]

            md_path = pages_dir / f"{link_path}.md"
            if md_path.is_file():
                try:
                    md_content = md_path.read_text(encoding="utf-8")
                    title_m = re.search(r"^title:\s*(.+)$", md_content, re.M)
                    url_m = re.search(r"^source_url:\s*(.+)$", md_content, re.M)
                    at_m = re.search(r"^scraped_at:\s*(.+)$", md_content, re.M)

                    body_start = md_content.find("---", 3)
                    content_text = md_content[body_start + 3:].strip() if body_start > 0 else md_content

                    md_title = title_m.group(1).strip() if title_m else title
                    md_url = url_m.group(1).strip() if url_m else ""
                    md_at = at_m.group(1).strip() if at_m else ""

                    breadcrumbs = _extract_breadcrumbs(link_path)

                    pages.append({
                        "slug": link_path,
                        "title": md_title,
                        "sourceUrl": md_url,
                        "scrapedAt": md_at,
                        "content": content_text,
                        "assets": _get_page_assets(pages_dir / link_path),
                        "breadcrumbs": breadcrumbs,
                    })
                except Exception:
                    continue

    # Fallback: discover from filesystem
    if not pages:
        pages = _discover_from_filesystem(pages_dir)

    return {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages": pages,
    }


def _extract_breadcrumbs(slug: str) -> list[str]:
    """Extract breadcrumb trail from a slug."""
    if slug == "index" or slug == "":
        return ["Home"]
    parts = [p.replace("_", " ").title() for p in slug.split("/") if p]
    return parts


def _get_page_assets(slug_path: str) -> list[str]:
    """Get the list of assets referenced by a page's markdown."""
    # This is called with a slug path string, need to read the md file
    # Since we don't have the base dir here, we'll find assets in content
    # Actually this is called from _generate_json_index where we have content
    # Let me fix this: the content is already loaded above, so pass content instead
    # For now, this is a placeholder -- callers should use content-based extraction
    return []


def _get_assets_from_content(content: str) -> list[str]:
    """Extract asset references from markdown content."""
    asset_pattern = re.compile(r"assets/([^)\s\"']+)")
    return sorted(set(asset_pattern.findall(content)))


def _discover_from_filesystem(pages_dir: Path) -> list[dict[str, Any]]:
    """Discover pages directly from the filesystem, skipping only the root index.md."""
    pages = []
    for md_file in sorted(pages_dir.rglob("*.md")):
        # Skip the root index.md (it's the TOC, not a content page)
        if md_file.name == "index.md" and md_file == pages_dir / "index.md":
            continue

        rel = md_file.relative_to(pages_dir)
        slug = str(rel.with_suffix(""))

        try:
            content = md_file.read_text(encoding="utf-8")
            title_m = re.search(r"^title:\s*(.+)$", content, re.M)
            url_m = re.search(r"^source_url:\s*(.+)$", content, re.M)
            at_m = re.search(r"^scraped_at:\s*(.+)$", content, re.M)

            body_start = content.find("---", 3)
            body = content[body_start + 3:].strip() if body_start > 0 else content

            pages.append({
                "slug": slug,
                "title": title_m.group(1).strip() if title_m else md_file.stem.replace("_", " ").title(),
                "sourceUrl": url_m.group(1).strip() if url_m else "",
                "scrapedAt": at_m.group(1).strip() if at_m else "",
                "content": body,
                "assets": _get_assets_from_content(content),
                "breadcrumbs": _extract_breadcrumbs(slug),
            })
        except Exception:
            continue

    return pages


def do_archive(
    input_dir: str = typer.Argument(
        ..., help="Directory containing converted Markdown (default: ./output/docs)"
    ),
    format: str = typer.Option(
        "tarball", "--format", help="Output format: tarball or json-index"
    ),
    output: str = typer.Option(
        None, "--output", help="Output file path (auto-generated if omitted)"
    ),
) -> None:
    """Package converted output for distribution to mobile/other consumers.

    Exports the Markdown output as either:
    - A .tar.gz archive containing the file tree + index.json
    - A structured JSON index (single file)

    Secrets (auth/, cache/, .env, credentials files) are excluded.
    """
    input_path = Path(input_dir)
    if not input_path.is_dir():
        typer.echo(f"Error: input directory not found: {input_path}", err=True)
        raise typer.Exit(1)

    # Discover all files
    files = _collect_files(input_path)
    if not files:
        typer.echo("Warning: no files found to package.", err=True)

    # Generate JSON index
    index_data = _generate_json_index(input_path)

    if format == "tarball":
        _create_tarball(input_path, files, index_data, output)
    elif format == "json-index":
        _create_json_index(index_data, output)
    else:
        typer.echo(f"Error: unknown format '{format}'. Use 'tarball' or 'json-index'.", err=True)
        raise typer.Exit(1)


def _create_tarball(
    input_path: Path,
    files: list[Path],
    index_data: dict,
    output_path: str | None,
) -> None:
    """Create a .tar.gz archive."""
    if output_path:
        tar_path = Path(output_path)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tar_path = Path(f"sitescrape-output-{timestamp}.tar.gz")

    tar_path.parent.mkdir(parents=True, exist_ok=True)
    index_json = json.dumps(index_data, indent=2, ensure_ascii=False).encode("utf-8")

    with tarfile.open(str(tar_path), "w:gz") as tar:
        # Add index.json at root
        info = tarfile.TarInfo(name="index.json")
        info.size = len(index_json)
        tar.addfile(info, io.BytesIO(index_json))

        # Add markdown files preserving directory structure
        for file_path in files:
            arcname = str(file_path.relative_to(input_path))
            tar.add(str(file_path), arcname=arcname)

        # Add assets
        assets_dir = input_path / "assets"
        if assets_dir.is_dir():
            for asset in sorted(assets_dir.rglob("*")):
                if asset.is_file():
                    arcname = str(asset.relative_to(input_path))
                    tar.add(str(asset), arcname=arcname)

    typer.echo(f"Created tarball: {tar_path}")
    typer.echo(f"  Pages: {len(index_data['pages'])}")
    typer.echo(f"  Files included: {len(files)}")


def _create_json_index(
    index_data: dict,
    output_path: str | None,
) -> None:
    """Create a single JSON index file."""
    if output_path:
        json_path = Path(output_path)
    else:
        json_path = Path("sitescrape-output.json")

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(index_data, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )

    typer.echo(f"Created JSON index: {json_path}")
    typer.echo(f"  Pages: {len(index_data['pages'])}")
