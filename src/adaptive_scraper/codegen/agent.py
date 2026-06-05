"""The codegen agent — calls Claude to produce an ExtractionSpec.

Uses structured outputs (`messages.parse(output_format=ExtractionSpec)`) so the model is
forced to return a schema-valid spec and retries on mismatch at the API layer — this is
the spec's "emit only valid JSON, reject and retry on parse failure" requirement.

`generate_spec` is the single-pass primitive (the spec's "Sonnet-class first pass").
`generate_spec_with_repair` is the Phase-2 self-heal loop: it feeds validation failures
back to the model and, after exhausting attempts on one model, escalates to the next in
the ladder (Sonnet 4.6 → Opus 4.8). The "definition of success" (interpret → validate) is
injected as a callback so this module stays decoupled from the execute/validate layers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anthropic

from ..compress.compressor import CompressedDOM
from ..config import ESCALATION_MODELS, MAX_REPAIR_ATTEMPTS
from ..models.spec import ExtractionSpec
from ..models.target import ExtractionTarget
from .prompts import SYSTEM_PROMPT, build_repair_message, build_user_message

if TYPE_CHECKING:
    # Type-only import: the repair loop duck-types the evaluator's result, so codegen
    # never imports the validate layer at runtime.
    from ..validate.validator import ValidationResult

DEFAULT_MODEL = ESCALATION_MODELS[0]
MAX_TOKENS = 4096


class CodegenError(RuntimeError):
    """Raised when the model fails to return a usable ExtractionSpec."""


@dataclass
class CodegenResult:
    spec: ExtractionSpec
    tokens_in: int
    tokens_out: int
    model: str


@dataclass
class RepairResult:
    """Outcome of the self-heal loop: the chosen spec plus what it took to get there."""

    spec: ExtractionSpec
    model: str  # the model that produced `spec`
    ok: bool  # whether `spec` passed validation
    tokens_in: int
    tokens_out: int
    attempts: int
    models_tried: list[str] = field(default_factory=list)
    repair_log: list[str] = field(default_factory=list)


def _request_kwargs(model: str) -> dict:
    """Per-model request params. Opus 4.7/4.8 removed `temperature`/`top_p`/`top_k` and
    return HTTP 400 if any is sent, so omit them. Sonnet 4.6 still accepts `temperature=0`,
    which aids determinism on the first pass."""
    if model.startswith("claude-opus-4-7") or model.startswith("claude-opus-4-8"):
        return {}
    return {"temperature": 0}


def generate_spec(
    compressed: CompressedDOM,
    target: ExtractionTarget,
    *,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    user_message: str | None = None,
) -> CodegenResult:
    """One codegen pass. Pass `user_message` to supply repair feedback; otherwise the
    standard task message is built from the target + compressed DOM."""
    client = client or anthropic.Anthropic()
    content = (
        user_message if user_message is not None else build_user_message(compressed, target)
    )
    response = client.messages.parse(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_format=ExtractionSpec,
        **_request_kwargs(model),
    )
    spec = response.parsed_output
    if spec is None:
        raise CodegenError("model did not return a valid ExtractionSpec")
    usage = response.usage
    return CodegenResult(
        spec=spec,
        tokens_in=usage.input_tokens,
        tokens_out=usage.output_tokens,
        model=model,
    )


def _is_better(candidate: ValidationResult, incumbent: ValidationResult) -> bool:
    """Best-effort ranking when no attempt fully validates: more valid rows wins, then
    fewer errors."""
    if candidate.valid_count != incumbent.valid_count:
        return candidate.valid_count > incumbent.valid_count
    return len(candidate.errors) < len(incumbent.errors)


def generate_spec_with_repair(
    compressed: CompressedDOM,
    target: ExtractionTarget,
    *,
    evaluate: Callable[[ExtractionSpec], "ValidationResult"],
    client: anthropic.Anthropic | None = None,
    ladder: tuple[str, ...] = ESCALATION_MODELS,
    max_attempts_per_model: int = MAX_REPAIR_ATTEMPTS,
) -> RepairResult:
    """Generate a spec, validate it via `evaluate`, and on failure feed the errors back to
    the model and retry. Exhaust `max_attempts_per_model` on each model in `ladder` before
    escalating to the next. Returns the first validating spec, or — if none validates — the
    best-effort attempt with `ok=False` (never raises on validation failure, so the caller
    can still write a run record and exit non-zero, matching Phase-1 behavior)."""
    if not ladder:
        raise CodegenError("escalation ladder is empty")
    client = client or anthropic.Anthropic()

    tokens_in = tokens_out = 0
    attempts = 0
    models_tried: list[str] = []
    repair_log: list[str] = []
    best: tuple[ExtractionSpec, ValidationResult, str] | None = None
    last_spec: ExtractionSpec | None = None
    last_validation: ValidationResult | None = None

    for model in ladder:
        models_tried.append(model)
        for attempt in range(1, max_attempts_per_model + 1):
            attempts += 1
            if last_spec is None or last_validation is None:
                message = build_user_message(compressed, target)
            else:
                message = build_repair_message(compressed, target, last_spec, last_validation)

            result = generate_spec(
                compressed, target, client=client, model=model, user_message=message
            )
            tokens_in += result.tokens_in
            tokens_out += result.tokens_out

            validation = evaluate(result.spec)
            repair_log.append(
                f"{model} attempt {attempt}: "
                + (
                    "ok"
                    if validation.ok
                    else f"{len(validation.errors)} error(s), {validation.valid_count} valid rows"
                )
            )

            if validation.ok:
                return RepairResult(
                    spec=result.spec,
                    model=model,
                    ok=True,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    attempts=attempts,
                    models_tried=models_tried,
                    repair_log=repair_log,
                )

            if best is None or _is_better(validation, best[1]):
                best = (result.spec, validation, model)
            last_spec, last_validation = result.spec, validation

    assert best is not None  # ladder is non-empty, so at least one attempt ran
    spec, _validation, model = best
    return RepairResult(
        spec=spec,
        model=model,
        ok=False,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        attempts=attempts,
        models_tried=models_tried,
        repair_log=repair_log,
    )
