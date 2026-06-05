import json

import pytest
from pydantic import ValidationError

from adaptive_scraper.models.schema import TARGET_REGISTRY, JobPosting
from adaptive_scraper.models.spec import ExtractionSpec, FieldRule


def test_extraction_spec_round_trips():
    spec = ExtractionSpec(
        container_selector="ul.jobs li",
        field_rules=[
            FieldRule(field="title", selector="h2", selector_type="css", transform="trim"),
            FieldRule(
                field="url",
                selector="a",
                selector_type="css",
                attribute="href",
                transform="abs_url",
            ),
        ],
        confidence=0.8,
        notes="example",
    )
    restored = ExtractionSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec
    assert restored.field_rules[1].attribute == "href"


def test_extraction_spec_schema_has_no_unsupported_constraints():
    # Structured outputs reject numeric/string constraints; keep the spec schema clean.
    schema_text = json.dumps(ExtractionSpec.model_json_schema())
    for forbidden in ("minLength", "maxLength", "minimum", "maximum", "multipleOf"):
        assert forbidden not in schema_text


def test_field_rule_rejects_unknown_selector_type():
    with pytest.raises(ValidationError):
        FieldRule(field="title", selector="h2", selector_type="bogus")


def test_target_registry_resolves_job_postings():
    target = TARGET_REGISTRY["job_postings"]
    assert target.cardinality == "many"
    assert target.item_schema is JobPosting
    assert "title" in target.required_fields


def test_job_posting_validates_minimal_row():
    job = JobPosting(title="Engineer", company="Acme", url="https://acme.example/jobs/1")
    assert job.location is None
    assert str(job.url).startswith("https://acme.example")


def test_job_posting_rejects_non_url():
    with pytest.raises(ValidationError):
        JobPosting(title="Engineer", company="Acme", url="not-a-url")
