"""Filesystem tools: read, write, edit, and list files.

Importing this module registers the tools onto the shared registry.
"""

import os

from .offload import maybe_offload
from .registry import Tool, registry

_MAX_OUTPUT = 20_000  # keep tool output from blowing up the context window


def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except UnicodeDecodeError:
        return f"Error: {path} is not a UTF-8 text file"
    except Exception as exc:
        return f"Error reading {path}: {exc}"
    return maybe_offload(content, _MAX_OUTPUT, "read_file") or "(empty file)"


def write_file(path: str, content: str) -> str:
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as exc:
        return f"Error writing {path}: {exc}"
    return f"Wrote {len(content)} characters to {path}"


def edit_file(path: str, old: str, new: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    occurrences = content.count(old)
    if occurrences == 0:
        return f"Error: the text to replace was not found in {path}"
    if occurrences > 1:
        return (
            f"Error: the text to replace appears {occurrences} times in {path}; "
            "make it unique so exactly one match is edited"
        )
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content.replace(old, new))
    except Exception as exc:
        return f"Error writing {path}: {exc}"
    return f"Edited {path}"


def list_dir(path: str = ".") -> str:
    try:
        entries = sorted(os.listdir(path))
    except FileNotFoundError:
        return f"Error: directory not found: {path}"
    except Exception as exc:
        return f"Error listing {path}: {exc}"
    if not entries:
        return "(empty directory)"
    lines = []
    for name in entries:
        full = os.path.join(path, name)
        lines.append(f"{name}/" if os.path.isdir(full) else name)
    return "\n".join(lines)


registry.register(
    Tool(
        name="read_file",
        description="Read and return the full text contents of a file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."}
            },
            "required": ["path"],
        },
        handler=read_file,
        risk="safe",
    )
)

registry.register(
    Tool(
        name="list_dir",
        description="List the files and subdirectories in a directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path. Defaults to the current directory.",
                }
            },
            "required": [],
        },
        handler=list_dir,
        risk="safe",
    )
)

registry.register(
    Tool(
        name="write_file",
        description="Create a file or overwrite it entirely with new content.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "content": {"type": "string", "description": "Full content to write."},
            },
            "required": ["path", "content"],
        },
        handler=write_file,
        risk="write",
    )
)

registry.register(
    Tool(
        name="edit_file",
        description=(
            "Replace an exact snippet of text in a file with new text. "
            "The snippet must appear exactly once."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "old": {"type": "string", "description": "Exact text to replace."},
                "new": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old", "new"],
        },
        handler=edit_file,
        risk="write",
    )
)
