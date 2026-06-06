# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An **adaptive self-writing web scraper**. The core inversion: the **output schema is the
permanent contract**, and an LLM writes the per-site extraction logic (an `ExtractionSpec`)
at runtime. The spec is disposable — regenerated on first contact or on breakage. The only
nondeterministic step is codegen; everything downstream (`interpret → validate`) is pure and
replayable. Phases 1 ("prove the loop"), 2 ("self-heal + render"), and 3 ("fingerprint cache +
drift detection") are implemented. A SQLite spec cache means the LLM only fires on first contact
or structural/content drift — steady-state runs of a known site reuse the cached spec ($0).

## Commands

Uses `uv` (Python ≥3.13). Always run via `uv run`.

```bash
uv sync                              # install deps
uv run playwright install chromium   # one-time: headless browser for JS rendering
cp .env.example .env                 # paste ANTHROPIC_API_KEY into .env (needed for live codegen/tests)

# Run (live: fetch → codegen → extract → validate → print JSON, write run record)
uv run scrape "<url>" --target job_postings
uv run scrape "<url>" --target job_postings --render          # force JS render up front
uv run scrape --from-snapshot runs/<run-id> --target job_postings  # offline replay, reuses stored spec

# Tests
uv run pytest                        # full suite, fully offline (default markers deselect live+render)
uv run pytest tests/test_interpreter.py            # one file
uv run pytest tests/test_interpreter.py::test_name # one test
uv run pytest -k repair              # by name substring
uv run pytest -m live                # hits real Anthropic API (needs ANTHROPIC_API_KEY)
uv run pytest -m render              # drives real headless browser (needs chromium installed)
```

There is no configured linter/formatter.

## Architecture

The pipeline is a fixed, deterministic spine with codegen as the one swappable, nondeterministic stage:

```
URL + target → recon (fetch/render + snapshot) → compress DOM → fingerprint + cache lookup
             → codegen (LLM, self-heal) on miss/drift only → interpret (deterministic)
             → validate (Pydantic) → print JSON + write run record
```

Key design seams to understand before changing anything:

- **`pipeline.py` is the testability seam.** `run_pipeline(..., spec=...)` skips codegen and
  runs the pure spine — this is how the whole loop is tested offline with a committed fixture +
  known-good spec. Omit `spec` to generate one via the LLM. The CLI (`cli.py`) is a thin wrapper.

- **Codegen is decoupled from validation via a callback.** `codegen/agent.py` never imports the
  validate layer at runtime (TYPE_CHECKING only). `run_pipeline` injects the "definition of
  success" (`interpret → validate`) as the `evaluate` callback into `generate_spec_with_repair`.

- **Single-model self-heal loop** (`generate_spec_with_repair`): generate a spec, evaluate it, and
  on failure feed the validation errors back to the model (`build_repair_message`) and retry, up to
  `MAX_REPAIR_ATTEMPTS` on a single model (`CODEGEN_MODEL`, default Sonnet 4.6). **Model escalation
  was removed** (cost/simplicity) — there is no Sonnet→Opus ladder. It **never raises on validation
  failure** — it returns the best-effort spec with `ok=False` so the caller still writes a run
  record and exits non-zero.

- **Fingerprint spec cache** (`compress/fingerprint.py` + `cache/cache.py`, wired in `pipeline.py`):
  before codegen, `run_pipeline` hashes the page's *structure* into a fingerprint and looks it up in
  a SQLite cache keyed by URL+target. Fingerprint match + cached spec still validates → **hit**
  (reuse spec, skip the LLM); fingerprint differs or cached spec fails → **drift** (regenerate +
  refresh); no entry → **miss**. The fingerprint is content-insensitive (row counts, salaries,
  dates, `href`s, build-hashed class suffixes don't trip it) but structure-sensitive. The cache is
  fail-open: a corrupt/old row is treated as a miss, never an error. `SqliteSpecCache` sits behind a
  `SpecCacheRepo` Protocol so Phase 4 can swap in Postgres without touching callers. Passing `spec=`
  (replay) bypasses the whole block, so the cache is never consulted on replay.

- **Structured outputs**: codegen uses `client.messages.parse(output_format=ExtractionSpec)`, so
  the model is forced to return a schema-valid spec. `_request_kwargs` keeps `temperature=0` for
  Sonnet 4.6 but omits `temperature`/`top_p`/`top_k` for Opus 4.7/4.8 (they return HTTP 400) — kept
  as a guard for anyone overriding `SCRAPER_CODEGEN_MODEL` to an Opus id.

- **Deterministic, sandbox-free execution.** `execute/interpreter.py` runs the spec with four
  selector types (`css | xpath | json_ld | regex`) and a **closed whitelist** of named transforms
  (`execute/transforms.py`) — no arbitrary code runs. The codegen prompt lists exactly the
  whitelisted transform names. Transforms raise on unparseable input; the interpreter catches
  that and records the field as missing, so a bad mapping surfaces as a high null-rate in
  validation rather than crashing the run.

- **Auto-fallback to rendering** (`cli.py`): on a live run, if static extraction fails and the page
  `looks_js_rendered`, it re-fetches with `render_page` (Playwright), overwrites the snapshot, and
  retries once with `rendered=True`.

## Targets (adding a new schema)

A target is the contract a run is judged against. `ExtractionTarget` (`models/target.py`) bundles
the Pydantic `item_schema` class, `cardinality` (`one`|`many`), `min_rows`, and `required_fields`.
The only registered target is `job_postings` (`JobPosting`) in `models/schema.py::TARGET_REGISTRY`.
**Add new targets there** — the rest of the pipeline is schema-agnostic.

## Runs / replay

A live run writes `runs/<run-id>/` containing `page.html`, `meta.json`, and `run.json` (the
`ScrapeRun` record: generated spec, `attempts`, `repair_log`, token usage, plus `cache_status` and
`fingerprint`). Because the snapshot stores both the page **and** the spec, any run is fully
reproducible offline via `--from-snapshot` (no API call). The spec cache is a *separate* mutable
store (`cache.db`) keyed by URL+target — distinct from the append-only `runs/` history; CLI flags
`--no-cache` / `--refresh` / `--cache-db` control it.

## Config

`config.py` calls `load_dotenv()` at import, so a project-root `.env` (gitignored;
`.env.example` is the committed template) is read automatically for `ANTHROPIC_API_KEY` and
the settings below. A real shell env var overrides `.env`. Constants in `config.py`, all
env-overridable: `SCRAPER_CODEGEN_MODEL` (default `claude-sonnet-4-6`), `SCRAPER_MAX_REPAIR_ATTEMPTS`
(default `2`), `SCRAPER_CACHE_DB` (default `cache.db`), `SCRAPER_RUNS_DIR` (default `runs`),
`SCRAPER_USER_AGENT`, `SCRAPER_TIMEOUT`, `SCRAPER_TOKEN_BUDGET` (compressed-DOM char budget sent to
codegen).

## Still deferred (later phases)

Pagination / list→detail crawl (2+); n8n orchestration, warehouse sink, dead-lettering, cost
dashboards, Postgres-backed cache (4); generated-code fallback + sandbox, proactive drift
regeneration (5).
