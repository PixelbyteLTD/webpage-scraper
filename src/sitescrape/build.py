"""Build command: generate static HTML site with mkdocs build."""

import subprocess
import sys
from pathlib import Path

import typer


def do_build(
    docs_dir: str = typer.Option(
        "./output/docs", "--docs-dir", help="Directory containing converted Markdown files."
    ),
    config_file: str = typer.Option(
        "./output/mkdocs.yml", "--config", help="Path to mkdocs.yml."
    ),
    strict: bool = typer.Option(
        False, "--strict", help="Enable strict mode (treat warnings as errors)."
    ),
) -> None:
    """Build a static HTML site from the converted Markdown output."""
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

    print(f"Building site from {docs_path}")
    print(f"  config: {config_path}")
    print(f"  output: output/site/\n")

    cmd = [sys.executable, "-m", "mkdocs", "build", "--clean"]
    if strict:
        cmd.append("--strict")

    result = subprocess.run(
        cmd,
        cwd=str(config_path.parent),
        env={**subprocess.os.environ, "MKDOCS_CONFIG": str(config_path)},
    )

    if result.returncode != 0:
        typer.echo(f"Error: mkdocs build failed (exit code {result.returncode})", err=True)
        raise typer.Exit(1)

    print(f"\nBuild complete! Static site written to output/site/")
