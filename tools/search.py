"""Search tools: grep file contents and find files by glob pattern.

Importing this module registers the tools onto the shared registry.
"""

import fnmatch
import glob as _glob
import os
import re

from .registry import Tool, registry

_MAX_OUTPUT = 20_000
_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".harness"}
_DEFAULT_MAX_RESULTS = 200


def _truncate(text: str) -> str:
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + "\n... [truncated]"
    return text


def _compile_pattern(pattern: str, case_insensitive: bool) -> re.Pattern[str]:
    flags = re.MULTILINE
    if case_insensitive:
        flags |= re.IGNORECASE
    return re.compile(pattern, flags)


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIRS or name.startswith(".")


def _matches_glob(rel_path: str, glob_pattern: str) -> bool:
    norm = rel_path.replace("\\", "/")
    if glob_pattern in ("**/*", "*"):
        return True
    if glob_pattern.startswith("**/"):
        suffix = glob_pattern[3:]
        if suffix == "*":
            return True
        return fnmatch.fnmatch(os.path.basename(norm), suffix)
    return fnmatch.fnmatch(norm, glob_pattern) or fnmatch.fnmatch(
        os.path.basename(norm), glob_pattern
    )


def grep(
    pattern: str,
    path: str = ".",
    glob: str = "**/*",
    case_insensitive: bool = False,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> str:
    """Search file contents for a regex pattern."""
    try:
        regex = _compile_pattern(pattern, case_insensitive)
    except re.error as exc:
        return f"Error: invalid regex pattern: {exc}"

    if max_results < 1:
        return "Error: max_results must be at least 1"

    root = os.path.abspath(path)
    if not os.path.exists(root):
        return f"Error: path not found: {path}"

    matches: list[str] = []
    truncated = False

    def _scan_file(file_path: str) -> None:
        nonlocal truncated
        if truncated:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    if regex.search(line):
                        rel = os.path.relpath(file_path, start=os.getcwd())
                        matches.append(f"{rel}:{line_no}:{line.rstrip()}")
                        if len(matches) >= max_results:
                            truncated = True
                            return
        except (UnicodeDecodeError, PermissionError, OSError):
            return

    if os.path.isfile(root):
        _scan_file(root)
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                rel = os.path.relpath(file_path, start=root)
                if not _matches_glob(rel, glob):
                    continue
                _scan_file(file_path)
                if truncated:
                    break
            if truncated:
                break

    if not matches:
        return "(no matches)"
    body = "\n".join(matches)
    if truncated:
        body += f"\n... [stopped at {max_results} matches]"
    return _truncate(body)


def find_files(pattern: str) -> str:
    """Find files matching a glob pattern (supports **)."""
    try:
        matches = sorted(_glob.glob(pattern, recursive=True))
    except Exception as exc:
        return f"Error: {exc}"
    if not matches:
        return "(no matches)"
    return _truncate("\n".join(matches))


registry.register(
    Tool(
        name="grep",
        description=(
            "Search file contents for a regex pattern. Scans a file or directory "
            "tree, skipping hidden and vendor folders (.git, .venv, node_modules). "
            "Returns matches as path:line:content. Use before editing to locate "
            "symbols, imports, or strings."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search. Defaults to '.'.",
                },
                "glob": {
                    "type": "string",
                    "description": "Filename filter, e.g. '*.py'. Defaults to '**/*'.",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Ignore case when matching. Defaults to false.",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"Stop after this many matches. Defaults to {_DEFAULT_MAX_RESULTS}.",
                },
            },
            "required": ["pattern"],
        },
        handler=grep,
        risk="safe",
    )
)

registry.register(
    Tool(
        name="find_files",
        description=(
            "Find files matching a glob pattern (supports ** for recursive search). "
            "Returns one path per line. Use to discover files before reading them."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'.",
                }
            },
            "required": ["pattern"],
        },
        handler=find_files,
        risk="safe",
    )
)
