# sitescrape

Capture a logged-in browser session and crawl a site behind authentication, converting every page into clean Markdown — ready for consumption by downstream tools (e.g. a React Native docs app).

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# 1. Log in (opens a headed browser)
sitescrape login https://docs.example.com

# 2. Verify the session still works
sitescrape check https://docs.example.com

# 3. Crawl the site
sitescrape crawl https://docs.example.com

# 4. Convert HTML cache → organised Markdown
sitescrape convert

# 5. Generate MkDocs config (for self-hosting)
sitescrape site

# 6. Preview locally or build static HTML
sitescrape serve --port 8090    # dev server
sitescrape build                # static output/

# Or do it all in one shot:
sitescrape all https://docs.example.com
```

**Headless / CI?** Skip `login` and use `sitescrape import-cookies cookies.txt` instead.

See the sections below for full configuration, troubleshooting, and data-contract details.

---

## Table of Contents

- [Architecture](#architecture)
- [Commands](#commands)
  - [login](#login)
  - [import-cookies](#import-cookies)
  - [check](#check)
  - [crawl](#crawl)
  - [convert](#convert)
  - [package](#package)
  - [site](#site)
  - [serve](#serve)
  - [build](#build)
  - [all](#all)
- [Configuration](#configuration)
- [Output Structure](#output-structure)
- [Data Contract (for React Native)](#data-contract-for-react-native)
- [Troubleshooting](#troubleshooting)
- [Tech Stack](#tech-stack)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     sitescrape CLI                       │
│                                                         │
│  login → import-cookies → check → crawl → convert       │
│      ↑                                        ↑         │
│   Playwright                        Markdown          │
│   headed/sync                      conversion         │
└──────────────┬──────────────────────────────┬─────────┘
               │                              │
    ┌──────────▼──────────┐      ┌────────────▼────────────┐
    │  auth/               │      │  output/                   │
    │  state.json         │      │  docs/ assets/             │
    │  (browser session)  │      │  index.md  *.md assets/   │
    └─────────────────────┘      └────────────┬─────────────┘
                                              │
                                 ┌────────────▼────────────┐
                                 │  cache/                    │
                                 │  manifest.json *.html      │
                                 └───────────────────────────┘
```

**Pipeline (5 steps, each is a standalone CLI command):**

1. **`login`** — Opens a headed Chromium browser at the target URL. The human logs in manually; Playwright captures the session cookies/storage and writes `auth/state.json`.
2. **`import-cookies`** (optional) — Reads a Netscape-format `cookies.txt` and converts it to Playwright's `state.json` format. Useful when you already have session cookies from an extension or another tool.
3. **`check`** — Launches headless Chromium with the saved session and visits a URL to verify the session is still valid.
4. **`crawl`** — Breadth-first crawl using the saved session. Fetches each page, saves the raw HTML to `cache/`, and builds `cache/manifest.json`.
5. **`convert`** — Reads `cache/manifest.json`, downloads referenced images, extracts main content via readability + trafilatura, rewrites internal links, generates YAML front matter, and writes organised Markdown under `output/docs/`.
6. **`package`** (new) — Zips the output directory into a `.tar.gz` archive or produces a structured JSON index, without any secrets or local config.

---

## Commands

### `sitescrape login`

Open a headed browser at a URL so you can log in manually, then capture the session state.

**Usage:**

```bash
sitescrape login <URL> [--auth-dir AUTH_DIR]
```

**Arguments:**

| Arg | Required | Description |
|-----|----------|-------------|
| `URL` | yes | The URL to navigate to for manual login |

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--auth-dir` | `./auth` | Directory where `state.json` will be written |

**Example:**

```bash
sitescrape login https://docs.example.com --auth-dir ./my-auth
```

> **Note:** No credentials are stored in the project. The session state file (`state.json`) contains browser cookies and localStorage — it is git-ignored and must be treated as secret.

---

### `sitescrape import-cookies`

Convert a browser's Netscape-format `cookies.txt` file into Playwright session state.

**Usage:**

```bash
sitescrape import-cookies <COOKIES_FILE> [--auth-dir AUTH_DIR]
```

**Example:**

```bash
sitescrape import-cookies cookies.txt --auth-dir ./my-auth
```

---

### `sitescrape check`

Verify that a saved session still looks logged in by visiting a URL headlessly.

**Usage:**

```bash
sitescrape check <URL> [--auth-dir AUTH_DIR]
```

**Example:**

```bash
sitescrape check https://docs.example.com
```

Output will indicate `✅ Looks logged in.` or `❌ Looks logged out` with diagnostic details.

---

### `sitescrape crawl`

Breadth-first crawl of a logged-in site using the saved session.

**Usage:**

```bash
sitescrape crawl <START_URL> [OPTIONS]
```

**Arguments:**

| Arg | Required | Description |
|-----|----------|-------------|
| `START_URL` | yes | The URL to begin crawling from |

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--max-pages` | int | 500 | Maximum pages to crawl |
| `--delay` | float | 1.5 | Seconds between requests |
| `--include` | str[] | — | Only crawl URLs matching these path prefixes or regexes |
| `--exclude` | str[] | — | Skip URLs matching these path prefixes or regexes |
| `--auth-dir` | str | `./auth` | Directory containing `state.json` |
| `--cache-dir` | str | `./cache` | Directory for crawled HTML files and manifest |
| `--refresh` | flag | false | Re-crawl pages already in the manifest |

**Example:**

```bash
sitescrape crawl https://docs.example.com/docs \
  --max-pages 100 \
  --delay 2.0 \
  --include /docs/guides \
  --exclude /docs/admin \
  --auth-dir ./my-auth
```

**What it does:**

- Visits each URL with the authenticated Playwright session.
- Saves raw HTML to `cache/<slug>.html`.
- Appends entries to `cache/manifest.json`.
- Stops early if a login page or 401/403 is detected (session expired).
- Reports a summary of pages saved, skipped, and failed.

---

### `sitescrape convert`

Convert cached HTML pages into organised Markdown files with front matter and rewritten links.

**Usage:**

```bash
sitescrape convert [OPTIONS]
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--cache-dir` | `./cache` | Directory containing crawled pages and `manifest.json` |
| `--output-dir` | `./output/docs` | Directory to write Markdown files |

**Example:**

```bash
sitescrape convert --cache-dir ./cache --output-dir ./output/docs
```

**What it does:**

1. Reads `cache/manifest.json`.
2. Downloads referenced images into `output/docs/assets/`.
3. Extracts main content (strips nav, headers, footers, scripts).
4. Replaces internal links with relative Markdown references.
5. Adds YAML front matter (`title`, `source_url`, `scraped_at`).
6. Writes Markdown files into the matching directory structure.
7. Generates `output/docs/index.md` as a table of contents grouped by folder.

---

### `sitescrape package`

Package the converted output for distribution to mobile/other consumers.

**Usage:**

```bash
sitescrape package <INPUT_DIR> --format FORMAT [--output OUTPUT]
```

**Arguments:**

| Arg | Required | Description |
|-----|----------|-------------|
| `INPUT_DIR` | yes | Directory containing converted Markdown (default: `./output/docs`) |

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--format` | str | `tarball` | `tarball` (`.tar.gz`) or `json-index` (`.json`) |
| `--output` | str | auto-generated | Output file path |

**Examples:**

```bash
# Create a tarball archive
sitescrape package ./output/docs

# Create a structured JSON index
sitescrape package ./output/docs --format json-index --output docs-index.json
```

**What's excluded from packaging:**

- `auth/` directory (contains session state / credentials)
- `cache/` directory (raw HTML — the Markdown is the deliverable)
- `.gitignore`, `__pycache__/`, `.venv/`, test files
- Any file named `.env`, `secrets.*`, or matching `*password*`, `*secret*`, `*token*`

**JSON index structure** (see [Data Contract](#data-contract-for-react-native) for full type spec):

```json
{
  "version": "1.0",
  "scraped_at": "2026-10-11T10:00:00Z",
  "pages": [
    {
      "slug": "guides/setup",
      "title": "Setup Guide",
      "source_url": "https://docs.example.com/guides/setup",
      "scraped_at": "2026-10-11T10:05:00",
      "content": "# Setup Guide\n\nFollow these steps...",
      "assets": ["assets/a1b2c3d4e5f6.png"]
    }
  ]
}
```

---

### `sitescrape site`

Generate an MkDocs configuration (`output/mkdocs.yml`) from the converted Markdown output. Uses MkDocs Material theme with search enabled and navigation auto-built from the directory structure.

**Usage:**

```bash
sitescrape site [OPTIONS]
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--docs-dir` | `./output/docs` | Directory containing converted Markdown files |
| `--output` | `./output/mkdocs.yml` | Path to write mkdocs.yml |

**Example:**

```bash
sitescrape site --docs-dir ./output/docs --output ./output/mkdocs.yml
```

---

### `sitescrape serve`

Start a local MkDocs dev server to preview the site. Serves on `0.0.0.0` so it can be viewed from other machines on the home network.

**Usage:**

```bash
sitescrape serve [OPTIONS]
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--docs-dir` | `./output/docs` | Directory containing Markdown files |
| `--config` | `./output/mkdocs.yml` | Path to mkdocs.yml |
| `--port` / `-p` | 8090 | Port to serve on |

**Example:**

```bash
sitescrape serve --port 8090
```

Press Ctrl+C to stop the server.

---

### `sitescrape build`

Build a static HTML site from the converted Markdown output into `output/site/`, ready to deploy to any web server.

**Usage:**

```bash
sitescrape build [OPTIONS]
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--docs-dir` | `./output/docs` | Directory containing Markdown files |
| `--config` | `./output/mkdocs.yml` | Path to mkdocs.yml |
| `--strict` | off | Enable strict mode (treat warnings as errors) |

**Example:**

```bash
sitescrape build --strict
```

---

### `sitescrape all`

Convenience wrapper that runs `crawl` → `convert` → `site` in one command. Supports all options of the `crawl` sub-command.

**Usage:**

```bash
sitescrape all <START_URL> [OPTIONS]
```

**Example:**

```bash
sitescrape all https://docs.example.com --max-pages 50 --delay 0.5
```

---

## Configuration

### Project structure (as produced)

```
project/
├── auth/                    # BROWSER SESSION STATE — DO NOT COMMIT
│   └── state.json
├── cache/                   # RAW HTML + MANIFEST
│   ├── manifest.json
│   └── *.html
├── output/
│   ├── docs/                # CONVERTED MARKDOWN
│   │   ├── index.md         # TOC / table of contents
│   │   ├── *.md             # Individual pages
│   │   └── assets/          # Downloaded images
│   └── package/             # PACKAGED OUTPUT (from `package` command)
├── src/sitescrape/          # SOURCE CODE
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py               # Typer CLI entry-point
│   ├── login.py             # `login` command
│   ├── cookies.py           # `import-cookies` command
│   ├── check.py             # `check` command
│   ├── crawler.py           # `crawl` command
│   ├── converter.py         # `convert` command
│   ├── archive.py           # `package` command
│   ├── site.py              # Site config data model
│   ├── build.py             # Build helpers
│   ├── output.py            # Output helpers
│   ├── site.py              # Site schema helpers
│   ├── extractor.py         # Content extraction utilities
│   ├── logger.py            # Structured JSON logging (Pino-compatible)
│   ├── models.py            # Shared data models (ScrapedPage, etc.)
│   ├── retry.py             # Exponential backoff retry logic
│   ├── shutdown.py          # Graceful shutdown hooks
│   ├── serve.py             # Local dev server (optional)
│   └── validator.py         # Output validation helpers
├── tests/                   # Test suite
├── pyproject.toml           # Project metadata & dependencies
├── .gitignore
└── README.md
```

### `auth/state.json`

The only file you should never commit. It contains Playwright session state (cookies, localStorage, etc.) and is git-ignored by default.

### Adding new sites

Create a new site config (see [site.py](src/sitescrape/site.py) for the schema):

```python
# Example site config
SITE_CONFIG = {
    "name": "Example Docs",
    "base_url": "https://docs.example.com",
    "selectors": {
        "username": "input[name='email']",
        "password": "input[name='password']",
        "submit": "button[type='submit']",
    },
    "crawl_rules": {
        "include": ["/docs/"],
        "exclude": ["/admin", "/api/"],
    },
}
```

---

## Output Structure

After running `sitescrape convert`, the output directory looks like:

```
output/docs/
├── index.md                  # Master table of contents
├── guides/
│   ├── setup.md              # Converted markdown
│   └── faq.md
├── reference/
│   └── api.md
└── assets/
    ├── a1b2c3d4e5f6.png      # Downloaded images (deterministic names)
    └── f7e8d9c0b1a2.jpg
```

**Markdown file format:**

Each `.md` file uses YAML front matter:

```yaml
---
title: Setup Guide
source_url: https://docs.example.com/guides/setup
scraped_at: 2026-10-11T10:05:00Z
---

# Setup Guide

Follow these steps...

[See the FAQ](guides/faq.md) for more info.
```

---

## Data Contract (for React Native)

The complete data contract is documented in [`DATA_CONTRACT.md`](DATA_CONTRACT.md).

**Quick reference — `ScrapedPage` type:**

```typescript
interface ScrapedPage {
  slug: string;
  title: string;
  sourceUrl: string;
  scrapedAt: string;        // ISO 8601
  content: string;           // Markdown body (no front matter)
  assets: string[];          // Relative asset paths
  breadcrumbs?: string[];    // e.g. ["Home", "Guides", "Setup"]
  headings?: {               // TOC for this page
    level: 1 | 2 | 3 | 4 | 5 | 6;
    text: string;
  }[];
}
```

See [`DATA_CONTRACT.md`](DATA_CONTRACT.md) for the full specification, including the JSON index format, navigation schema, and RN integration checklist.

---

## Troubleshooting

### Session expires mid-crawl

```
⚠️  Redirected to login page. Stopping crawl.
```

**Fix:** Re-run `sitescrape login` and then re-run `sitescrape crawl --refresh`.

### `state.json not found`

```
Error: auth state file not found at ./auth/state.json
```

**Fix:** Run `sitescrape login <URL>` first.

### 403/401 during crawl

**Fix:** Your session has expired. Run `sitescrape login` to get a new session, then re-run crawl.

### `manifest.json not found in cache`

**Fix:** Run `sitescrape crawl` before running `sitescrape convert`.

### Image download failures

Images are downloaded asynchronously and failures are silently skipped. If images are missing from the output, check:
- The image URLs are accessible (not behind additional auth).
- You have internet access from the crawl environment.

### Too many pages / crawl takes too long

Adjust these options:

- `--max-pages <N>` — cap the number of pages
- `--delay <seconds>` — increase delay between requests
- `--include /prefix` / `--exclude /prefix` — narrow the crawl scope

### `ImportError` on installation

Make sure you have Python 3.11+:

```bash
python --version  # must be 3.11 or higher
pip install -e ".[dev]"
```

---

## Tech Stack

| Component | Library | Purpose |
|-----------|---------|---------|
| HTTP / Browser automation | [Playwright](https://playwright.dev/) >= 1.40.0 | Headed/headless Chromium, session management |
| CLI framework | [Typer](https://typer.tiangolo.com/) >= 0.9.0 | Fast CLI commands with auto-help |
| HTML parsing | [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) + [lxml](https://lxml.de/) >= 4.12 / 5.0 | DOM manipulation, element extraction |
| Content extraction | [readability-lxml](https://github.com/bmaupin/readability-lxml) >= 0.9.0 | Mozilla Readability — extract main content |
| Content extraction | [trafilatura](https://github.com/adbar/trafilatura) >= 2.0.0 | Alternative extraction, tables |
| Markdown generation | [markdownify](https://github.com/matthias-k/markdownify) >= 0.11.0 | HTML → Markdown conversion |
| Logging | Pino-compatible JSON logs | Structured, machine-parseable log output |
| Testing | pytest >= 7.0.0 | Unit and E2E tests |

---

## Contributing

1. Create a feature branch: `git checkout -b feat/your-feature`
2. Make changes and add tests
3. Run tests: `pytest`
4. Submit a pull request

## License

Private — all rights reserved.
