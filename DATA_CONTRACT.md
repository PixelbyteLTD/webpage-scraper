# Data Contract — sitescrape → React Native

This document defines the data interface between the **sitescrape** scraper (Phase 1) and the **React Native docs app** (Phase 2). It covers output schema, front matter structure, packaging formats, and the integration checklist.

---

## 1. Output Overview

After a full pipeline run (`crawl` → `convert` → `package`), consumers receive one of two formats:

| Format | File | Use case |
|--------|------|----------|
| **Tarball** | `sitescrape-output.tar.gz` | Download + extract as file tree |
| **JSON index** | `sitescrape-output.json` | Stream to RN app, parse as data |

Both contain the same pages in the same structure. The tarball is a convenience format for file-based distribution; the JSON index is the primary programmatic interface.

---

## 2. JSON Index Schema

The JSON index is a single top-level object:

```json
{
  "version": "1.0",
  "generated_at": "2026-10-11T10:30:00Z",
  "pages": [ScrapedPage, ...]
}
```

### Top-level fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | string | yes | Schema version — always `"1.0"` |
| `generated_at` | string | yes | ISO 8601 timestamp of package generation |
| `pages` | array | yes | Array of `ScrapedPage` objects (see §3) |

---

## 3. ScrapedPage Schema

Each crawled page is represented as a `ScrapedPage` object:

```typescript
interface ScrapedPage {
  /**
   * File-system-safe path relative to output root,
   * without the .md extension.
   * Examples: "index", "guides/setup", "reference/api"
   */
  slug: string;

  /** Human-readable page title. */
  title: string;

  /** The original source URL. */
  sourceUrl: string;

  /** When the page was scraped (ISO 8601). */
  scrapedAt: string;

  /** Markdown content (body only — no YAML front matter). */
  content: string;

  /**
   * Relative asset paths for images referenced by this page.
   * Paths are relative to the output root, e.g.:
   * ["assets/a1b2c3d4e5f6.png", "assets/f7e8d9c0b1a2.jpg"]
   */
  assets: string[];

  /**
   * Breadcrumb trail from root to this page.
   * Populated from directory structure:
   * "guides/setup" → ["Guides", "Setup"]
   * "" → ["Home"]
   * Omitted if trivial.
   */
  breadcrumbs?: string[];

  /**
   * Table of contents extracted from h1–h6 elements.
   */
  headings?: {
    level: 1 | 2 | 3 | 4 | 5 | 6;
    text: string;
  }[];
}
```

### Field details

#### `slug`

Derived from the URL path. Used as the file key and navigation key.

| URL | slug |
|-----|------|
| `https://docs.example.com/` | `index` |
| `https://docs.example.com/guides/setup` | `guides/setup` |
| `https://docs.example.com/reference/api#auth` | `reference/api` (fragment stripped) |

#### `content`

Markdown **body only**. The YAML front matter is stripped — it is represented by the other fields (`title`, `sourceUrl`, `scrapedAt`) on the `ScrapedPage` object itself.

Link rewriting is applied: internal links are relative `.md` paths (`[FAQ](guides/faq)`), while external links are preserved as-is.

#### `assets`

List of local asset paths for images downloaded during conversion. The asset filenames are deterministic (MD5 hash of the original URL), so the same image always maps to the same file.

---

## 4. Tarball Format

When `--format tarball` is used, the output is a `.tar.gz` archive containing:

```
sitescrape-output/
├── index.json          # JSON index (same schema as above)
├── index.md            # Markdown TOC
├── index.html          # HTML-rendered TOC for browser viewing
├── guides/
│   ├── setup.md
│   └── faq.md
├── reference/
│   └── api.md
└── assets/
    ├── a1b2c3d4e5f6.png
    └── f7e8d9c0b1a2.jpg
```

The `index.json` at the archive root is the single source of truth for programmatic consumers.

---

## 5. Exclusions (secrets safety)

The following are **never** included in either format:

| Category | Pattern | Reason |
|----------|---------|--------|
| Session state | `auth/` | Contains cookies / tokens |
| Raw HTML | `cache/` | Intermediate data, not deliverable |
| Env files | `.env`, `*.env.*` | Secrets |
| Credential files | `*password*`, `*secret*`, `*token*`, `*key*` | Accidental secrets |
| Python artifacts | `__pycache__/`, `*.pyc`, `*.egg-info/` | Build noise |
| VCS | `.git/`, `.gitignore` (as content) | Not deliverable |
| IDE files | `.idea/`, `.vscode/`, `*.swp` | Dev tooling |
| Test files | `tests/` | Not runtime data |
| Build artifacts | `dist/`, `build/`, `htmlcov/` | Not deliverable |

---

## 6. React Native Integration Checklist

### 6.1 Data ingestion

```typescript
import { ScrapedPage, Index as ScrapedIndex } from './types';

async function loadIndex(): Promise<ScrapedIndex> {
  // Option A: bundled JSON asset
  const json = require('../assets/sitescrape-output.json');
  return json as ScrapedIndex;

  // Option B: download from CDN/server
  // const res = await fetch('https://cdn.example.com/docs/index.json');
  // return res.json() as ScrapedIndex;
}
```

### 6.2 Page rendering

```typescript
import Markdown from 'react-native-markdown-display';

function ScrapedPageView({ page }: { page: ScrapedPage }) {
  return (
    <ScrollView>
      <Text style={styles.title}>{page.title}</Text>
      {page.breadcrumbs && <Breadcrumb items={page.breadcrumbs} />}
      <Markdown style={markdownStyles}>
        {page.content}
      </Markdown>
      {page.headings && <PageTOC headings={page.headings} />}
    </ScrollView>
  );
}
```

### 6.3 Image handling

Images in `assets/` are local files in the tarball. For the JSON flow, the app must either:
- Have the assets bundled locally (preferred for offline), or
- Download them on demand using the paths from `page.assets`.

```typescript
function ImageAsset({ path }: { path: string }) {
  // path is relative to app's assets directory
  return <Image source={{ uri: `file://${assetsPath}/${path}` }} />;
}
```

### 6.4 Navigation

Use `slug` as the navigation key:

```typescript
// Build navigation tree from pages
const navigationTree = buildNavTree(pages: ScrapedPage[]): NavNode[] {
  const root: NavNode = { title: 'Home', slug: '', children: [] };
  for (const page of pages) {
    const segments = page.slug.split('/').filter(Boolean);
    let node = root;
    for (let i = 0; i < segments.length; i++) {
      const key = segments.slice(0, i + 1).join('/');
      let child = node.children?.find(c => c.slug === key);
      if (!child) {
        child = {
          title: segments[i].replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
          slug: key,
          page: i === segments.length - 1 ? page : undefined,
          children: [],
        };
        node.children?.push(child);
      }
      node = child;
    }
  }
  return [root];
};
```

### 6.5 Search (optional)

Build a search index from `page.content` at app start:

```typescript
interface SearchResult {
  pageSlug: string;
  pageTitle: string;
  snippet: string;
  score: number;
}

function indexSearchContent(pages: ScrapedPage[]): void {
  // Tokenize content, build inverted index
  // Store in AsyncStorage or SQLite for offline search
}
```

---

## 7. Versioning

- **`version`** field on the JSON index is `"1.0"`.
- If the schema changes, increment the version (`"2.0"`, etc.).
- The RN app should check the version and display a message if it encounters an incompatible schema.

---

## 8. Error Handling

### Missing content
- `content` is an empty string if no content was extractable.
- The page will still appear in the index but may show a placeholder in the UI.

### Missing assets
- `assets` can be an empty array.
- Image components should handle missing files gracefully (show placeholder).

### Invalid markdown
- If the content contains malformed Markdown, the rendering library should gracefully degrade (show raw text).

---

## 9. Example: Full Round Trip

```bash
# Phase 1: Scrape & convert
sitescrape login https://docs.example.com
sitescrape crawl https://docs.example.com/docs
sitescrape convert

# Phase 1 (new): Package for RN
sitescrape package ./output/docs --format json-index --output docs-index.json

# Phase 2: RN app consumes docs-index.json
# 1. Load JSON with loadIndex()
# 2. Render with ScrapedPageView
# 3. Navigate using slug
# 4. Display images from assets/
```

---

## 10. Migration from File Tree to JSON

If migrating an existing tarball consumer to the JSON index:

1. Replace file-tree imports with `loadIndex()`.
2. Replace `require('./guides/setup.md')` with `pages.find(p => p.slug === 'guides/setup')?.content`.
3. Replace `require('./assets/image.png')` with `ImageAsset({ path: 'assets/image.png' })`.
4. Update navigation to use the `buildNavTree()` helper.

No content changes are required — only the ingestion layer changes.
