"""Local web extraction via defuddle — plugin form.

Every other bundled extract backend (Firecrawl, Tavily, Exa, Parallel)
hands the URL to a paid remote API. This one runs locally: Hermes fetches
the page itself and pipes the HTML to the ``defuddle`` CLI, which strips
boilerplate and emits clean markdown. No API key, no account.

Fetching stays on the Python side deliberately. ``defuddle parse <url>``
would fetch the page in Node, which routes around Hermes' SSRF guard and
the website-access policy entirely; reading HTML from stdin keeps every
network hop under the same checks the other providers get. The cost is
that relative links in the extracted markdown are not rebased against the
page URL, since the stdin path gives defuddle no base URL to resolve
against.

Search is not offered — defuddle only cleans a page you already have.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx

from agent.web_search_provider import WebSearchProvider
from tools.url_safety import is_safe_url
from tools.website_policy import check_website_access

logger = logging.getLogger(__name__)

# Wall-clock cap for a single defuddle subprocess. Parsing is CPU-bound and
# local, so anything past this is a hung Node process rather than a slow page.
_PARSE_TIMEOUT_SECS = 30

# Cap for fetching one page before parsing.
_FETCH_TIMEOUT_SECS = 20

# Redirect hops followed manually. Each hop is re-checked against the SSRF
# guard and the website policy, which is why this does not use httpx's own
# follow_redirects — that would complete the unsafe request before we could
# inspect where it landed.
_MAX_REDIRECTS = 5

# Bound the HTML handed to the parser. Defuddle holds the whole document in
# memory as a DOM; a multi-hundred-MB response would balloon the Node heap.
_MAX_HTML_BYTES = 10 * 1024 * 1024

_USER_AGENT = (
    "Mozilla/5.0 (compatible; HermesAgent/1.0; +https://github.com/) "
    "defuddle-extract"
)


def _repo_root() -> Path:
    # plugins/web/defuddle/provider.py -> repo root is four levels up.
    return Path(__file__).resolve().parents[3]


def defuddle_bin() -> Optional[str]:
    """Locate a runnable defuddle CLI, or None.

    Checks the repo-local ``node_modules/.bin`` before PATH so a workspace
    install wins over a stale global one. Never touches the network — this
    runs on every ``hermes tools`` paint.
    """
    override = os.environ.get("DEFUDDLE_BIN")
    if override:
        return override if Path(override).exists() else None

    local = _repo_root() / "node_modules" / ".bin" / "defuddle"
    if local.exists():
        return str(local)

    return shutil.which("defuddle")


def _fetch_html(url: str) -> tuple[str, str]:
    """Fetch a page, returning ``(final_url, html)``.

    Follows redirects one hop at a time so each intermediate URL is checked
    before it is requested. Raises ValueError with a user-facing reason when
    a hop is blocked or the response is not HTML.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not is_safe_url(current):
            raise ValueError(f"Blocked unsafe URL: {current}")
        blocked = check_website_access(current)
        if blocked:
            raise ValueError(blocked["message"])

        resp = httpx.get(
            current,
            timeout=_FETCH_TIMEOUT_SECS,
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT},
        )

        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location")
            if not location:
                raise ValueError(f"Redirect with no Location header: {current}")
            current = urljoin(current, location)
            continue

        resp.raise_for_status()

        ctype = resp.headers.get("content-type", "")
        if ctype and "html" not in ctype.lower():
            raise ValueError(f"Not an HTML page (content-type: {ctype})")

        content = resp.content[:_MAX_HTML_BYTES]
        # Prefer the charset httpx negotiated; fall back to UTF-8 with
        # replacement so a mislabelled page degrades instead of raising.
        encoding = resp.encoding or "utf-8"
        return current, content.decode(encoding, errors="replace")

    raise ValueError(f"Too many redirects starting from {url}")


def _run_defuddle(html: str, binary: str) -> Dict[str, Any]:
    """Pipe HTML through the defuddle CLI and return its parsed JSON."""
    proc = subprocess.run(
        [binary, "parse", "--markdown", "--json"],
        input=html,
        capture_output=True,
        text=True,
        timeout=_PARSE_TIMEOUT_SECS,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise ValueError(f"defuddle exited {proc.returncode}: {stderr[:300]}")

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"defuddle returned non-JSON output: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("defuddle returned a non-object JSON payload")
    return parsed


class DefuddleWebSearchProvider(WebSearchProvider):
    """Extract-only backend backed by the local defuddle CLI."""

    @property
    def name(self) -> str:
        return "defuddle"

    @property
    def display_name(self) -> str:
        return "Defuddle (local)"

    def is_available(self) -> bool:
        return defuddle_bin() is not None

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Extract clean content for each URL. Per-URL failures stay per-URL."""
        from tools.interrupt import is_interrupted

        binary = defuddle_bin()
        if binary is None:
            msg = (
                "defuddle CLI not found. Install it with "
                "`npm install -g defuddle`, or set DEFUDDLE_BIN."
            )
            return [{"url": u, "title": "", "content": "", "error": msg} for u in urls]

        results: List[Dict[str, Any]] = []
        for url in urls:
            if is_interrupted():
                results.append({"url": url, "title": "", "content": "", "error": "Interrupted"})
                continue
            results.append(self._extract_one(url, binary))
        return results

    def _extract_one(self, url: str, binary: str) -> Dict[str, Any]:
        try:
            final_url, html = _fetch_html(url)
        except ValueError as exc:
            return {"url": url, "title": "", "content": "", "error": str(exc)}
        except httpx.HTTPError as exc:
            return {"url": url, "title": "", "content": "", "error": f"Fetch failed: {exc}"}

        try:
            parsed = _run_defuddle(html, binary)
        except subprocess.TimeoutExpired:
            return {
                "url": final_url,
                "title": "",
                "content": "",
                "error": f"defuddle timed out after {_PARSE_TIMEOUT_SECS}s",
            }
        except ValueError as exc:
            return {"url": final_url, "title": "", "content": "", "error": str(exc)}
        except OSError as exc:
            return {
                "url": final_url,
                "title": "",
                "content": "",
                "error": f"Could not run defuddle: {exc}",
            }

        content = str(parsed.get("content") or "")
        return {
            "url": final_url,
            "title": str(parsed.get("title") or ""),
            "content": content,
            "raw_content": content,
            "metadata": {
                key: parsed[key]
                for key in (
                    "author", "description", "domain", "published",
                    "site", "language", "wordCount", "image",
                )
                if parsed.get(key)
            },
        }

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Defuddle (local)",
            "badge": "free",
            "tag": "Runs locally via the defuddle npm CLI. Extract only, no API key.",
            "env_vars": [],
        }
