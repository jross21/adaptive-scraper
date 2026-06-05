from pathlib import Path

import pytest

from adaptive_scraper.execute.interpreter import interpret
from adaptive_scraper.execute.transforms import UnknownTransformError
from adaptive_scraper.models.spec import ExtractionSpec, FieldRule

FIXTURE = Path(__file__).parent / "fixtures" / "jobs_listing.html"
BASE_URL = "https://careers.acme.example/careers"


def html() -> str:
    return FIXTURE.read_text()


def test_css_many_extracts_all_cards():
    spec = ExtractionSpec(
        container_selector="ul.jobs li.job-card",
        container_selector_type="css",
        field_rules=[
            FieldRule(field="title", selector="h3.job-title", selector_type="css", transform="trim"),
            FieldRule(field="company", selector="span.company", selector_type="css", transform="trim"),
            FieldRule(field="location", selector="span.location", selector_type="css", transform="trim"),
        ],
    )
    rows = interpret(spec, html(), base_url=BASE_URL, cardinality="many")
    assert len(rows) == 3
    assert rows[0]["title"] == "Senior Backend Engineer"
    assert rows[2]["title"] == "Data Scientist"
    assert {r["company"] for r in rows} == {"Acme Corp"}


def test_css_attribute_with_abs_url_transform():
    spec = ExtractionSpec(
        container_selector="ul.jobs li.job-card",
        field_rules=[
            FieldRule(
                field="url",
                selector="a.job-link",
                selector_type="css",
                attribute="href",
                transform="abs_url",
            ),
        ],
    )
    rows = interpret(spec, html(), base_url=BASE_URL, cardinality="many")
    assert rows[0]["url"] == "https://careers.acme.example/jobs/senior-backend-engineer"


def test_xpath_field_relative_to_container():
    spec = ExtractionSpec(
        container_selector="ul.jobs li.job-card",
        field_rules=[
            FieldRule(field="title", selector=".//h3", selector_type="xpath", transform="trim"),
        ],
    )
    rows = interpret(spec, html(), base_url=BASE_URL, cardinality="many")
    assert rows[1]["title"] == "Frontend Engineer"


def test_regex_field_over_container_html():
    spec = ExtractionSpec(
        container_selector="ul.jobs li.job-card",
        field_rules=[
            FieldRule(field="title", selector="h3.job-title", selector_type="css", transform="trim"),
            FieldRule(field="req_id", selector=r"Req #(\d+)", selector_type="regex"),
        ],
    )
    rows = interpret(spec, html(), base_url=BASE_URL, cardinality="many")
    assert [r["req_id"] for r in rows] == ["4521", "4522", "4530"]


def test_json_ld_many_iterates_typed_objects():
    spec = ExtractionSpec(
        container_selector="JobPosting",
        container_selector_type="json_ld",
        field_rules=[
            FieldRule(field="title", selector="title", selector_type="json_ld"),
            FieldRule(field="company", selector="hiringOrganization.name", selector_type="json_ld"),
            FieldRule(
                field="location",
                selector="jobLocation.address.addressLocality",
                selector_type="json_ld",
            ),
            FieldRule(field="url", selector="url", selector_type="json_ld"),
        ],
    )
    rows = interpret(spec, html(), base_url=BASE_URL, cardinality="many")
    assert len(rows) == 3
    assert rows[0]["company"] == "Acme Corp"
    assert rows[1]["location"] == "New York, NY"
    assert rows[2]["url"] == "https://careers.acme.example/jobs/data-scientist"


def test_json_ld_one_resolves_first_object():
    spec = ExtractionSpec(
        container_selector=None,
        field_rules=[
            FieldRule(field="title", selector="title", selector_type="json_ld"),
            FieldRule(field="company", selector="hiringOrganization.name", selector_type="json_ld"),
        ],
    )
    rows = interpret(spec, html(), base_url=BASE_URL, cardinality="one")
    assert len(rows) == 1
    assert rows[0]["title"] == "Senior Backend Engineer"
    assert rows[0]["company"] == "Acme Corp"


def test_missing_optional_field_is_none():
    spec = ExtractionSpec(
        container_selector="ul.jobs li.job-card",
        field_rules=[
            FieldRule(field="title", selector="h3.job-title", selector_type="css"),
            FieldRule(
                field="department",
                selector="span.department",
                selector_type="css",
                optional=True,
            ),
        ],
    )
    rows = interpret(spec, html(), base_url=BASE_URL, cardinality="many")
    assert rows[0]["department"] is None


def test_data_parse_failure_yields_none_not_crash():
    # 'int' over non-numeric location text should be swallowed -> None, never crash.
    spec = ExtractionSpec(
        container_selector="ul.jobs li.job-card",
        field_rules=[
            FieldRule(field="salary_min", selector="span.location", selector_type="css", transform="int"),
        ],
    )
    rows = interpret(spec, html(), base_url=BASE_URL, cardinality="many")
    assert rows[0]["salary_min"] is None


def test_unknown_transform_fails_loud():
    # A non-whitelisted transform is a bad spec, not bad data -> it must raise.
    spec = ExtractionSpec(
        container_selector="ul.jobs li.job-card",
        field_rules=[
            FieldRule(field="title", selector="h3.job-title", selector_type="css", transform="os_system"),
        ],
    )
    with pytest.raises(UnknownTransformError):
        interpret(spec, html(), base_url=BASE_URL, cardinality="many")
