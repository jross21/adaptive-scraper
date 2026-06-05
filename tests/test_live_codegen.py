"""Live end-to-end proof: real Sonnet 4.6 codegen produces a working spec.

Gated by the `live` marker (deselected by default in pyproject) and an API key.
Run with: uv run pytest -m live
"""

import os
from pathlib import Path

import pytest

from adaptive_scraper.codegen.agent import generate_spec
from adaptive_scraper.compress.compressor import compress
from adaptive_scraper.execute.interpreter import interpret
from adaptive_scraper.models.schema import TARGET_REGISTRY
from adaptive_scraper.validate.validator import validate

pytestmark = pytest.mark.live

FIXTURE = Path(__file__).parent / "fixtures" / "jobs_listing.html"
BASE_URL = "https://careers.acme.example/careers"


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
def test_real_codegen_produces_validating_spec():
    html = FIXTURE.read_text()
    target = TARGET_REGISTRY["job_postings"]

    compressed = compress(html)
    result = generate_spec(compressed, target)
    rows = interpret(result.spec, html, base_url=BASE_URL, cardinality=target.cardinality)
    validation = validate(rows, target)

    assert validation.ok, validation.errors
    assert validation.valid_count >= 1
    # Sanity: the model found the actual jobs, not noise.
    titles = {r["title"] for r in validation.valid_rows}
    assert "Senior Backend Engineer" in titles
