"""Pipeline orchestration — the seam that wires the modules together.

Kept separate from the CLI so the whole loop is testable offline: pass a `spec` to
skip codegen (deterministic spine), or omit it to generate one via the LLM. Codegen runs
through the Phase-2 self-heal loop (`generate_spec_with_repair`); this module supplies the
"definition of success" — interpret → validate — as the evaluator callback.
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic

from .codegen.agent import DEFAULT_MODEL, generate_spec_with_repair
from .compress.compressor import compress
from .config import ESCALATION_MODELS, TOKEN_BUDGET
from .execute.interpreter import interpret
from .models.run import ScrapeRun
from .models.spec import ExtractionSpec
from .models.target import ExtractionTarget
from .validate.validator import ValidationResult, validate


@dataclass
class PipelineOutcome:
    run: ScrapeRun
    validation: ValidationResult
    rows: list[dict]


def _ladder_from(model: str) -> tuple[str, ...]:
    """The escalation ladder starting at `model`: if it sits on the standard ladder,
    escalate upward from there; otherwise run that single model with no escalation."""
    if model in ESCALATION_MODELS:
        return ESCALATION_MODELS[ESCALATION_MODELS.index(model) :]
    return (model,)


def run_pipeline(
    *,
    html: str,
    base_url: str | None,
    target: ExtractionTarget,
    target_name: str,
    run_id: str,
    model: str = DEFAULT_MODEL,
    spec: ExtractionSpec | None = None,
    client: anthropic.Anthropic | None = None,
    snapshot_uri: str | None = None,
    rendered: bool = False,
) -> PipelineOutcome:
    tokens_in = tokens_out = 0
    attempts = 1
    models_tried: list[str] = []
    repair_log: list[str] = []

    if spec is None:
        compressed = compress(html, token_budget=TOKEN_BUDGET)

        def _evaluate(candidate: ExtractionSpec) -> ValidationResult:
            rows = interpret(
                candidate, html, base_url=base_url, cardinality=target.cardinality
            )
            return validate(rows, target)

        repair = generate_spec_with_repair(
            compressed,
            target,
            evaluate=_evaluate,
            client=client,
            ladder=_ladder_from(model),
        )
        spec = repair.spec
        model = repair.model
        tokens_in, tokens_out = repair.tokens_in, repair.tokens_out
        attempts = repair.attempts
        models_tried = repair.models_tried
        repair_log = repair.repair_log

    rows = interpret(spec, html, base_url=base_url, cardinality=target.cardinality)
    validation = validate(rows, target)

    run = ScrapeRun(
        run_id=run_id,
        url=base_url or "",
        target_name=target_name,
        model=model,
        snapshot_uri=snapshot_uri,
        rendered=rendered,
        spec=spec,
        rows_extracted=validation.valid_count,
        final_status="ok" if validation.ok else "failed",
        validation_errors=validation.errors,
        attempts=attempts,
        models_tried=models_tried,
        repair_log=repair_log,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    return PipelineOutcome(run=run, validation=validation, rows=rows)
