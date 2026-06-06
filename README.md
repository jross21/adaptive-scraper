# Adaptive Self-Writing Scraper

A web scraper where the **output schema is the fixed contract** and an LLM writes the
per-site extraction logic at runtime. The schema is permanent; the generated extraction
spec is disposable and regenerated on first contact or on breakage.

**Phases 1–3 are implemented:** "prove the loop" (1), "self-heal + render" (2), and
"fingerprint cache + drift detection" (3) — plus **pagination + list→detail crawl**. One
end-to-end slice from a URL to clean, schema-validated structured output, with a single-model
self-heal loop, JS rendering, a structural-fingerprint spec cache that skips the LLM on
unchanged pages, and a `--crawl` mode that follows pagination across a whole board reusing the
same cached spec (no extra LLM cost). No code-execution sandbox yet (Phase 5).

```
URL + target schema
  → fetch + snapshot      (httpx, robots.txt check, raw HTML stored for replay; --render or
                           auto-fallback drives a headless browser for JS-heavy pages)
  → compress DOM          (strip noise, surface JSON-LD, exemplars of repeating rows)
  → fingerprint + cache   (hash the page structure; on a match reuse the cached spec and
                           skip the LLM — regenerate only on first contact or drift)
  → codegen + self-heal   (Claude emits an ExtractionSpec via structured outputs; on validation
                           failure the errors are fed back and regenerated on the same model)
  → crawl (--crawl)       (follow next-page links + optional per-row detail pages, reusing the
                           SAME spec on every page; snapshots each page for offline replay)
  → interpret             (deterministic: css | xpath | json_ld | regex + whitelisted transforms)
  → validate              (Pydantic rows + row-count floor + null-rate, over the merged set)
  → print JSON + run record
```

The only nondeterministic step is codegen; everything downstream is pure and replayable.
Because each run stores the page snapshot **and** the generated spec, a run is fully
reproducible offline.

## Setup

```bash
uv sync
uv run playwright install chromium    # one-time: the headless browser for JS rendering
cp .env.example .env                   # then paste your key into .env (ANTHROPIC_API_KEY)
```

The `ANTHROPIC_API_KEY` is needed only for live codegen / live runs. It's loaded
automatically from `.env` (gitignored) at startup; a shell `export ANTHROPIC_API_KEY=...`
also works and takes precedence over `.env`.

Other env vars (all optional): `SCRAPER_CODEGEN_MODEL` (default `claude-sonnet-4-6`),
`SCRAPER_CACHE_DB` (default `cache.db`), `SCRAPER_MAX_REPAIR_ATTEMPTS` (default `2`),
`SCRAPER_CRAWL_DELAY` (default `1.0`), `SCRAPER_MAX_PAGES` (default `10`),
`SCRAPER_MAX_DETAIL_PAGES` (default `25`), `SCRAPER_RUNS_DIR`, `SCRAPER_TIMEOUT`,
`SCRAPER_TOKEN_BUDGET`, `SCRAPER_USER_AGENT`.

## Usage

```bash
# Live: fetch a job board, generate a spec, extract, validate, print JSON.
# If the static page fails to extract and looks JS-shelled, it auto-retries with a browser.
uv run scrape "https://example-job-board.com/jobs" --target job_postings

# Force JS rendering up front (skip the static attempt) for sites you know need it.
uv run scrape "https://spa-job-board.com/jobs" --target job_postings --render

# Crawl: follow pagination across the whole board (one codegen, reused on every page).
uv run scrape "https://example-job-board.com/jobs" --target job_postings --crawl
# ...and enrich each row from its own detail page (more fetches; opt-in):
uv run scrape "https://example-job-board.com/jobs" --target job_postings --crawl --detail

# Replay: re-run a stored snapshot offline (reuses the stored spec — no API call). A crawl's
# pages are all snapshotted, so replay re-walks the whole crawl offline.
uv run scrape --from-snapshot runs/<run-id> --target job_postings
```

`--crawl` follows `next` links up to `--max-pages` (default 10) with a `--crawl-delay`
(default 1s) between polite requests; `--detail` follows each row's detail URL up to
`--max-detail` (default 25). The spec is generated once on page 1 and reused on every page, so
a multi-page crawl costs no extra LLM tokens. (Limitation: pages whose structure differs from
page 1 surface as a degraded null-rate rather than being re-generated per page.)

When codegen's first spec doesn't validate, the **self-heal loop** feeds the validation
errors back to the model and regenerates, up to `SCRAPER_MAX_REPAIR_ATTEMPTS` tries on a
single model (default Sonnet 4.6). The `ScrapeRun` record captures `attempts` and a
`repair_log` so you can see what it took.

The **spec cache** (Phase 3) is the cost lever. Before calling the LLM, the scraper hashes
the page's *structure* into a fingerprint and looks it up in a small SQLite cache keyed by
URL + target:

- **hit** — fingerprint matches and the cached spec still validates → reuse it, skip the LLM
  entirely ($0);
- **drift** — the structure changed, or the cached spec stopped validating → regenerate and
  refresh the cache;
- **miss** — first contact → generate and store.

So a known site costs nothing in steady state; the model only fires on first contact or a
real change. Use `--no-cache` to bypass the cache, `--refresh` to force regeneration, and
`--cache-db PATH` to point at a different database (default `cache.db`). The fingerprint is
content-insensitive (new rows, changed salaries/dates/links don't trip it) but
structure-sensitive.

A live run writes `runs/<run-id>/` containing `page.html`, `meta.json`, and `run.json`
(the `ScrapeRun` record with the generated spec, fingerprint, cache status, and token
usage). The command exits non-zero and prints the validation errors when extraction
doesn't satisfy the schema.

The only built-in target is `job_postings` (the `JobPosting` schema in
`src/adaptive_scraper/models/schema.py`). Add new targets there; the rest of the
pipeline is schema-agnostic.

## Tests

```bash
uv run pytest            # full suite, offline (no network, no API, no browser)
uv run pytest -m render  # drives a real headless browser over a local JS fixture
uv run pytest -m live    # the real proof: Sonnet 4.6 writes a working spec for the fixture
```

The default suite drives the deterministic spine with a committed fixture page +
known-good spec. The `live` test (deselected by default) calls the real API and asserts
that model-generated extraction logic validates against the schema.

## Layout

```
src/adaptive_scraper/
  models/      JobPosting + ExtractionTarget (the contract); FieldRule/ExtractionSpec (codegen output); ScrapeRun
  recon/       polite fetch (robots.txt, User-Agent) + page snapshot/replay + Playwright JS render
               + crawler.py (pagination + list→detail walk; live + replay page sources)
  compress/    DOM -> compact, signal-rich view (JSON-LD, repeating exemplars, tag outline) +
               a content-insensitive structural fingerprint of the page (fingerprint.py)
  codegen/     prompts (role, constraints, prompt-injection framing) + the Claude call (structured
               outputs) + the single-model self-heal repair loop
  cache/       SQLite spec cache — reuse a known-good spec when the fingerprint is unchanged
  execute/     the selector-map interpreter (4 selector types) + whitelisted transform registry
  validate/    structural validation against the target schema
  pipeline.py  orchestration seam (fingerprint -> cache lookup -> codegen; pass a spec to skip it all)
  cli.py       typer entry point
```

## Deferred to later phases

**Done:** self-heal retry loop (2) · JS rendering (2) · structural fingerprint cache + drift
detection (3) · pagination + list→detail crawl (2+).
**Still deferred:** n8n orchestration, warehouse sink, dead-lettering, cost dashboards,
Postgres-backed cache, retry/backoff + per-page rendering (4) · generated-code fallback +
sandbox, proactive drift regeneration (5).
