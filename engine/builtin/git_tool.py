"""Git tools: inspect repository state without shelling through run_command.

Importing this module registers the tools onto the shared registry.
"""

import subprocess

from ..registry import Tool, registry

_MAX_OUTPUT = 20_000


def _run_git(args: list[str], cwd: str = ".") -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "Error: git command timed out after 60s"
    except FileNotFoundError:
        return "Error: git is not installed or not on PATH"
    except Exception as exc:
        return f"Error running git: {exc}"

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


def git_status(path: str = ".") -> str:
    return _truncate(_run_git(["status"], cwd=path))


def git_diff(path: str = ".", staged: bool = False, context_lines: int = 3) -> str:
    args = ["diff", f"-U{context_lines}"]
    if staged:
        args.append("--staged")
    return _truncate(_run_git(args, cwd=path))


def git_commit(message: str, path: str = ".") -> str:
    """Stage everything and commit -- the interactive session's checkpoint
    (the pipeline has its own committing in pipeline/worktree.py; this
    brings the same rollback point to main.py sessions, closing AUDIT D3)."""
    if not message.strip():
        return "Error: commit message must not be empty"
    staged = _run_git(["add", "-A"], cwd=path)
    if staged.startswith("Error"):
        return staged
    result = _run_git(["commit", "-m", message], cwd=path)
    if result.startswith("Error") and "nothing to commit" in result:
        return "(nothing to commit -- working tree clean)"
    return _truncate(result)


def git_log(path: str = ".", max_count: int = 10, oneline: bool = True) -> str:
    if max_count < 1:
        return "Error: max_count must be at least 1"
    args = ["log", f"-{max_count}"]
    if oneline:
        args.append("--oneline")
    return _truncate(_run_git(args, cwd=path))


registry.register(
    Tool(
        name="git_status",
        description=(
            "Show git working tree status (staged, unstaged, and untracked files). "
            "Use before committing or to see what changed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repository path. Defaults to '.'.",
                }
            },
            "required": [],
        },
        handler=git_status,
        risk="safe",
    )
)

registry.register(
    Tool(
        name="git_diff",
        description=(
            "Show git diff for unstaged changes (or staged changes if staged=true). "
            "Use to review edits before committing."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repository path. Defaults to '.'.",
                },
                "staged": {
                    "type": "boolean",
                    "description": "If true, show staged (--cached) diff. Defaults to false.",
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Lines of context around each hunk. Defaults to 3.",
                },
            },
            "required": [],
        },
        handler=git_diff,
        risk="safe",
    )
)

registry.register(
    Tool(
        name="git_commit",
        description=(
            "Stage all changes and create a git commit with the given message. "
            "Use to checkpoint work so it can be reviewed or rolled back."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message."},
                "path": {
                    "type": "string",
                    "description": "Repository path. Defaults to '.'.",
                },
            },
            "required": ["message"],
        },
        handler=git_commit,
        risk="write",
    )
)

registry.register(
    Tool(
        name="git_log",
        description=(
            "Show recent git commit history. Use to understand recent changes "
            "or find a commit to inspect."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repository path. Defaults to '.'.",
                },
                "max_count": {
                    "type": "integer",
                    "description": "Number of commits to show. Defaults to 10.",
                },
                "oneline": {
                    "type": "boolean",
                    "description": "Use compact one-line format. Defaults to true.",
                },
            },
            "required": [],
        },
        handler=git_log,
        risk="safe",
    )
)
