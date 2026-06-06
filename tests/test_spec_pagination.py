"""Pagination + list→detail fields on ExtractionSpec.

These are optional, structured-outputs-safe additions: absent => single-page behavior, so
every existing spec (incl. the committed jobs_spec.json) and all existing tests still load.
"""

from pathlib import Path

from adaptive_scraper.models.spec import ExtractionSpec, FieldRule

FIXTURES = Path(__file__).parent / "fixtures"


def test_defaults_are_single_page():
    spec = ExtractionSpec(
        field_rules=[FieldRule(field="title", selector="h3", selector_type="css")],
    )
    assert spec.next_page_selector is None
    assert spec.next_page_selector_type == "css"
    assert spec.next_page_attribute == "href"
    assert spec.detail_url_field is None
    assert spec.detail_field_rules == []


def test_round_trip_with_pagination_and_detail():
    spec = ExtractionSpec(
        container_selector="ul.jobs li.job-card",
        field_rules=[
            FieldRule(field="title", selector="h3", selector_type="css"),
            FieldRule(field="url", selector="a", selector_type="css", attribute="href", transform="abs_url"),
        ],
        next_page_selector="a.next",
        next_page_selector_type="css",
        next_page_attribute="href",
        detail_url_field="url",
        detail_field_rules=[
            FieldRule(field="description", selector="div.description", selector_type="css"),
        ],
        confidence=0.9,
    )
    restored = ExtractionSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec
    assert restored.next_page_selector == "a.next"
    assert restored.detail_url_field == "url"
    assert restored.detail_field_rules[0].field == "description"


def test_existing_fixture_spec_still_loads():
    # Back-compat: the committed known-good spec (no pagination/detail) must still deserialize.
    spec = ExtractionSpec.model_validate_json((FIXTURES / "jobs_spec.json").read_text())
    assert spec.next_page_selector is None
    assert spec.detail_field_rules == []
    assert spec.field_rules  # sanity: it still has its field rules
