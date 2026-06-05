"""Page snapshots — the replay backbone.

A run stores the raw HTML + fetch metadata under runs/<run_id>/ so the whole pipeline
(and the tests) can run offline against a stored page. Replay re-runs a stored spec
against a stored snapshot, never re-hitting the site.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .fetch import FetchResult

PAGE_FILE = "page.html"
META_FILE = "meta.json"


class Snapshot(BaseModel):
    html: str
    meta: dict


def save_snapshot(run_id: str, result: FetchResult, *, runs_dir: Path) -> Path:
    directory = Path(runs_dir) / run_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / PAGE_FILE).write_text(result.html, encoding="utf-8")
    meta = {
        "url": result.url,
        "requested_url": result.requested_url,
        "status_code": result.status_code,
        "fetched_at": result.fetched_at,
        "user_agent": result.user_agent,
        "rendered": result.rendered,
    }
    (directory / META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return directory


def load_snapshot(path: Path | str) -> Snapshot:
    directory = Path(path)
    html = (directory / PAGE_FILE).read_text(encoding="utf-8")
    meta = json.loads((directory / META_FILE).read_text(encoding="utf-8"))
    return Snapshot(html=html, meta=meta)
