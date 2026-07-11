"""Shared helper: when a tool's output is too large to return inline, write
it to a file and return a preview + the file path instead of silently
truncating the rest away. Used by tools/filesystem.py, tools/shell.py,
tools/web.py, and tools/memory.py so oversized output stays recoverable (the
model can `read_file` further into it) rather than lost.
"""

import hashlib
import os

_ROOT = ".harness/offload"
_PREVIEW_CHARS = 4_000


def set_offload_root(path: str) -> None:
    """Point offloaded files at a different directory (default:
    .harness/offload). Same pattern as tools/memory.py's set_memory_root --
    called once at startup by the interface, not injected through tool
    handler signatures (which would break the plain (**kwargs) -> str
    handler contract every other tool follows)."""
    global _ROOT
    _ROOT = path


def maybe_offload(text: str, max_inline: int, label: str) -> str:
    """Return `text` unchanged if it fits within `max_inline` characters.
    Otherwise write the full text to a file under the offload root -- named
    deterministically from its content, so identical output reuses the same
    file instead of writing a duplicate -- and return a preview plus the
    file path so the model can read more of it on demand."""
    if len(text) <= max_inline:
        return text

    os.makedirs(_ROOT, exist_ok=True)
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    path = os.path.join(_ROOT, f"{label}-{digest}.txt")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    preview = text[:_PREVIEW_CHARS]
    return (
        f"{preview}\n\n"
        f"... [showing {_PREVIEW_CHARS} of {len(text)} characters. "
        f"Full output saved to {path} -- use read_file to see more of it.]"
    )
