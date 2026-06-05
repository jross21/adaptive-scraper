"""Structural validation against the target schema (Phase-1 subset of spec §6).

Three graduated checks:
  1. Pydantic per-row validation against the item schema (invalid rows are excluded
     and reported, not fatal on their own).
  2. Row-count floor: enough valid rows (>= min_rows for "many", >= 1 for "one").
  3. Per-field null-rate: a required field that is null/empty in too large a fraction
     of rows signals a bad selector.

Deferred to later phases: type-coercion success rate, sanity heuristics (salary_min
<= max, dates plausible), and cross-run drift (needs the cache baseline).

The error strings are intentionally descriptive — they are the feedback the Phase-2
repair loop will hand back to the codegen agent.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from ..models.target import ExtractionTarget

DEFAULT_NULL_RATE_THRESHOLD = 0.5


class ValidationResult(BaseModel):
    ok: bool
    valid_rows: list[dict]
    valid_count: int
    errors: list[str]
    null_rates: dict[str, float]


def _is_null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _null_rates(rows: list[dict], fields: list[str]) -> dict[str, float]:
    if not rows:
        return {f: 1.0 for f in fields}
    return {
        f: sum(1 for r in rows if _is_null(r.get(f))) / len(rows) for f in fields
    }


def validate(
    rows: list[dict],
    target: ExtractionTarget,
    *,
    null_rate_threshold: float = DEFAULT_NULL_RATE_THRESHOLD,
) -> ValidationResult:
    item_schema: type[BaseModel] = target.item_schema
    errors: list[str] = []

    # 1. Pydantic per-row validation.
    valid_rows: list[dict] = []
    for i, raw in enumerate(rows):
        try:
            model = item_schema.model_validate(raw)
            valid_rows.append(model.model_dump(mode="json"))
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"]) or "<row>"
                errors.append(f"row {i}: field '{loc}' {err['msg']}")

    # 2. Row-count floor.
    needed = target.min_rows if target.cardinality == "many" else 1
    floor_ok = len(valid_rows) >= needed
    if not floor_ok:
        errors.append(
            f"row-count floor not met: {len(valid_rows)} valid rows "
            f"< required {needed} (cardinality={target.cardinality})"
        )

    # 3. Per-field null-rate over all item-schema fields; gate on required fields.
    all_fields = list(item_schema.model_fields)
    null_rates = _null_rates(rows, all_fields)
    null_ok = True
    for field in target.required_fields:
        rate = null_rates.get(field, 1.0)
        if rate > null_rate_threshold:
            null_ok = False
            errors.append(
                f"field '{field}' is null/empty in {rate:.0%} of rows "
                f"(> {null_rate_threshold:.0%} threshold) — likely a bad selector"
            )

    return ValidationResult(
        ok=floor_ok and null_ok,
        valid_rows=valid_rows,
        valid_count=len(valid_rows),
        errors=errors,
        null_rates=null_rates,
    )
