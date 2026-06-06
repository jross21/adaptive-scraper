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

#: Codegen escalation ladder — the repair loop tries each model in order, escalating
#: to the next when a model exhausts its attempts. Sonnet-class first pass, Opus-class
#: fallback (spec §4). Override with a comma-separated list.
ESCALATION_MODELS = tuple(
    m.strip()
    for m in os.getenv(
        "SCRAPER_ESCALATION_MODELS", "claude-sonnet-4-6,claude-opus-4-8"
    ).split(",")
    if m.strip()
)

#: How many codegen attempts the repair loop makes per model before escalating.
MAX_REPAIR_ATTEMPTS = int(os.getenv("SCRAPER_MAX_REPAIR_ATTEMPTS", "2"))
