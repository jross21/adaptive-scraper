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

from .codegen.agent import DEFAULT_MODEL
from .config import RUNS_DIR
from .models.run import ScrapeRun
from .models.schema import TARGET_REGISTRY
from .pipeline import run_pipeline
from .recon.fetch import fetch
from .recon.render import looks_js_rendered, render_page
from .recon.snapshot import load_snapshot, save_snapshot

app = typer.Typer(add_completion=False, help="Adaptive self-writing scraper (Phase 1).")

RUN_RECORD = "run.json"


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
) -> None:
    if target not in TARGET_REGISTRY:
        typer.echo(f"unknown target {target!r}; known: {', '.join(TARGET_REGISTRY)}", err=True)
        raise typer.Exit(code=2)
    target_obj = TARGET_REGISTRY[target]

    if from_snapshot is not None:
        snap = load_snapshot(from_snapshot)
        outcome = run_pipeline(
            html=snap.html,
            base_url=snap.meta.get("url"),
            target=target_obj,
            target_name=target,
            run_id=Path(from_snapshot).name,
            model=model,
            spec=_stored_spec(Path(from_snapshot)),
            snapshot_uri=str(from_snapshot),
        )
    else:
        if not url:
            typer.echo("provide a URL, or use --from-snapshot", err=True)
            raise typer.Exit(code=2)
        run_id = _new_run_id()
        result = render_page(url) if render else fetch(url)
        snap_dir = save_snapshot(run_id, result, runs_dir=runs_dir)
        outcome = run_pipeline(
            html=result.html,
            base_url=result.url,
            target=target_obj,
            target_name=target,
            run_id=run_id,
            model=model,
            snapshot_uri=str(snap_dir),
            rendered=result.rendered,
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
                html=result.html,
                base_url=result.url,
                target=target_obj,
                target_name=target,
                run_id=run_id,
                model=model,
                snapshot_uri=str(snap_dir),
                rendered=True,
            )
        _write_run_record(snap_dir, outcome.run)

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
