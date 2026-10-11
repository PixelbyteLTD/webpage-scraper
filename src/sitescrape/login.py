"""Login command: open a headed browser, wait for user, save session."""

import os
import typer
from playwright.sync_api import sync_playwright


def do_login(
    url: str = typer.Argument(..., help="URL to open for manual login"),
    auth_dir: str = typer.Option("./auth", "--auth-dir", help="Directory to save auth state."),
) -> None:
    """Launch a visible Chromium browser at URL, wait for the human to log in,
    then press Enter to capture the session and write auth/state.json.

    The human MUST log in themselves — no credentials are stored or prompted.
    """
    output_path = f"{auth_dir}/state.json"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headed=True)
        context = browser.new_context()
        page = context.new_page()

        print(f"\nOpening {url} in a headed browser...")
        print("Please log in manually. Press Enter in the terminal when done.\n")
        input()  # block until human presses Enter

        context.storage_state(path=output_path)
        print(f"Session state saved to {output_path}")

        browser.close()
