"""GitHub tools: wrap the gh CLI for PRs and CI status.

Requires `gh` to be installed and authenticated (`gh auth login`).
Importing this module registers the tools onto the shared registry.
"""

import json
import subprocess

from .registry import Tool, registry

_MAX_OUTPUT = 20_000


def _run_gh(args: list[str], cwd: str = ".") -> str:
    try:
        result = subprocess.run(
            ["gh", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: gh command timed out after 120s"
    except FileNotFoundError:
        return "Error: gh is not installed or not on PATH (install from https://cli.github.com)"
    except Exception as exc:
        return f"Error running gh: {exc}"

    output = (result.stdout or "").strip()
    if result.stderr:
        stderr = result.stderr.strip()
        output = f"{output}\n[stderr]\n{stderr}".strip() if output else f"[stderr]\n{stderr}"
    if result.returncode != 0:
        return f"Error (exit {result.returncode}): {output or '(no output)'}"
    return output or "(no output)"


def _truncate(text: str) -> str:
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + "\n... [truncated]"
    return text


def github_pr_create(
    title: str,
    body: str = "",
    draft: bool = False,
    path: str = ".",
) -> str:
    args = ["pr", "create", "--title", title, "--body", body]
    if draft:
        args.append("--draft")
    return _truncate(_run_gh(args, cwd=path))


def github_pr_view(number: str = "", path: str = ".") -> str:
    args = ["pr", "view"]
    if number:
        args.append(number)
    args.extend(["--json", "title,state,url,body,commits,checksStatus"])
    raw = _run_gh(args, cwd=path)
    if raw.startswith("Error"):
        return raw
    try:
        data = json.loads(raw)
        lines = [
            f"title: {data.get('title', '')}",
            f"state: {data.get('state', '')}",
            f"url: {data.get('url', '')}",
            f"checks: {data.get('checksStatus', '')}",
            f"commits: {data.get('commits', '')}",
            f"body:\n{data.get('body', '')}",
        ]
        return _truncate("\n".join(lines))
    except json.JSONDecodeError:
        return _truncate(raw)


def github_ci_status(branch: str = "", limit: int = 5, path: str = ".") -> str:
    if limit < 1:
        return "Error: limit must be at least 1"
    args = ["run", "list", "--limit", str(limit), "--json", "databaseId,status,conclusion,name,headBranch,url"]
    if branch:
        args.extend(["--branch", branch])
    raw = _run_gh(args, cwd=path)
    if raw.startswith("Error"):
        return raw
    try:
        runs = json.loads(raw)
        if not runs:
            return "(no workflow runs found)"
        lines = []
        for run in runs:
            lines.append(
                f"{run.get('name')} | {run.get('status')} | {run.get('conclusion')} | "
                f"branch={run.get('headBranch')} | {run.get('url')}"
            )
        return _truncate("\n".join(lines))
    except json.JSONDecodeError:
        return _truncate(raw)


registry.register(
    Tool(
        name="github_pr_create",
        description=(
            "Create a GitHub pull request for the current branch using gh. "
            "The branch must already be pushed to origin."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "PR title."},
                "body": {"type": "string", "description": "PR description. Defaults to empty."},
                "draft": {
                    "type": "boolean",
                    "description": "Open as draft PR. Defaults to false.",
                },
                "path": {
                    "type": "string",
                    "description": "Repository path. Defaults to '.'.",
                },
            },
            "required": ["title"],
        },
        handler=github_pr_create,
        risk="write",
    )
)

registry.register(
    Tool(
        name="github_pr_view",
        description=(
            "View a GitHub pull request (current branch if number omitted) "
            "including title, state, URL, and check status."
        ),
        parameters={
            "type": "object",
            "properties": {
                "number": {
                    "type": "string",
                    "description": "PR number. Omit to view PR for the current branch.",
                },
                "path": {
                    "type": "string",
                    "description": "Repository path. Defaults to '.'.",
                },
            },
            "required": [],
        },
        handler=github_pr_view,
        risk="safe",
    )
)

registry.register(
    Tool(
        name="github_ci_status",
        description=(
            "List recent GitHub Actions workflow runs and their status/conclusion. "
            "Use to check if CI passed on a branch."
        ),
        parameters={
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "description": "Filter by branch name. Defaults to all branches.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max runs to return. Defaults to 5.",
                },
                "path": {
                    "type": "string",
                    "description": "Repository path. Defaults to '.'.",
                },
            },
            "required": [],
        },
        handler=github_ci_status,
        risk="safe",
    )
)
