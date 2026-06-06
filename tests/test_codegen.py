from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_scraper.codegen.agent import CodegenError, DEFAULT_MODEL, generate_spec
from adaptive_scraper.codegen.prompts import SYSTEM_PROMPT, build_user_message
from adaptive_scraper.compress.compressor import compress
from adaptive_scraper.execute.transforms import TRANSFORM_NAMES
from adaptive_scraper.models.schema import TARGET_REGISTRY
from adaptive_scraper.models.spec import ExtractionSpec, FieldRule

FIXTURE = Path(__file__).parent / "fixtures" / "jobs_listing.html"
TARGET = TARGET_REGISTRY["job_postings"]


class FakeMessages:
    def __init__(self, result):
        self._result = result
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


class FakeClient:
    def __init__(self, result):
        self.messages = FakeMessages(result)


def _fake_response(spec: ExtractionSpec | None):
    return SimpleNamespace(
        parsed_output=spec,
        usage=SimpleNamespace(input_tokens=123, output_tokens=45),
    )


def test_system_prompt_lists_only_whitelisted_transforms():
    for name in TRANSFORM_NAMES:
        assert name in SYSTEM_PROMPT
    # The four selector types and the injection framing must be present.
    for kind in ("css", "xpath", "json_ld", "regex"):
        assert kind in SYSTEM_PROMPT
    assert "untrusted" in SYSTEM_PROMPT.lower()


def test_user_message_frames_page_content_as_data_and_includes_schema():
    cd = compress(FIXTURE.read_text())
    msg = build_user_message(cd, TARGET)
    assert "untrusted" in msg.lower()
    assert "Senior Backend Engineer" in msg  # the page content is embedded
    assert "JobPosting" in msg or "job_postings" in msg  # the target/schema is present
    assert "cardinality" in msg.lower()


def test_generate_spec_returns_spec_and_usage_from_client():
    spec = ExtractionSpec(
        container_selector="ul.jobs li.job-card",
        field_rules=[FieldRule(field="title", selector="h3", selector_type="css")],
        confidence=0.9,
    )
    client = FakeClient(_fake_response(spec))
    cd = compress(FIXTURE.read_text())

    result = generate_spec(cd, TARGET, client=client)

    assert result.spec == spec
    assert result.tokens_in == 123
    assert result.tokens_out == 45
    assert result.model == DEFAULT_MODEL
    # The call must use structured outputs + deterministic settings.
    call = client.messages.calls[0]
    assert call["output_format"] is ExtractionSpec
    assert call["temperature"] == 0
    assert call["model"] == DEFAULT_MODEL


def test_generate_spec_raises_when_model_returns_no_spec():
    client = FakeClient(_fake_response(None))
    cd = compress(FIXTURE.read_text())
    with pytest.raises(CodegenError):
        generate_spec(cd, TARGET, client=client)


def test_generate_spec_omits_temperature_for_opus():
    """Opus 4.7/4.8 reject `temperature` (HTTP 400); Sonnet keeps temperature=0.
    This guards anyone overriding SCRAPER_CODEGEN_MODEL to an Opus id."""
    spec = ExtractionSpec(
        field_rules=[FieldRule(field="title", selector="h3", selector_type="css")],
        confidence=0.9,
    )
    cd = compress(FIXTURE.read_text())

    sonnet_client = FakeClient(_fake_response(spec))
    generate_spec(cd, TARGET, client=sonnet_client, model="claude-sonnet-4-6")
    assert sonnet_client.messages.calls[0]["temperature"] == 0

    opus_client = FakeClient(_fake_response(spec))
    generate_spec(cd, TARGET, client=opus_client, model="claude-opus-4-8")
    assert "temperature" not in opus_client.messages.calls[0]
