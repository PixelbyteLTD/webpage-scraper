"""Serve command: run mkdocs serve for local preview."""

import subprocess
import sys
from pathlib import Path

import typer


def do_serve(
    docs_dir: str = typer.Option(
        "./output/docs", "--docs-dir", help="Directory containing converted Markdown files."
    ),
    config_file: str = typer.Option(
        "./output/mkdocs.yml", "--config", help="Path to mkdocs.yml."
    ),
    port: int = typer.Option(
        8090, "--port", "-p", help="Port to serve on."
    ),
) -> None:
    """Start a local MkDocs dev server to preview the site."""
    config_path = Path(config_file)
    if not config_path.is_file():
        typer.echo(
            f"Error: config file not found at {config_path}\n"
            f"  Run `sitescrape site` first to generate it.",
            err=True,
        )
        raise typer.Exit(1)

    docs_path = Path(docs_dir)
    if not docs_path.is_dir():
        typer.echo(
            f"Error: docs directory not found at {docs_path}\n"
            f"  Run `sitescrape convert` first to generate Markdown files.",
            err=True,
        )
        raise typer.Exit(1)

    print(f"Serving docs from {docs_path} on http://0.0.0.0:{port}")
    print("Press Ctrl+C to stop.\n")

    subprocess.run(
        [sys.executable, "-m", "mkdocs", "serve", "--dev-addr", f"0.0.0.0:{port}"],
        cwd=str(config_path.parent),
        env={**subprocess.os.environ, "MKDOCS_CONFIG": str(config_path)},
        check=True,
    )
