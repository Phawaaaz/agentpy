"""MemoryTracker -- automatic, harness-side activity memory.

The `memory` tool (tools/memory.py) is for the *model* to write deliberate
notes. This is the complementary, automatic half: it listens on the same
on_event hook as EventLogger (observability/log.py) and derives a standing
summary of what the harness is working on -- the current task, which files
it has touched, and how often each tool was used -- so that picture exists
even if the model never calls the memory tool itself.

Kept as a plain listener with the same `log(kind, *details)` shape as
EventLogger so it composes into interfaces/cli.py's existing event fan-out
without changing the orchestrator's callback contract (core/orchestrator.py
is untouched).
"""

import os
from collections import Counter

# tool name -> (its path-like argument, the action word to record)
_FILE_ARG_TOOLS = {
    "read_file": ("path", "read"),
    "write_file": ("path", "write"),
    "edit_file": ("path", "edit"),
    "list_dir": ("path", "list"),
}

_ACTIVITY_FILE = "activity.md"


class MemoryTracker:
    """Maintains `<memory_dir>/activity.md`: current task, files touched,
    and tool usage counts. Overwritten on every update, so it always
    reflects the most recent session -- a quick "what was I doing" check,
    readable by a human, the CLI, or the model itself via the memory tool.
    """

    def __init__(self, memory_dir: str, session_id: str) -> None:
        self.memory_dir = memory_dir
        self.session_id = session_id
        self.task: str = ""
        self.files_touched: dict[str, set[str]] = {}
        self.tool_counts: Counter = Counter()
        os.makedirs(memory_dir, exist_ok=True)

    def set_task(self, task: str) -> None:
        self.task = task
        self._save()

    def log(self, kind: str, *details) -> None:
        if kind != "tool_call":
            return
        name, arguments = details
        self.tool_counts[name] += 1
        entry = _FILE_ARG_TOOLS.get(name)
        if entry and isinstance(arguments, dict):
            arg_name, action = entry
            if arg_name in arguments:
                path = str(arguments[arg_name])
                self.files_touched.setdefault(path, set()).add(action)
        self._save()

    def summary(self) -> str:
        lines = [f"# Session activity -- {self.session_id}", "", "## Current task", self.task or "(none yet)", ""]

        lines.append("## Files touched")
        if self.files_touched:
            for path in sorted(self.files_touched):
                lines.append(f"- {path} ({', '.join(sorted(self.files_touched[path]))})")
        else:
            lines.append("(none yet)")
        lines.append("")

        lines.append("## Tool usage")
        if self.tool_counts:
            for name, count in self.tool_counts.most_common():
                lines.append(f"- {name}: {count}")
        else:
            lines.append("(none yet)")

        return "\n".join(lines) + "\n"

    def _save(self) -> None:
        try:
            with open(os.path.join(self.memory_dir, _ACTIVITY_FILE), "w", encoding="utf-8") as f:
                f.write(self.summary())
        except Exception:
            pass  # bookkeeping must never break a run
