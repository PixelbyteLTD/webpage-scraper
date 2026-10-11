"""CLI entry-point using Typer."""

import typer

from sitescrape.check import do_check
from sitescrape.cookies import do_import_cookies
from sitescrape.converter import do_convert
from sitescrape.crawler import do_crawl
from sitescrape.login import do_login

app = typer.Typer(
    name="sitescrape",
    help="Capture a logged-in browser session for sites behind a login.",
    add_completion=False,
)

app.command(name="login")(do_login)
app.command(name="import-cookies")(do_import_cookies)
app.command(name="check")(do_check)
app.command(name="crawl")(do_crawl)
app.command(name="convert")(do_convert)
