"""CLI entry-point using Typer."""

import typer

from sitescrape.archive import do_archive
from sitescrape.build import do_build
from sitescrape.check import do_check
from sitescrape.cookies import do_import_cookies
from sitescrape.converter import do_convert
from sitescrape.crawler import do_crawl
from sitescrape.login import do_login
from sitescrape.service import do_service
from sitescrape.site import do_site

app = typer.Typer(
    name="sitescrape",
    help="Capture a logged-in browser session for sites behind a login.",
    add_completion=False,
)

# ── one-shot pipeline ──────────────────────────────────────────────────


def do_crawl_convert_site(
    start_url: str = typer.Argument(..., help="URL to start crawling from."),
    max_pages: int = typer.Option(500, "--max-pages", help="Maximum pages to crawl."),
    delay: float = typer.Option(1.5, "--delay", help="Seconds between requests."),
    include: list[str] = typer.Option(None, "--include", help="Only include URLs matching these path prefixes or regexes."),
    exclude: list[str] = typer.Option(None, "--exclude", help="Skip URLs matching these path prefixes or regexes."),
    auth_dir: str = typer.Option("./auth", "--auth-dir", help="Directory containing state.json."),
    cache_dir: str = typer.Option("./cache", "--cache-dir", help="Directory to save crawled pages."),
    docs_dir: str = typer.Option("./output/docs", "--docs-dir", help="Directory for converted Markdown files."),
    strict: bool = typer.Option(False, "--strict", help="Enable strict mode for build."),
) -> None:
    """Run crawl → convert → site in one command.

    This is a convenience wrapper that chains all three steps.
    """
    print("=" * 60)
    print("Step 1/3: Crawling")
    print("=" * 60)
    do_crawl(
        start_url=start_url,
        max_pages=max_pages,
        delay=delay,
        include=include,
        exclude=exclude,
        auth_dir=auth_dir,
        cache_dir=cache_dir,
    )

    print()
    print("=" * 60)
    print("Step 2/3: Converting")
    print("=" * 60)
    do_convert(cache_dir=cache_dir, output_dir=docs_dir)

    print()
    print("=" * 60)
    print("Step 3/3: Generating site config")
    print("=" * 60)
    do_site(docs_dir=docs_dir)

    print()
    print("=" * 60)
    print("Done! All three steps completed.")
    print(f"  • Crawled pages are in {cache_dir}/")
    print(f"  • Markdown files are in {docs_dir}/")
    print(f"  • MkDocs config is at output/mkdocs.yml")
    print()
    print("To preview locally:  sitescrape serve")
    print("To build static HTML: sitescrape build")
    print()


app.command(name="login")(do_login)
app.command(name="import-cookies")(do_import_cookies)
app.command(name="check")(do_check)
app.command(name="crawl")(do_crawl)
app.command(name="convert")(do_convert)
app.command(name="site")(do_site)
app.command(name="serve")(do_service)
app.command(name="build")(do_build)
app.command(name="all")(do_crawl_convert_site)
app.command(name="package")(do_archive)
