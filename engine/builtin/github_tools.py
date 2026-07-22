"""GitHub tool: act on the *current user's* GitHub account via the REST API.

Each web user connects their own GitHub through OAuth; the server stores their
token and, per request, sets it on a ContextVar (D28) — so user A's turn uses
A's token and user B's uses B's, even though the tool is registered once on the
shared registry. When the current user hasn't connected GitHub, the tool says
so instead of failing, prompting them to click "Connect GitHub".

One deliberately-generic tool, `github_request`, covers the whole REST API
(list repos, read/create issues, open PRs, read files, …) rather than dozens of
narrow tools — the model already knows the GitHub API surface.
"""

import json as _json
from contextvars import ContextVar

import httpx

from ..registry import Tool, registry

_TOKEN: ContextVar[str | None] = ContextVar("github_token", default=None)
_API = "https://api.github.com"
_MAX_OUTPUT = 20_000
_TIMEOUT = 30


def set_github_token(token: str | None) -> None:
    """Set the current request's GitHub token (per-user). Called by the server
    at the start of a turn, not passed through tool signatures."""
    _TOKEN.set(token or None)


def github_request(method: str, path: str, body: str | None = None) -> str:
    """Call the GitHub REST API as the connected user and return the response.

    method: GET/POST/PATCH/PUT/DELETE. path: e.g. "/user/repos" or
    "/repos/owner/repo/issues". body: optional JSON string for writes.
    """
    token = _TOKEN.get()
    if not token:
        return ("The user has not connected their GitHub account yet. Ask them "
                "to click 'Connect GitHub' in the app, then retry.")

    m = (method or "GET").upper()
    if m not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
        return f"Error: unsupported method {method!r}"
    if not path.startswith("/"):
        path = "/" + path
    url = _API + path

    json_body = None
    if body:
        try:
            json_body = _json.loads(body)
        except _json.JSONDecodeError:
            return f"Error: body is not valid JSON: {body[:200]}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        resp = httpx.request(m, url, headers=headers, json=json_body, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        return f"Error calling GitHub {m} {path}: {exc}"

    text = resp.text
    try:  # pretty-print JSON so the model reads it cleanly
        text = _json.dumps(resp.json(), indent=2)
    except ValueError:
        pass
    if len(text) > _MAX_OUTPUT:
        text = text[:_MAX_OUTPUT] + "\n... [truncated]"
    prefix = "" if resp.is_success else f"GitHub returned HTTP {resp.status_code}:\n"
    return prefix + text


registry.register(
    Tool(
        name="github_request",
        description=(
            "Call the GitHub REST API as the connected user. Use for anything "
            "on GitHub: list repos (GET /user/repos), list issues "
            "(GET /repos/{owner}/{repo}/issues), create an issue "
            "(POST /repos/{owner}/{repo}/issues with a JSON body), read a file "
            "(GET /repos/{owner}/{repo}/contents/{path}), etc. Returns the JSON "
            "response. If the user hasn't connected GitHub, it says so."
        ),
        parameters={
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "HTTP method: GET, POST, PATCH, PUT, or DELETE."},
                "path": {"type": "string", "description": "API path, e.g. /user/repos or /repos/owner/repo/issues."},
                "body": {"type": "string", "description": "Optional JSON string body for writes."},
            },
            "required": ["method", "path"],
        },
        handler=github_request,
        risk="write",
    )
)
