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
