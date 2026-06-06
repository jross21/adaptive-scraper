"""Project-wide configuration constants (env-overridable where useful)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load a project-local .env (if present) before reading any settings below, so
# ANTHROPIC_API_KEY and the SCRAPER_* values can live in a file instead of the shell.
# override=False (the default) means a real environment variable always wins over .env.
load_dotenv()

#: Descriptive User-Agent — politeness/identification from day one (spec §10).
USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "adaptive-scraper/0.1 (+https://github.com/jross21/adaptive-scraper)",
)

#: HTTP request timeout, seconds.
REQUEST_TIMEOUT = float(os.getenv("SCRAPER_TIMEOUT", "20"))

#: Target token budget for the compressed DOM sent to the codegen agent.
TOKEN_BUDGET = int(os.getenv("SCRAPER_TOKEN_BUDGET", "10000"))

#: Where run snapshots + records are written.
RUNS_DIR = Path(os.getenv("SCRAPER_RUNS_DIR", "runs"))

#: The single codegen model (the spec's "Sonnet-class first pass"). The Sonnet→Opus
#: escalation ladder was removed in Phase 3 for cost/simplicity. Override for a one-off via
#: SCRAPER_CODEGEN_MODEL (Opus ids are handled too — see codegen.agent._request_kwargs).
CODEGEN_MODEL = os.getenv("SCRAPER_CODEGEN_MODEL", "claude-sonnet-4-6")

#: How many codegen attempts the self-heal repair loop makes before giving up.
MAX_REPAIR_ATTEMPTS = int(os.getenv("SCRAPER_MAX_REPAIR_ATTEMPTS", "2"))

#: SQLite spec-cache database (Phase 3). Reused specs skip codegen entirely; the LLM only
#: fires on first contact or structural/content drift. Override with SCRAPER_CACHE_DB.
CACHE_DB = Path(os.getenv("SCRAPER_CACHE_DB", "cache.db"))

#: Crawl politeness/caps (pagination + list→detail). A crawl reuses one cached spec across
#: all pages, so extra pages cost no LLM tokens — these bound fetch volume, not cost.
CRAWL_DELAY = float(os.getenv("SCRAPER_CRAWL_DELAY", "1.0"))  # seconds between requests
MAX_PAGES = int(os.getenv("SCRAPER_MAX_PAGES", "10"))  # pagination cap per crawl
MAX_DETAIL_PAGES = int(os.getenv("SCRAPER_MAX_DETAIL_PAGES", "25"))  # detail-fetch cap per crawl
