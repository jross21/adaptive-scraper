"""The generated extraction spec — what the codegen agent emits.

This is the *preferred* output form from the spec: a declarative field -> selector
+ transform map that the deterministic interpreter runs. Kept free of unsupported
JSON-schema constraints (no min/max, no recursion) so it works with the Anthropic
SDK's structured-outputs `messages.parse(output_format=ExtractionSpec)`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SelectorType = Literal["css", "xpath", "json_ld", "regex"]


class FieldRule(BaseModel):
    """One rule mapping a target field to a way of extracting it."""

    field: str
    selector: str
    selector_type: SelectorType
    attribute: str | None = None  # e.g. "href", "text", "content"; None -> text
    transform: str | None = None  # a whitelisted transform name (see execute.transforms)
    optional: bool = False


class ExtractionSpec(BaseModel):
    """A full extraction recipe for one page template against one target."""

    container_selector: str | None = None  # repeating element for cardinality "many"
    # How to interpret container_selector: a css/xpath selecting repeating DOM elements,
    # or "json_ld" meaning container_selector is a schema.org @type to iterate over.
    container_selector_type: SelectorType = "css"
    field_rules: list[FieldRule]
    confidence: float = 0.0  # model's self-reported confidence, 0..1 (validated in-app)
    notes: str | None = None  # reasoning, for debugging
