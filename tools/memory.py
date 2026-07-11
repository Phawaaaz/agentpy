"""Memory tool: lets the model persist notes across turns and sessions.

Reimplements Anthropic's memory-tool convention (view/create/str_replace/
insert/delete/rename over a virtual "/memories" directory) as a plain
neutral Tool instead of a provider-native tool type, so it works identically
on every provider -- the harness, not any one model API, owns the storage.
See DESIGN.md D16 for why this is a custom tool rather than Anthropic's
built-in `memory_20250818` type.

All paths are addressed relative to a virtual root and confined to it --
never resolved against the real filesystem root -- mirroring the same
path-traversal protection a text-editor tool needs for untrusted model input.
"""

import os

from .offload import maybe_offload
from .registry import Tool, registry

_MAX_OUTPUT = 20_000
_ROOT = ".harness/memory"


def set_memory_root(path: str) -> None:
    """Point the memory tool at a different directory (default: .harness/memory).

    Called once at startup by the interface, so it stays in sync with
    Config.memory_dir / MemoryTracker without tool modules taking runtime
    config injected through their handler signatures (which would break the
    plain `(**kwargs) -> str` handler contract every other tool follows).
    """
    global _ROOT
    _ROOT = path


def _resolve(path: str) -> str:
    """Confine `path` to the memory root. Raises ValueError if it escapes."""
    root_abs = os.path.abspath(_ROOT)
    candidate_abs = os.path.abspath(os.path.join(root_abs, path.lstrip("/")))
    if candidate_abs != root_abs and not candidate_abs.startswith(root_abs + os.sep):
        raise ValueError(f"path '{path}' escapes the memory directory")
    return candidate_abs


def _view(path: str, view_range: list[int] | None) -> str:
    full = _resolve(path)
    if not os.path.exists(full):
        return f"Error: '{path}' does not exist"
    if os.path.isdir(full):
        entries = sorted(os.listdir(full))
        if not entries:
            return "(empty directory)"
        lines = [f"{e}/" if os.path.isdir(os.path.join(full, e)) else e for e in entries]
        return "\n".join(lines)
    with open(full, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    if view_range:
        start, end = view_range[0], view_range[1]
        start = max(start, 1)
        end = len(lines) if end == -1 else min(end, len(lines))
        lines = lines[start - 1 : end]
        numbered = [f"{i}: {line}" for i, line in enumerate(lines, start=start)]
    else:
        numbered = [f"{i}: {line}" for i, line in enumerate(lines, start=1)]
    return maybe_offload("\n".join(numbered), _MAX_OUTPUT, "memory_view") or "(empty file)"


def _create(path: str, file_text: str) -> str:
    full = _resolve(path)
    directory = os.path.dirname(full)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(file_text)
    return f"Created '{path}' ({len(file_text)} characters)"


def _str_replace(path: str, old_str: str, new_str: str) -> str:
    full = _resolve(path)
    if not os.path.exists(full):
        return f"Error: '{path}' does not exist"
    with open(full, "r", encoding="utf-8") as f:
        content = f.read()
    occurrences = content.count(old_str)
    if occurrences == 0:
        return f"Error: the text to replace was not found in '{path}'"
    if occurrences > 1:
        return f"Error: the text to replace appears {occurrences} times in '{path}'; make it unique"
    with open(full, "w", encoding="utf-8") as f:
        f.write(content.replace(old_str, new_str))
    return f"Edited '{path}'"


def _insert(path: str, insert_line: int, insert_text: str) -> str:
    full = _resolve(path)
    if not os.path.exists(full):
        return f"Error: '{path}' does not exist"
    with open(full, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    if insert_line < 0 or insert_line > len(lines):
        return f"Error: insert_line {insert_line} is out of range (file has {len(lines)} lines)"
    lines[insert_line:insert_line] = insert_text.splitlines()
    with open(full, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return f"Inserted into '{path}' after line {insert_line}"


def _delete(path: str) -> str:
    full = _resolve(path)
    if not os.path.exists(full):
        return f"Error: '{path}' does not exist"
    if os.path.isdir(full):
        return f"Error: '{path}' is a directory, not a file"
    os.remove(full)
    return f"Deleted '{path}'"


def _rename(old_path: str, new_path: str) -> str:
    old_full = _resolve(old_path)
    new_full = _resolve(new_path)
    if not os.path.exists(old_full):
        return f"Error: '{old_path}' does not exist"
    directory = os.path.dirname(new_full)
    if directory:
        os.makedirs(directory, exist_ok=True)
    os.rename(old_full, new_full)
    return f"Renamed '{old_path}' -> '{new_path}'"


_COMMANDS = {
    "view": lambda a: _view(a.get("path", "."), a.get("view_range")),
    "create": lambda a: _create(a["path"], a.get("file_text", "")),
    "str_replace": lambda a: _str_replace(a["path"], a.get("old_str", ""), a.get("new_str", "")),
    "insert": lambda a: _insert(a["path"], int(a.get("insert_line", 0)), a.get("insert_text", "")),
    "delete": lambda a: _delete(a["path"]),
    "rename": lambda a: _rename(a["old_path"], a["new_path"]),
}


def memory(
    command: str,
    path: str = "",
    file_text: str = "",
    old_str: str = "",
    new_str: str = "",
    insert_line: int = 0,
    insert_text: str = "",
    old_path: str = "",
    new_path: str = "",
    view_range: list[int] | None = None,
) -> str:
    handler = _COMMANDS.get(command)
    if handler is None:
        return f"Error: unknown memory command '{command}' (use view/create/str_replace/insert/delete/rename)"
    args = {
        "path": path,
        "file_text": file_text,
        "old_str": old_str,
        "new_str": new_str,
        "insert_line": insert_line,
        "insert_text": insert_text,
        "old_path": old_path,
        "new_path": new_path,
        "view_range": view_range,
    }
    try:
        return handler(args)
    except ValueError as exc:  # path escaped the memory root
        return f"Error: {exc}"
    except KeyError as exc:
        return f"Error: missing required argument {exc} for command '{command}'"
    except Exception as exc:
        return f"Error: {exc}"


registry.register(
    Tool(
        name="memory",
        description=(
            "Persist notes across turns and sessions in a private memory directory. "
            "Use it to remember decisions, progress, and context worth keeping between "
            "conversations -- check it at the start of a task and update it as you learn things. "
            "Commands: view (read a file or list a directory), create (write a new file), "
            "str_replace (edit an existing file, old_str must be unique), "
            "insert (insert text after a given line), delete, rename."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "enum": ["view", "create", "str_replace", "insert", "delete", "rename"],
                    "description": "Which memory operation to perform.",
                },
                "path": {"type": "string", "description": "Path within the memory directory."},
                "file_text": {"type": "string", "description": "Full content for 'create'."},
                "old_str": {"type": "string", "description": "Exact text to replace, for 'str_replace'."},
                "new_str": {"type": "string", "description": "Replacement text, for 'str_replace'."},
                "insert_line": {
                    "type": "integer",
                    "description": "Line number to insert after (0 = start of file), for 'insert'.",
                },
                "insert_text": {"type": "string", "description": "Text to insert, for 'insert'."},
                "old_path": {"type": "string", "description": "Current path, for 'rename'."},
                "new_path": {"type": "string", "description": "New path, for 'rename'."},
                "view_range": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional [start, end] 1-indexed line range for 'view' (end=-1 for EOF).",
                },
            },
            "required": ["command"],
        },
        handler=memory,
        risk="write",
    )
)
