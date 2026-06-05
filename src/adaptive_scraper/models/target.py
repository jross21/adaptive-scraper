"""The extraction target — the contract a job is judged against.

`item_schema` is a Pydantic model class (e.g. JobPosting); validation and the
JSON Schema sent to the codegen agent both derive from it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtractionTarget(BaseModel):
    # item_schema holds a Pydantic *class*, not an instance, so allow arbitrary types.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    cardinality: Literal["one", "many"]
    item_schema: type[BaseModel]
    min_rows: int = 1  # validation floor for "many"
    required_fields: list[str] = Field(default_factory=list)  # fields that may not be null
