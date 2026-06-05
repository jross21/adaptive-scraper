"""JS rendering via Playwright (Phase 2).

Some pages ship an empty shell and build the DOM client-side; a static httpx fetch sees
nothing useful. `render_page` drives a headless Chromium, waits for the page to settle, and
returns the rendered HTML in the *same* `FetchResult` shape as `recon.fetch`, so everything
downstream (compress → codegen → interpret → validate) is unchanged. The same robots.txt
allow-check runs before the browser launches, so rendering stays polite.

`looks_js_rendered` is the cheap, offline heuristic the CLI uses to decide whether a failed
static attempt is worth retrying with a browser.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from selectolax.parser import HTMLParser

from ..config import REQUEST_TIMEOUT, USER_AGENT
from .fetch import FetchResult, RobotsDisallowedError, _robots_allows

#: Common single-page-app mount points — an empty one is a strong "needs JS" signal.
SPA_ROOT_IDS = ("root", "app", "__next", "__nuxt")

#: Below this much visible body text, a page is almost certainly an unrendered shell.
MIN_TEXT_CHARS = 200


def looks_js_rendered(html: str) -> bool:
    """Heuristic: does this static HTML look like an unrendered SPA shell (so rendering
    would likely help)? True when a known SPA mount point is present but empty, or the
    visible body text (excluding scripts/styles) is negligible."""
    tree = HTMLParser(html)
    if tree.body is None:
        return False
    # An empty SPA mount point is a strong signal even amid boilerplate. Note selectolax's
    # css("*") includes the node itself, so an element with no descendants yields [self].
    for node in tree.css("[id]"):
        if node.attributes.get("id") in SPA_ROOT_IDS and len(node.css("*")) <= 1:
            return True
    # Otherwise: strip non-visible tags and check whether any real text remains.
    tree.strip_tags(["script", "style", "noscript", "template", "svg"])
    body = tree.body
    text = (body.text(deep=True, separator=" ") or "").strip() if body else ""
    return len(text) < MIN_TEXT_CHARS


def render_page(
    url: str,
    *,
    user_agent: str = USER_AGENT,
    respect_robots: bool = True,
    timeout: float = REQUEST_TIMEOUT,
    wait_until: str = "networkidle",
) -> FetchResult:
    if respect_robots:
        with httpx.Client(
            headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True
        ) as client:
            if not _robots_allows(client, url, user_agent):
                raise RobotsDisallowedError(f"robots.txt disallows fetching {url}")

    # Import lazily: the package stays importable without the browser binary; only an
    # actual render needs `playwright install chromium`.
    from playwright.sync_api import sync_playwright

    timeout_ms = int(timeout * 1000)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=user_agent)
            response = page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            html = page.content()
            final_url = page.url
            status_code = response.status if response is not None else 0
        finally:
            browser.close()

    return FetchResult(
        url=final_url,
        requested_url=url,
        status_code=status_code,
        html=html,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        user_agent=user_agent,
        rendered=True,
    )
