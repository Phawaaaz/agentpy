"""Web tool: fetch a URL and return its text.

Uses only the standard library (no new dependencies). HTML is reduced to rough
plain text so the model gets readable content instead of markup. Marked
"dangerous" because it reaches the network.
"""

import re
import urllib.request

from ..registry import Tool, registry
from .offload import maybe_offload

_MAX_OUTPUT = 20_000
_USER_AGENT = "agentic-harness/0.1 (+https://localhost)"


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)  # drop scripts/styles
    html = re.sub(r"(?s)<[^>]+>", " ", html)  # strip remaining tags
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&lt;", "<", html)
    html = re.sub(r"&gt;", ">", html)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n\s*\n\s*\n+", "\n\n", html)
    return html.strip()


def fetch_url(url: str, timeout: int = 20) -> str:
    if not url.lower().startswith(("http://", "https://")):
        return "Error: url must start with http:// or https://"
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read(2_000_000)  # cap download at ~2MB
            content_type = response.headers.get_content_type()
    except Exception as exc:
        return f"Error fetching {url}: {exc}"

    body = raw.decode(charset, errors="replace")
    text = _html_to_text(body) if "html" in content_type else body
    return maybe_offload(text, _MAX_OUTPUT, "fetch_url") or "(empty response)"


registry.register(
    Tool(
        name="fetch_url",
        description=(
            "Fetch a web page or API over HTTP(S) and return its text content "
            "(HTML is reduced to plain text). Use to read documentation or data."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full http(s) URL."},
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait. Defaults to 20.",
                },
            },
            "required": ["url"],
        },
        handler=fetch_url,
        risk="dangerous",
    )
)
