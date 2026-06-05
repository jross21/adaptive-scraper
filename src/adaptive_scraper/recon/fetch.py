"""Polite static fetch via httpx.

Builds the etiquette layer from day one (spec §10): a descriptive User-Agent and a
robots.txt allow-check before fetching. The full rate limiter / concurrency caps /
proxy strategy are Phase 4; Phase 1 fetches a single URL once and snapshots it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel

from ..config import REQUEST_TIMEOUT, USER_AGENT


class RobotsDisallowedError(RuntimeError):
    """Raised when robots.txt disallows fetching the requested URL."""


class FetchResult(BaseModel):
    url: str  # final URL after redirects
    requested_url: str
    status_code: int
    html: str
    fetched_at: str  # ISO 8601
    user_agent: str
    rendered: bool = False  # True when the HTML came from a headless browser (Phase 2)


def _robots_allows(client: httpx.Client, url: str, user_agent: str) -> bool:
    robots_url = urljoin(url, "/robots.txt")
    try:
        resp = client.get(robots_url)
    except httpx.HTTPError:
        return True  # robots unreachable -> permitted (standard behavior)
    if resp.status_code >= 400:
        return True  # no robots.txt -> permitted
    parser = RobotFileParser()
    parser.parse(resp.text.splitlines())
    return parser.can_fetch(user_agent, url)


def fetch(
    url: str,
    *,
    client: httpx.Client | None = None,
    user_agent: str = USER_AGENT,
    respect_robots: bool = True,
    timeout: float = REQUEST_TIMEOUT,
) -> FetchResult:
    own_client = client is None
    client = client or httpx.Client(
        headers={"User-Agent": user_agent},
        timeout=timeout,
        follow_redirects=True,
    )
    try:
        if respect_robots and not _robots_allows(client, url, user_agent):
            raise RobotsDisallowedError(f"robots.txt disallows fetching {url}")
        resp = client.get(url, headers={"User-Agent": user_agent})
        return FetchResult(
            url=str(resp.url),
            requested_url=url,
            status_code=resp.status_code,
            html=resp.text,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            user_agent=user_agent,
        )
    finally:
        if own_client:
            client.close()
