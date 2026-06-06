"""Page snapshots — the replay backbone.

A run stores the raw HTML + fetch metadata under runs/<run_id>/ so the whole pipeline
(and the tests) can run offline against a stored page. Replay re-runs a stored spec
against a stored snapshot, never re-hitting the site.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from .fetch import FetchResult

PAGE_FILE = "page.html"
META_FILE = "meta.json"
PAGES_DIR = "pages"  # crawled pages 2..N live here (page 1 stays at PAGE_FILE)
DETAIL_DIR = "detail"  # list→detail pages, named by a hash of their URL
MANIFEST_FILE = "manifest.json"  # ordered record of a crawl, for replay


class Snapshot(BaseModel):
    html: str
    meta: dict


def _meta(result: FetchResult) -> dict:
    return {
        "url": result.url,
        "requested_url": result.requested_url,
        "status_code": result.status_code,
        "fetched_at": result.fetched_at,
        "user_agent": result.user_agent,
        "rendered": result.rendered,
    }


def save_snapshot(run_id: str, result: FetchResult, *, runs_dir: Path) -> Path:
    directory = Path(runs_dir) / run_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / PAGE_FILE).write_text(result.html, encoding="utf-8")
    (directory / META_FILE).write_text(json.dumps(_meta(result), indent=2), encoding="utf-8")
    return directory


def save_crawl_page(run_dir: Path, index: int, result: FetchResult) -> Path:
    """Persist a paginated page (index >= 2) under pages/. Returns the HTML path."""
    pages = Path(run_dir) / PAGES_DIR
    pages.mkdir(parents=True, exist_ok=True)
    name = f"{index:03d}"
    html_path = pages / f"{name}.html"
    html_path.write_text(result.html, encoding="utf-8")
    (pages / f"{name}.meta.json").write_text(json.dumps(_meta(result), indent=2), encoding="utf-8")
    return html_path


def save_detail_page(run_dir: Path, url: str, result: FetchResult) -> Path:
    """Persist a detail page under detail/, named by a short hash of its URL."""
    detail = Path(run_dir) / DETAIL_DIR
    detail.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    html_path = detail / f"{name}.html"
    html_path.write_text(result.html, encoding="utf-8")
    (detail / f"{name}.meta.json").write_text(json.dumps(_meta(result), indent=2), encoding="utf-8")
    return html_path


def save_manifest(run_dir: Path, manifest: dict) -> Path:
    path = Path(run_dir) / MANIFEST_FILE
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def load_manifest(run_dir: Path) -> dict | None:
    """The crawl manifest, or None for a legacy single-page snapshot."""
    path = Path(run_dir) / MANIFEST_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_snapshot(path: Path | str) -> Snapshot:
    directory = Path(path)
    html = (directory / PAGE_FILE).read_text(encoding="utf-8")
    meta = json.loads((directory / META_FILE).read_text(encoding="utf-8"))
    return Snapshot(html=html, meta=meta)
