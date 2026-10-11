"""Import-cookies command: convert a Netscape cookies.txt to Playwright session state."""

import json
import os
from pathlib import Path

import typer


def _parse_netscape_cookie_line(line: str) -> dict | None:
    """Parse a single Netscape format line.

    Format (tab-separated):
        domain  flag  path  secure  expire  name  value

    flag: TRUE or FALSE (whether all cookies from this domain are secure)
    secure: TRUE or FALSE
    """
    parts = line.split("\t")
    if len(parts) < 7:
        return None

    try:
        return {
            "name": parts[5],
            "value": parts[6],
            "domain": parts[0],
            "path": parts[2],
            "secure": parts[3].upper() == "TRUE",
            "httpOnly": parts[4].upper() == "TRUE",  # noqa: N806
            "sameSite": "None",
        }
    except (IndexError, ValueError):
        return None


def do_import_cookies(
    file: str = typer.Argument(..., help="Path to Netscape-format cookies.txt file"),
    auth_dir: str = typer.Option("./auth", "--auth-dir", help="Directory to write auth state."),
) -> None:
    """Read a Netscape-format cookies.txt file (exported from a browser extension)
    and convert it to Playwright's session state JSON at auth_dir/state.json.
    """
    input_path = Path(file)
    if not input_path.is_file():
        typer.echo(f"Error: file not found: {file}", err=True)
        raise typer.Exit(1)

    cookies: list[dict] = []
    with input_path.open("r") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parsed = _parse_netscape_cookie_line(stripped)
            if parsed:
                cookies.append(parsed)

    os.makedirs(auth_dir, exist_ok=True)

    state = {
        "cookies": cookies,
        "expires": [],
        "domain": [],
        "path": "/",
    }

    output_path = Path(auth_dir) / "state.json"
    output_path.write_text(json.dumps(state, indent=2))
    typer.echo(f"Imported {len(cookies)} cookies → {output_path}")
