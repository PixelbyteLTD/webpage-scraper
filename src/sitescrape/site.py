"""Site command: generate MkDocs config from crawled/converted output."""

import os
from pathlib import Path

import typer


def _scan_docs_dir(output_dir: Path) -> list[dict]:
    """Walk output_dir and return a list of page dicts sorted by path."""
    pages: list[dict] = []
    if not output_dir.is_dir():
        return pages

    for root, dirs, files in os.walk(output_dir):
        dirs.sort()
        for f in sorted(files):
            if f == "index.md":
                continue  # index is handled separately
            full = Path(root) / f
            rel = full.relative_to(output_dir)
            pages.append({"path": str(rel), "title": f})

    # Also add index.md at each level
    for root, dirs, files in os.walk(output_dir):
        dirs.sort()
        index_file = Path(root) / "index.md"
        if index_file.is_file():
            rel = index_file.relative_to(output_dir)
            if rel not in [p["path"] for p in pages]:
                pages.append({"path": str(rel), "title": "Index"})

    pages.sort(key=lambda p: p["path"])
    return pages


def do_site(
    docs_dir: str = typer.Option(
        "./output/docs", "--docs-dir", help="Directory containing converted Markdown files."
    ),
    output_file: str = typer.Option(
        "./output/mkdocs.yml", "--output", help="Path to write mkdocs.yml."
    ),
) -> None:
    """Generate an MkDocs configuration from the converted Markdown output."""
    docs_path = Path(docs_dir)
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pages = _scan_docs_dir(docs_path)

    # Build nav structure from directory hierarchy
    nav = _build_nav(pages)

    config = {
        "site_name": "Sitescrape Documentation",
        "site_description": "Documentation scraped from a website",
        "theme": {
            "name": "material",
            "features": ["search"],
            "palette": {
                "scheme": "default",
                "primary": "indigo",
                "accent": "indigo",
            },
            "icon": {"repo": "fontawesome/brands/github"},
        },
        "plugins": [
            {"search": {}},
        ],
        "nav": nav,
    }

    # If we have docs dir content, set it as the docs_dir in config
    config["docs_dir"] = str(docs_path)

    import yaml

    config_yaml = yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True)
    output_path.write_text(config_yaml, encoding="utf-8")

    print(f"Generated mkdocs.yml at {output_path}")
    print(f"  Pages indexed: {len(pages)}")


def _build_nav(pages: list[dict]) -> list[dict]:
    """Build a nested nav structure from a flat list of page paths.

    Example:
        ["guides/setup.md", "guides/faq.md", "index.md"]
        -> [
            {"index": "index.md"},
            {
                "guides": [
                    {"setup": "guides/setup.md"},
                    {"faq": "guides/faq.md"},
                ]
            },
           ]
    """
    if not pages:
        return []

    # Build a tree
    tree: dict = {}
    for page in pages:
        parts = page["path"].replace("/", ".").split(".")
        current = tree
        for i, part in enumerate(parts[:-1]):
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = page["path"]

    # Convert tree to MkDocs nav format
    return _tree_to_nav(tree)


def _tree_to_nav(tree: dict) -> list[dict]:
    """Convert nested dict to MkDocs nav list format."""
    result = []
    for key, value in tree.items():
        if isinstance(value, str):
            # Leaf node: key is the page title, value is the path
            result.append({key: value})
        else:
            # Section: key is the section name, value is nested dict
            children = _tree_to_nav(value)
            if children:
                result.append({key: children})
            else:
                result.append({key: ""})
    return result
