"""Command-line entry point.

  scrape <url> --target job_postings          # live: fetch, snapshot, codegen, run
  scrape --from-snapshot runs/<id> --target … # replay: reuse stored spec, offline

Prints validated JSON rows to stdout, writes a ScrapeRun record, and exits non-zero
when validation fails (surfacing the errors on stderr).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from .cache.cache import SqliteSpecCache
from .codegen.agent import DEFAULT_MODEL
from .config import CACHE_DB, CRAWL_DELAY, MAX_DETAIL_PAGES, MAX_PAGES, RUNS_DIR
from .models.run import ScrapeRun
from .models.schema import TARGET_REGISTRY
from .pipeline import PipelineOutcome, run_pipeline
from .recon.crawler import LivePageSource, ReplayPageSource
from .recon.fetch import fetch
from .recon.render import looks_js_rendered, render_page
from .recon.snapshot import (
    PAGE_FILE,
    load_manifest,
    load_snapshot,
    save_crawl_page,
    save_detail_page,
    save_manifest,
    save_snapshot,
)

app = typer.Typer(add_completion=False, help="Adaptive self-writing scraper.")

RUN_RECORD = "run.json"

_CACHE_MESSAGES = {
    "hit": "cache HIT — reused the stored spec, skipped the LLM ($0)",
    "miss": "cache MISS — first contact, generated a new spec",
    "drift_regenerated": "DRIFT — page changed, regenerated the spec",
}


def _echo_cache_status(run: ScrapeRun) -> None:
    msg = _CACHE_MESSAGES.get(run.cache_status)
    if msg:
        typer.echo(msg, err=True)


def _snapshot_crawl(snap_dir: Path, outcome: PipelineOutcome, live: LivePageSource) -> None:
    """Persist the crawled pages + detail pages and write the manifest, so --from-snapshot
    can re-walk the identical crawl offline. Page 1 is already saved as page.html."""
    crawl = outcome.crawl
    if crawl is None:
        return
    manifest: dict = {"version": 1, "pages": [], "detail": []}
    for i, url in enumerate(crawl.page_urls, start=1):
        if i == 1:
            manifest["pages"].append({"url": url, "file": PAGE_FILE})
            continue
        result = live.fetched.get(url)
        if result is None:
            continue
        path = save_crawl_page(snap_dir, i, result)
        manifest["pages"].append({"url": url, "file": path.relative_to(snap_dir).as_posix()})
    for url in crawl.detail_urls:
        result = live.fetched.get(url)
        if result is None:
            continue
        path = save_detail_page(snap_dir, url, result)
        manifest["detail"].append({"url": url, "file": path.relative_to(snap_dir).as_posix()})
    save_manifest(snap_dir, manifest)


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def _stored_spec(snapshot_dir: Path):
    record = snapshot_dir / RUN_RECORD
    if not record.exists():
        return None
    data = json.loads(record.read_text())
    spec = data.get("spec")
    if spec is None:
        return None
    from .models.spec import ExtractionSpec

    return ExtractionSpec.model_validate(spec)


@app.command()
def scrape(
    url: Optional[str] = typer.Argument(None, help="URL to scrape (omit when using --from-snapshot)."),
    target: str = typer.Option("job_postings", "--target", "-t", help="Target name from the registry."),
    from_snapshot: Optional[Path] = typer.Option(None, "--from-snapshot", help="Replay a stored snapshot dir offline."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", help="Codegen model (overrides the default Sonnet)."),
    render: bool = typer.Option(False, "--render/--no-render", help="Render JS with a headless browser up front. When off, the scraper still auto-falls-back to rendering if static extraction fails on a JS-shelled page."),
    runs_dir: Path = typer.Option(RUNS_DIR, "--runs-dir", help="Where snapshots/records are written."),
    cache: bool = typer.Option(True, "--cache/--no-cache", help="Reuse a cached spec when the page structure is unchanged (skips the LLM)."),
    refresh: bool = typer.Option(False, "--refresh", help="Ignore any cached spec and regenerate, then update the cache."),
    cache_db: Path = typer.Option(CACHE_DB, "--cache-db", help="SQLite spec-cache database path."),
    crawl: bool = typer.Option(False, "--crawl/--no-crawl", help="Follow pagination to subsequent pages (up to --max-pages)."),
    max_pages: int = typer.Option(MAX_PAGES, "--max-pages", help="Max pages to follow when crawling."),
    detail: bool = typer.Option(False, "--detail/--no-detail", help="Enrich each row from its detail page (extra fetches)."),
    max_detail: int = typer.Option(MAX_DETAIL_PAGES, "--max-detail", help="Max detail pages to fetch when --detail is on."),
    crawl_delay: float = typer.Option(CRAWL_DELAY, "--crawl-delay", help="Seconds to wait between crawl requests."),
) -> None:
    if target not in TARGET_REGISTRY:
        typer.echo(f"unknown target {target!r}; known: {', '.join(TARGET_REGISTRY)}", err=True)
        raise typer.Exit(code=2)
    target_obj = TARGET_REGISTRY[target]

    if from_snapshot is not None:
        snap = load_snapshot(from_snapshot)
        # If the run was a crawl, its manifest lets us re-walk the stored pages offline.
        manifest = load_manifest(Path(from_snapshot))
        replay_source = ReplayPageSource(from_snapshot, manifest) if manifest else None
        replay_pages = max(1, len(manifest.get("pages", []))) if manifest else 1
        replay_detail = len(manifest.get("detail", [])) if manifest else 0
        outcome = run_pipeline(
            html=snap.html,
            base_url=snap.meta.get("url"),
            target=target_obj,
            target_name=target,
            run_id=Path(from_snapshot).name,
            model=model,
            spec=_stored_spec(Path(from_snapshot)),
            snapshot_uri=str(from_snapshot),
            page_source=replay_source,
            max_pages=replay_pages,
            enrich_detail=replay_detail > 0,
            max_detail=replay_detail,
        )
    else:
        if not url:
            typer.echo("provide a URL, or use --from-snapshot", err=True)
            raise typer.Exit(code=2)
        run_id = _new_run_id()
        spec_cache = SqliteSpecCache(cache_db) if cache else None
        live = LivePageSource(delay=crawl_delay) if (crawl or detail) else None
        pipe_kw = dict(
            target=target_obj,
            target_name=target,
            run_id=run_id,
            model=model,
            use_cache=cache,
            refresh=refresh,
            cache=spec_cache,
            page_source=live,
            max_pages=max_pages if crawl else 1,
            enrich_detail=detail,
            max_detail=max_detail,
        )
        result = render_page(url) if render else fetch(url)
        snap_dir = save_snapshot(run_id, result, runs_dir=runs_dir)
        outcome = run_pipeline(
            html=result.html, base_url=result.url, snapshot_uri=str(snap_dir),
            rendered=result.rendered, **pipe_kw,
        )
        # Auto-fallback: static extraction failed on what looks like a JS shell — render
        # the page in a real browser and try once more (overwriting the snapshot).
        if not outcome.validation.ok and not result.rendered and looks_js_rendered(result.html):
            typer.echo(
                "static extraction failed on a JS-shelled page — retrying with rendering…",
                err=True,
            )
            result = render_page(url)
            snap_dir = save_snapshot(run_id, result, runs_dir=runs_dir)
            outcome = run_pipeline(
                html=result.html, base_url=result.url, snapshot_uri=str(snap_dir),
                rendered=True, **pipe_kw,
            )
        if live is not None:
            _snapshot_crawl(snap_dir, outcome, live)
            live.close()
        _write_run_record(snap_dir, outcome.run)
        _echo_cache_status(outcome.run)
        if outcome.run.pages_crawled > 1 or outcome.run.detail_pages_fetched:
            typer.echo(
                f"crawled {outcome.run.pages_crawled} page(s); "
                f"{outcome.run.detail_pages_fetched} detail page(s)",
                err=True,
            )

    typer.echo(json.dumps(outcome.validation.valid_rows, indent=2, default=str))

    if not outcome.validation.ok:
        typer.echo("\nVALIDATION FAILED:", err=True)
        for err in outcome.run.validation_errors:
            typer.echo(f"  - {err}", err=True)
        raise typer.Exit(code=1)


def _write_run_record(snap_dir: Path, run: ScrapeRun) -> None:
    (snap_dir / RUN_RECORD).write_text(run.model_dump_json(indent=2))


if __name__ == "__main__":  # pragma: no cover
    app()
