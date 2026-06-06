# Adaptive Self-Writing Scraper

A web scraper where the **output schema is the fixed contract** and an LLM writes the
per-site extraction logic at runtime. The schema is permanent; the generated extraction
spec is disposable and regenerated on first contact or on breakage.

**Phase 1 ("prove the loop")** and **Phase 2 ("self-heal + render")** are implemented: one
end-to-end vertical slice from a single URL to clean, schema-validated structured output,
with a repair loop, model escalation, and JS rendering. No cache, no code-execution sandbox
(those are Phases 3–5).

```
URL + target schema
  → fetch + snapshot      (httpx, robots.txt check, raw HTML stored for replay; --render or
                           auto-fallback drives a headless browser for JS-heavy pages)
  → compress DOM          (strip noise, surface JSON-LD, exemplars of repeating rows)
  → codegen + self-heal   (Claude emits an ExtractionSpec via structured outputs; on validation
                           failure the errors are fed back and regenerated, escalating
                           Sonnet 4.6 → Opus 4.8)
  → interpret             (deterministic: css | xpath | json_ld | regex + whitelisted transforms)
  → validate              (Pydantic rows + row-count floor + null-rate)
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

## Usage

```bash
# Live: fetch a job board, generate a spec, extract, validate, print JSON.
# If the static page fails to extract and looks JS-shelled, it auto-retries with a browser.
uv run scrape "https://example-job-board.com/jobs" --target job_postings

# Force JS rendering up front (skip the static attempt) for sites you know need it.
uv run scrape "https://spa-job-board.com/jobs" --target job_postings --render

# Replay: re-run a stored snapshot offline (reuses the stored spec — no API call).
uv run scrape --from-snapshot runs/<run-id> --target job_postings
```

When codegen's first spec doesn't validate, the **self-heal loop** feeds the validation
errors back to the model and regenerates, escalating Sonnet 4.6 → Opus 4.8 after exhausting
attempts on each. The `ScrapeRun` record captures `attempts`, `models_tried`, and a
`repair_log` so you can see what it took. (Opus 4.8 omits `temperature`, which it rejects;
Sonnet 4.6 keeps `temperature=0`.)

A live run writes `runs/<run-id>/` containing `page.html`, `meta.json`, and `run.json`
(the `ScrapeRun` record with the generated spec + token usage). The command exits
non-zero and prints the validation errors when extraction doesn't satisfy the schema.

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
  compress/    DOM -> compact, signal-rich view (JSON-LD, repeating-structure exemplars, tag outline)
  codegen/     prompts (role, constraints, prompt-injection framing) + the Claude call (structured
               outputs) + the self-heal repair loop with Sonnet->Opus escalation
  execute/     the selector-map interpreter (4 selector types) + whitelisted transform registry
  validate/    structural validation against the target schema
  pipeline.py  orchestration seam (wires the modules; pass a spec to skip codegen)
  cli.py       typer entry point
```

## Deferred to later phases

**Done:** self-heal retry loop + model escalation (2) · JS rendering (2).
**Still deferred:** pagination, list→detail crawl (2+) · structural fingerprinting +
scraper cache + drift detection (3) · n8n orchestration, warehouse sink, dead-lettering,
cost dashboards (4) · generated-code fallback + sandbox, proactive drift regeneration (5).
