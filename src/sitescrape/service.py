"""Serve command: run mkdocs serve for local preview.

Exports do_service so the CLI can reference it.
"""

from sitescrape.serve import do_serve as do_service  # noqa: F401
