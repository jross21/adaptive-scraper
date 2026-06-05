import pytest

from adaptive_scraper.models.schema import TARGET_REGISTRY, JobPosting
from adaptive_scraper.models.target import ExtractionTarget
from adaptive_scraper.validate.validator import validate

TARGET = TARGET_REGISTRY["job_postings"]


def row(**overrides):
    base = {
        "title": "Engineer",
        "company": "Acme",
        "location": "Remote",
        "url": "https://x.example/1",
    }
    base.update(overrides)
    return base


def test_all_valid_rows_pass():
    rows = [row(url="https://x.example/1"), row(title="Dev", url="https://x.example/2")]
    res = validate(rows, TARGET)
    assert res.ok
    assert res.valid_count == 2
    assert res.null_rates["company"] == 0.0
    assert len(res.valid_rows) == 2


def test_zero_valid_rows_fails_floor():
    rows = [row(url="not-a-url"), row(url="also-bad")]
    res = validate(rows, TARGET)
    assert not res.ok
    assert res.valid_count == 0
    assert any("floor" in e for e in res.errors)


def test_custom_min_rows_floor_not_met():
    target = ExtractionTarget(
        name="t",
        cardinality="many",
        item_schema=JobPosting,
        min_rows=3,
        required_fields=["title", "company", "url"],
    )
    rows = [row(url="https://x.example/1"), row(url="https://x.example/2")]
    res = validate(rows, target)
    assert not res.ok
    assert res.valid_count == 2
    assert any("floor" in e for e in res.errors)


def test_null_rate_flags_required_field():
    rows = [
        row(company=None, url="https://x.example/1"),
        row(company=None, url="https://x.example/2"),
        row(url="https://x.example/3"),
    ]
    res = validate(rows, TARGET)
    assert res.null_rates["company"] == pytest.approx(2 / 3)
    assert not res.ok
    assert any("company" in e for e in res.errors)


def test_invalid_row_excluded_but_run_still_ok():
    rows = [
        row(url="https://x.example/1"),
        row(url="bad-url"),
        row(url="https://x.example/3"),
    ]
    res = validate(rows, TARGET)
    assert res.valid_count == 2
    assert res.ok  # floor (>=1) met, required null-rates fine
    assert any("url" in e for e in res.errors)  # the bad row is still reported


def test_pydantic_error_message_is_descriptive():
    rows = [{"company": "Acme", "url": "https://x.example/1"}]  # missing required title
    res = validate(rows, TARGET)
    assert res.valid_count == 0
    assert any("title" in e for e in res.errors)


def test_cardinality_one_needs_at_least_one_valid_row():
    target = ExtractionTarget(
        name="single",
        cardinality="one",
        item_schema=JobPosting,
        required_fields=["title", "company", "url"],
    )
    res = validate([row()], target)
    assert res.ok
    assert res.valid_count == 1
