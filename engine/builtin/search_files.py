"""File search tools: find files by glob pattern and grep file contents.

Closes the audit's C2 gap (the only way to search files used to be the
dangerous-risk run_command escape hatch) with two safe, read-only tools.
Both respect workspace confinement (D27) via the same resolve() the other
filesystem tools use. Importing this module registers the tools onto the
shared registry.
"""

import fnmatch
import os
import re

from ..registry import Tool, registry
from ..workspace import resolve
from .offload import maybe_offload

_MAX_OUTPUT = 20_000
_MAX_MATCHES = 500
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".harness"}
_MAX_FILE_BYTES = 2_000_000  # don't grep through huge binaries


def find_files(pattern: str, path: str = ".") -> str:
    """Recursively find files whose name matches a glob pattern."""
    try:
        root = resolve(path)
    except ValueError as exc:
        return f"Error: {exc}"
    if not os.path.isdir(root):
        return f"Error: directory not found: {path}"
    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if fnmatch.fnmatch(name, pattern):
                matches.append(os.path.relpath(os.path.join(dirpath, name), root))
                if len(matches) >= _MAX_MATCHES:
                    matches.append(f"... [stopped at {_MAX_MATCHES} matches]")
                    return "\n".join(matches)
    if not matches:
        return "(no matches)"
    return maybe_offload("\n".join(sorted(matches)), _MAX_OUTPUT, "find_files")


def grep_files(pattern: str, path: str = ".", file_glob: str = "*") -> str:
    """Search file contents for a regular expression; returns
    path:line_number: line for each match."""
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Error: invalid regular expression: {exc}"
    try:
        root = resolve(path)
    except ValueError as exc:
        return f"Error: {exc}"
    if not os.path.isdir(root):
        return f"Error: directory not found: {path}"

    lines: list[str] = []
    hits = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in sorted(filenames):
            if not fnmatch.fnmatch(name, file_glob):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > _MAX_FILE_BYTES:
                    continue
                with open(full, "r", encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue  # unreadable or binary: skip, don't fail the search
            rel = os.path.relpath(full, root)
            for i, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    lines.append(f"{rel}:{i}: {line.strip()}")
                    hits += 1
                    if hits >= _MAX_MATCHES:
                        lines.append(f"... [stopped at {_MAX_MATCHES} matches]")
                        return maybe_offload("\n".join(lines), _MAX_OUTPUT, "grep_files")
    if not lines:
        return "(no matches)"
    return maybe_offload("\n".join(lines), _MAX_OUTPUT, "grep_files")


registry.register(
    Tool(
        name="find_files",
        description=(
            "Recursively find files whose NAME matches a glob pattern (e.g. '*.py', "
            "'test_*'). Returns relative paths. Use grep_files to search contents."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern matched against file names."},
                "path": {"type": "string", "description": "Directory to search from. Defaults to '.'."},
            },
            "required": ["pattern"],
        },
        handler=find_files,
        risk="safe",
    )
)

registry.register(
    Tool(
        name="grep_files",
        description=(
            "Search file CONTENTS for a regular expression, returning "
            "path:line_number: line for each match. Optionally restrict to file "
            "names matching a glob (e.g. '*.py')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression to search for."},
                "path": {"type": "string", "description": "Directory to search from. Defaults to '.'."},
                "file_glob": {"type": "string", "description": "Only search files matching this glob. Defaults to '*'."},
            },
            "required": ["pattern"],
        },
        handler=grep_files,
        risk="safe",
    )
)
