"""Check command: verify a saved session still looks logged in."""

import re

import typer
from playwright.sync_api import sync_playwright

# Common login-page selectors
_LOGIN_SELECTORS = [
    'input[type="password"]',
    'input[name="password"]',
    'input[name="passwd"]',
    'input[name="pass"]',
    '#password',
    '#login-password',
    '.password',
]

# Common login-page page-title / body text patterns
_LOGIN_TEXT_PATTERNS = [
    r"[Ll]og\s*[Ii]n",
    r"[Ss]ign\s*[Ii]n",
    r"[Ll]og\s*[Oo]ut",
    r"[Ll]og\s*[_-]?[Ii][Nn]",
    r"authenticate",
    r"sign[_-]?[_\s]*up",
]


def do_check(
    url: str = typer.Argument(..., help="URL to check whether session is logged in"),
    auth_dir: str = typer.Option("./auth", "--auth-dir", help="Directory containing state.json."),
) -> None:
    """Launch headless Chromium with the saved session state and check whether
    the page at URL appears to be logged in.

    Heuristics:
    - No redirect to a login page
    - No password input field on the page
    """
    state_path = f"{auth_dir}/state.json"
    if not state_path.endswith("/state.json"):
        state_path = f"{state_path}/state.json"

    import os

    if not os.path.isfile(state_path):
        typer.echo(f"Error: auth state file not found at {state_path}", err=True)
        raise typer.Exit(1)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(storage_state=state_path)
        page = context.new_page()

        print(f"\nLoading {url} headless with saved session...\n")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            print(f"Page load warning: {exc}")

        # Check for password field
        password_found = any(
            page.locator(sel).count() > 0 for sel in _LOGIN_SELECTORS
        )

        # Check for login/sign-in text in title or body
        body_text = page.inner_text("body")
        page_title = page.title() or ""
        preview = f"{page_title} {body_text[:500]}" if body_text else page_title

        text_matches = [
            pattern for pattern in _LOGIN_TEXT_PATTERNS
            if re.search(pattern, preview)
        ]

        is_logged_in = not password_found and len(text_matches) == 0

        if is_logged_in:
            print("✅ Looks logged in.")
            print(f"   Title: {page_title}")
            print(f"   URL: {page.url}")
        else:
            print("❌ Looks logged out or on a login page.")
            if password_found:
                print("   → Password input field found on the page.")
            if text_matches:
                print(f"   → Login/sign-in text patterns matched: {text_matches[:3]}")
            print(f"   Title: {page_title}")
            print(f"   URL: {page.url}")

        browser.close()
