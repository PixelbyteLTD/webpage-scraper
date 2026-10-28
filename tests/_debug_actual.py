#!/usr/bin/env python3
"""Debug script to understand actual source behavior."""

import json
from pathlib import Path

# 1. Check cookie parsing
from sitescrape.cookies import _parse_netscape_cookie_line
line = ".example.com\tTRUE\t/\tTRUE\t0\tSESSION_ID\tabc123"
r = _parse_netscape_cookie_line(line)
print("1. Cookie parsing:", r)

# 2. Check frontmatter parsing
content = "---\ntitle: Test\nsource_url: https://example.com\nscraped_at: 2026-01-01T00:00:00Z\n---\n"
print("2. Frontmatter split result:")
parts = content.split("---\n")
for i, p in enumerate(parts):
    print(f"   part {i}: {repr(p)}")

# 3. Check _slugify_url from crawler
from sitescrape.crawler import _slugify_url
print("3. Crawler slugify:", _slugify_url("https://example.com/guides/setup"))

# 4. Check _slugify_url from converter
from sitescrape.converter import _slugify_url as cv_slugify
print("4. Converter slugify:", cv_slugify("https://example.com/guides/setup"))

# 5. Check _image_ext from converter
from sitescrape.converter import _image_ext
print("5. Image ext .jpg:", _image_ext("https://example.com/photo.jpg"))
print("   Image ext .png:", _image_ext("https://example.com/photo.png"))
print("   Image ext .JPG:", _image_ext("https://example.com/photo.JPG"))

# 6. Check normalise
from sitescrape.crawler import _normalise
print("6. Normalise same domain:", _normalise("/page", "https://example.com/"))
print("   Normalise guides path:", _normalise("/guides/setup", "https://example.com/"))
print("   Normalise trailing slash:", _normalise("/guides/", "https://example.com/"))

# 7. Check _is_excluded from archive
from sitescrape.archive import _is_excluded
base = Path("/tmp/test/docs")
print("7. _is_excluded on auth:", _is_excluded(Path("/tmp/test/docs/auth/state.json"), base))

# 8. Check _scan_docs_dir
from sitescrape.site import _scan_docs_dir
import tempfile
with tempfile.TemporaryDirectory() as td:
    tp = Path(td)
    (tp / "setup.md").write_text("# Setup")
    (tp / "faq.md").write_text("# FAQ")
    (tp / "index.md").write_text("# Index")
    result = _scan_docs_dir(tp)
    print("8. _scan_docs_dir:", result)

# 9. Check _build_nav
from sitescrape.site import _build_nav
pages = [
    {"path": "guides/setup.md", "title": "Setup"},
    {"path": "index.md", "title": "Index"},
]
print("9. _build_nav:", json.dumps(_build_nav(pages), indent=2))

# 10. Check _relative_md_path
from sitescrape.converter import _relative_md_path
url_map = {"https://example.com/guides/": ("guides", "index.md")}
result = _relative_md_path("guides", "https://example.com/guides/", url_map)
print("10. _relative_md_path:", result)

# 11. Check login detection patterns
import re
from sitescrape.check import _LOGIN_TEXT_PATTERNS
print("11. Login text patterns:")
for p in _LOGIN_TEXT_PATTERNS:
    test = "Log In"
    match = re.search(p, test)
    print(f"   Pattern {p!r} -> matches 'Log In': {match is not None}")

# 12. Check _has_avoid_links regex
from sitescrape.crawler import _AVOID_LINK_TEXT_RE
print("12. _AVOID_LINK_TEXT_RE:")
for text in ["Logout", "Sign Out", "next page", "Documentation"]:
    print(f"   Matches '{text}': {_AVOID_LINK_TEXT_RE.search(text) is not None}")

# 13. Check _rewrite_links
from sitescrape.converter import _rewrite_links
url_map = {"https://example.com/page": ("", "page.md")}
md = "[](https://example.com/page)"
result = _rewrite_links(md, url_map, "", url_map)
print("13. _rewrite_links blank link:", repr(result))

# 14. Check _extract_main_content with empty
from sitescrape.converter import _extract_main_content
print("14. _extract_main_content empty:", repr(_extract_main_content("<html><body></body></html>")))

# 15. Check _download_images with failure
from sitescrape.converter import _download_images
import tempfile
with tempfile.TemporaryDirectory() as td:
    html = '<img src="https://example.com/missing.png">'
    with open(f"{td}/_debug.py", "w") as f:
        f.write("""
from unittest import mock
from pathlib import Path
from sitescrape.converter import _download_images
import tempfile
with tempfile.TemporaryDirectory() as td:
    html = '<img src=\"https://example.com/missing.png\">'
    with mock.patch("urllib.request.urlopen", side_effect=Exception("404")):
        try:
            result = _download_images(html, Path(td))
            print("download_images result:", result)
        except Exception as e:
            print("download_images error:", e)
""")
        import subprocess
        subprocess.run(["python3", f"{td}/_debug.py"])

# 16. Check _is_excluded more carefully
print("16. _is_excluded on auth path:", _is_excluded(Path("/tmp/test/docs/auth/state.json"), Path("/tmp/test/docs")))
print("    _is_excluded on auth no base:", _is_excluded(Path("/tmp/test/docs/auth/state.json"), None))
