"""The single `web_search` tool, with two backends behind one name (D25):

- Tavily (https://tavily.com -- built for LLM tool-calling, free tier
  available) when an API key is configured -- the reliable path.
- DuckDuckGo lite-HTML scraping (engine/builtin/web.py's
  `duckduckgo_search`) when no key is set -- so search still works with
  zero configuration, just less reliably.

Uses only the standard library (urllib), same as fetch_url -- no new
dependency. Unlike most engine/builtin/ tools this one does NOT
self-register on import: the key is a per-config value, so
interfaces/cli.py and interfaces/pipeline_cli.py call
`registry.register(build_search_tool(config.search_api_key))` -- always,
key or not, since the fallback means the tool works either way.
"""

import json
import urllib.request

from ..registry import Tool
from .offload import maybe_offload
from .web import duckduckgo_search

_MAX_OUTPUT = 20_000
_ENDPOINT = "https://api.tavily.com/search"


def _format_results(payload: dict) -> str:
    results = payload.get("results") or []
    if not results:
        return "(no results)"
    lines = [
        f"{i}. {r.get('title') or '(no title)'}\n   {r.get('url', '')}\n   {(r.get('content') or '').strip()}"
        for i, r in enumerate(results, 1)
    ]
    text = "\n\n".join(lines)
    answer = payload.get("answer")
    return f"Answer: {answer}\n\n{text}" if answer else text


def _tavily_search(api_key: str, query: str, max_results: int, timeout: int) -> str:
    body = json.dumps(
        {"api_key": api_key, "query": query, "max_results": max_results}
    ).encode("utf-8")
    request = urllib.request.Request(
        _ENDPOINT, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return f"Error searching for {query!r}: {exc}"
    return _format_results(payload)


def build_search_tool(api_key: str | None = None, timeout: int = 20) -> Tool:
    """Build the web_search Tool. With `api_key`, searches via Tavily; without
    one, falls back to DuckDuckGo scraping. A closure (not a module-level
    function) because the key is a per-config value, not a global."""

    def web_search(query: str, max_results: int = 5) -> str:
        if not query.strip():
            return "Error: query must not be empty"
        clamped = max(1, min(max_results, 10))
        if api_key:
            text = _tavily_search(api_key, query, clamped, timeout)
        else:
            text = duckduckgo_search(query, clamped, timeout)
        return maybe_offload(text, _MAX_OUTPUT, "web_search") or "(no results)"

    return Tool(
        name="web_search",
        description=(
            "Search the web and return results (title, url, short excerpt) for a "
            "query. Use this for current information not in your training data or "
            "not reachable via a known URL -- for a known URL, use fetch_url instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."},
                "max_results": {
                    "type": "integer",
                    "description": "How many results to return (1-10). Defaults to 5.",
                },
            },
            "required": ["query"],
        },
        handler=web_search,
        risk="dangerous",
    )
