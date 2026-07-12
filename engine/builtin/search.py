"""Web search tool via the Tavily API (https://tavily.com -- built for LLM
agent tool-calling, free tier available). Uses only the standard library
(urllib), same as fetch_url -- no new dependency.

Unlike the other engine/builtin/ tools, this one does NOT self-register on
import: it needs an API key, so interfaces/cli.py only registers it when
HARNESS_SEARCH_API_KEY is configured (same opt-in shape as
multiagent/coordinator.py's build_delegate_tool -- no key means no tool,
not a tool that always errors). See DESIGN.md D24.
"""

import json
import urllib.request

from ..registry import Tool
from .offload import maybe_offload

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


def build_search_tool(api_key: str, timeout: int = 20) -> Tool:
    """Build the web_search Tool bound to `api_key`. A closure (not a module-
    level function) because the key is a per-config value, not a global."""

    def web_search(query: str, max_results: int = 5) -> str:
        body = json.dumps(
            {"api_key": api_key, "query": query, "max_results": max(1, min(max_results, 10))}
        ).encode("utf-8")
        request = urllib.request.Request(
            _ENDPOINT, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return f"Error searching for {query!r}: {exc}"
        text = _format_results(payload)
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
