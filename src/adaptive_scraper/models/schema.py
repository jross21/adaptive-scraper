"""Target schemas (the contract) and the registry the CLI resolves against.

JobPosting is the Phase-1 example item schema from the architecture spec. Add more
item schemas + registry entries here as new targets are introduced; the rest of the
pipeline is schema-agnostic.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, HttpUrl

from .target import ExtractionTarget


class JobPosting(BaseModel):
    title: str
    company: str
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    url: HttpUrl
    posted_at: datetime | None = None


TARGET_REGISTRY: dict[str, ExtractionTarget] = {
    "job_postings": ExtractionTarget(
        name="job_postings",
        cardinality="many",
        item_schema=JobPosting,
        min_rows=1,
        required_fields=["title", "company", "url"],
    ),
}
