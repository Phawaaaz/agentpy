"""Workspace confinement: optionally restrict filesystem/shell tools to a
per-user, per-session directory (workspaces/{user}/{session}/).

Off by default (HARNESS_CONFINE_WORKSPACE, D27): with no root set, every
tool behaves exactly as it always has -- unconfined, operating on whatever
path it's given. With a root set (the CLI sets one per session when
config.confine_workspace is on; a future server would set one per request,
always), `resolve()` maps every tool path inside the root and rejects
anything that escapes it -- the same whitelist-the-untrusted-path
protection context_engine/memory_tool.py has always applied to memory
files, now shared here so the two confinement implementations can't drift.

The module-level root is the same deliberate, startup-set global pattern as
memory_tool.set_memory_root and offload.set_offload_root -- all three are
scheduled to become session-scoped together (PLAN.md Milestone 3).
"""

import os

_ROOT: str | None = None  # None = unconfined (the historical behavior)


def set_workspace_root(path: str | None) -> None:
    """Confine filesystem/shell tools to `path` (created if missing), or
    lift confinement with None. Called by the interface at session start
    and whenever the session id changes (/new, /load)."""
    global _ROOT
    if path is not None:
        os.makedirs(path, exist_ok=True)
    _ROOT = path


def workspace_root() -> str | None:
    """The current confinement root, or None when unconfined."""
    return _ROOT


def confine(root: str, path: str, treat_absolute_as_relative: bool = False) -> str:
    """Resolve `path` against `root` and raise ValueError if the result
    escapes it. Symlinks are resolved (realpath) so a link pointing outside
    the root can't smuggle access. With `treat_absolute_as_relative`, a
    leading "/" is stripped first ("/notes.md" means "<root>/notes.md" --
    the memory tool's virtual-root semantics); without it, an absolute path
    is allowed only if it already lies inside the root."""
    root_abs = os.path.realpath(os.path.abspath(root))
    p = path.lstrip("/") if treat_absolute_as_relative else path
    joined = p if os.path.isabs(p) else os.path.join(root_abs, p)
    candidate = os.path.realpath(os.path.abspath(joined))
    if candidate != root_abs and not candidate.startswith(root_abs + os.sep):
        raise ValueError(f"path '{path}' escapes the workspace directory")
    return candidate


def resolve(path: str) -> str:
    """Confine `path` to the active workspace root, or return it unchanged
    when no root is set. Raises ValueError on escape (tool handlers catch
    it and return an error string, per PRINCIPLES rule 1)."""
    if _ROOT is None:
        return path
    return confine(_ROOT, path)
