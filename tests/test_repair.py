"""Phase-2 self-heal loop: repair feedback + Sonnet->Opus escalation.

Driven entirely offline with a fake Claude client (returns scripted specs) and a stub
evaluator (stands in for interpret -> validate), so the loop's control flow is tested
without the network or the deterministic spine.
"""

from pathlib import Path
from types import SimpleNamespace

from adaptive_scraper.codegen.agent import generate_spec_with_repair
from adaptive_scraper.codegen.prompts import build_repair_message
from adaptive_scraper.compress.compressor import compress
from adaptive_scraper.models.schema import TARGET_REGISTRY
from adaptive_scraper.models.spec import ExtractionSpec, FieldRule

FIXTURE = Path(__file__).parent / "fixtures" / "jobs_listing.html"
TARGET = TARGET_REGISTRY["job_postings"]
SONNET = "claude-sonnet-4-6"
OPUS = "claude-opus-4-8"


def _spec(tag: str) -> ExtractionSpec:
    """A distinguishable spec (so tests can tell which attempt was returned)."""
    return ExtractionSpec(
        container_selector=tag,
        field_rules=[FieldRule(field="title", selector="h3", selector_type="css")],
        confidence=0.5,
    )


def _vr(ok: bool, *, valid_count: int = 0, errors=None, null_rates=None) -> SimpleNamespace:
    """A ValidationResult stand-in (duck-typed by the repair loop)."""
    return SimpleNamespace(
        ok=ok,
        valid_count=valid_count,
        errors=list(errors or []),
        null_rates=dict(null_rates or {}),
    )


class FakeMessages:
    def __init__(self, specs):
        self._specs = list(specs)
        self._i = 0
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        spec = self._specs[min(self._i, len(self._specs) - 1)]
        self._i += 1
        return SimpleNamespace(
            parsed_output=spec,
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class FakeClient:
    def __init__(self, specs):
        self.messages = FakeMessages(specs)


def _compressed():
    return compress(FIXTURE.read_text())


def test_first_attempt_success_makes_one_call():
    client = FakeClient([_spec("good")])
    result = generate_spec_with_repair(
        _compressed(),
        TARGET,
        evaluate=lambda s: _vr(True, valid_count=3),
        client=client,
        ladder=(SONNET, OPUS),
        max_attempts_per_model=2,
    )

    assert result.ok is True
    assert result.attempts == 1
    assert result.model == SONNET
    assert len(client.messages.calls) == 1
    assert result.tokens_in == 10 and result.tokens_out == 5


def test_repair_feeds_errors_back_then_succeeds():
    client = FakeClient([_spec("bad"), _spec("fixed")])
    n = {"count": 0}

    def evaluate(spec):
        n["count"] += 1
        if n["count"] == 1:
            return _vr(
                False,
                valid_count=0,
                errors=["row-count floor not met: 0 valid rows < required 1"],
                null_rates={"title": 1.0},
            )
        return _vr(True, valid_count=3)

    result = generate_spec_with_repair(
        _compressed(),
        TARGET,
        evaluate=evaluate,
        client=client,
        ladder=(SONNET, OPUS),
        max_attempts_per_model=2,
    )

    assert result.ok is True
    assert result.attempts == 2
    assert result.model == SONNET  # succeeded before escalating
    assert result.spec.container_selector == "fixed"
    assert result.tokens_in == 20 and result.tokens_out == 10  # accumulated

    # The second call must carry repair feedback containing the prior errors.
    second = client.messages.calls[1]["messages"][0]["content"]
    assert "PREVIOUS ATTEMPT FAILED" in second
    assert "row-count floor not met" in second


def test_escalates_to_opus_and_drops_temperature():
    client = FakeClient([_spec("nope")])

    result = generate_spec_with_repair(
        _compressed(),
        TARGET,
        evaluate=lambda s: _vr(False, valid_count=0, errors=["still bad"]),
        client=client,
        ladder=(SONNET, OPUS),
        max_attempts_per_model=2,
    )

    assert result.ok is False
    assert result.attempts == 4  # 2 on Sonnet, then 2 on Opus
    assert result.models_tried == [SONNET, OPUS]

    by_model = {}
    for call in client.messages.calls:
        by_model.setdefault(call["model"], []).append(call)
    # Sonnet 4.6 keeps temperature=0; Opus 4.8 must omit it (else HTTP 400).
    assert all(c.get("temperature") == 0 for c in by_model[SONNET])
    assert all("temperature" not in c for c in by_model[OPUS])


def test_exhaustion_returns_best_effort_without_raising():
    client = FakeClient([_spec("a"), _spec("b"), _spec("c"), _spec("d")])
    # valid_count: 1, 3, 2, 0 -> the 2nd attempt ("b") is the best-effort winner.
    counts = iter([1, 3, 2, 0])

    result = generate_spec_with_repair(
        _compressed(),
        TARGET,
        evaluate=lambda s: _vr(False, valid_count=next(counts), errors=["x"]),
        client=client,
        ladder=(SONNET, OPUS),
        max_attempts_per_model=2,
    )

    assert result.ok is False
    assert result.spec.container_selector == "b"  # most valid rows wins
    assert len(result.repair_log) == 4


def test_build_repair_message_includes_errors_and_previous_spec():
    previous = _spec("ul.does-not-exist li")
    validation = _vr(
        False,
        errors=["field 'title' is null/empty in 100% of rows"],
        null_rates={"title": 1.0, "company": 0.0},
    )
    msg = build_repair_message(_compressed(), TARGET, previous, validation)

    assert "PREVIOUS ATTEMPT FAILED" in msg
    assert "ul.does-not-exist li" in msg  # the previous spec is echoed back
    assert "field 'title' is null/empty" in msg
    assert "title: 100% null/empty" in msg
    assert "untrusted" in msg.lower()  # page-content framing preserved
