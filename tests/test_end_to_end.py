"""End-to-end proof of the deterministic spine — no network, no API.

Drives the pipeline with the committed fixture HTML + a known-good ExtractionSpec
(skipping codegen) through interpret -> validate, and exercises the CLI replay path.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from adaptive_scraper.cli import app
from adaptive_scraper.models.schema import TARGET_REGISTRY
from adaptive_scraper.models.spec import ExtractionSpec
from adaptive_scraper.pipeline import run_pipeline

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://careers.acme.example/careers"


def _fixture_spec() -> ExtractionSpec:
    return ExtractionSpec.model_validate_json((FIXTURES / "jobs_spec.json").read_text())


def test_pipeline_with_known_spec_yields_valid_rows():
    html = (FIXTURES / "jobs_listing.html").read_text()
    target = TARGET_REGISTRY["job_postings"]

    outcome = run_pipeline(
        html=html,
        base_url=BASE_URL,
        target=target,
        target_name="job_postings",
        run_id="test-run",
        spec=_fixture_spec(),
    )

    assert outcome.validation.ok
    assert outcome.validation.valid_count == 3
    assert outcome.run.final_status == "ok"
    titles = {r["title"] for r in outcome.validation.valid_rows}
    assert titles == {"Senior Backend Engineer", "Frontend Engineer", "Data Scientist"}
    # url got resolved to absolute by the abs_url transform.
    assert outcome.validation.valid_rows[0]["url"].startswith("https://careers.acme.example/jobs/")


def test_cli_replay_from_snapshot(tmp_path):
    # Seed a snapshot directory the way a live run would: page.html + meta.json + run.json.
    run_dir = tmp_path / "seeded-run"
    run_dir.mkdir()
    (run_dir / "page.html").write_text((FIXTURES / "jobs_listing.html").read_text())
    (run_dir / "meta.json").write_text(json.dumps({"url": BASE_URL, "status_code": 200}))
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "seeded-run",
                "url": BASE_URL,
                "target_name": "job_postings",
                "model": "stored",
                "spec": _fixture_spec().model_dump(),
            }
        )
    )

    result = CliRunner().invoke(app, ["--from-snapshot", str(run_dir), "--target", "job_postings"])

    assert result.exit_code == 0, result.output
    assert "Senior Backend Engineer" in result.output


def test_cli_replay_exits_nonzero_when_validation_fails(tmp_path):
    # A spec that extracts nothing should make the CLI exit non-zero.
    run_dir = tmp_path / "bad-run"
    run_dir.mkdir()
    (run_dir / "page.html").write_text((FIXTURES / "jobs_listing.html").read_text())
    (run_dir / "meta.json").write_text(json.dumps({"url": BASE_URL, "status_code": 200}))
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "bad-run",
                "url": BASE_URL,
                "target_name": "job_postings",
                "model": "stored",
                "spec": {
                    "container_selector": "ul.does-not-exist li",
                    "container_selector_type": "css",
                    "field_rules": [
                        {"field": "title", "selector": "h3", "selector_type": "css"}
                    ],
                    "confidence": 0.1,
                },
            }
        )
    )

    result = CliRunner().invoke(app, ["--from-snapshot", str(run_dir), "--target", "job_postings"])
    assert result.exit_code == 1


def _crawl_page(titles, *, next_url=None):
    cards = "".join(
        f'<li class="job-card"><h3 class="job-title">{t}</h3>'
        f'<span class="company">Acme</span>'
        f'<a class="job-link" href="/jobs/{t}">apply</a></li>'
        for t in titles
    )
    nxt = f'<a class="next" href="{next_url}">Next</a>' if next_url else ""
    return f"<html><body><ul class='jobs'>{cards}</ul>{nxt}</body></html>"


def test_cli_replay_multipage_rewalks_offline(tmp_path):
    # Seed a run dir as a crawl would: page.html + pages/ + manifest.json + a paginating spec.
    base = "https://board.example"
    run_dir = tmp_path / "crawl-run"
    (run_dir / "pages").mkdir(parents=True)
    (run_dir / "page.html").write_text(_crawl_page(["A1", "A2", "A3"], next_url=f"{base}/p2"))
    (run_dir / "pages" / "002.html").write_text(_crawl_page(["B1", "B2", "B3"], next_url=f"{base}/p3"))
    (run_dir / "pages" / "003.html").write_text(_crawl_page(["C1", "C2", "C3"]))
    (run_dir / "meta.json").write_text(json.dumps({"url": f"{base}/p1", "status_code": 200}))
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pages": [
                    {"url": f"{base}/p1", "file": "page.html"},
                    {"url": f"{base}/p2", "file": "pages/002.html"},
                    {"url": f"{base}/p3", "file": "pages/003.html"},
                ],
                "detail": [],
            }
        )
    )
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "crawl-run",
                "url": f"{base}/p1",
                "target_name": "job_postings",
                "model": "stored",
                "spec": {
                    "container_selector": "ul.jobs li.job-card",
                    "container_selector_type": "css",
                    "field_rules": [
                        {"field": "title", "selector": "h3.job-title", "selector_type": "css"},
                        {"field": "company", "selector": "span.company", "selector_type": "css"},
                        {"field": "url", "selector": "a.job-link", "selector_type": "css", "attribute": "href", "transform": "abs_url"},
                    ],
                    "next_page_selector": "a.next",
                    "next_page_attribute": "href",
                    "confidence": 0.95,
                },
            }
        )
    )

    result = CliRunner().invoke(app, ["--from-snapshot", str(run_dir), "--target", "job_postings"])

    assert result.exit_code == 0, result.output
    # Rows from all three stored pages were re-walked offline.
    assert "A1" in result.output and "B1" in result.output and "C3" in result.output
