"""Persisted pipeline state: one job, like store/session_store.py -- turn a
slice's status into a file and back. `ProgressLog` is the append-only text
trail each fresh iteration reads to reconstruct context, instead of carrying
a growing in-memory conversation across iterations (mirrors Ralph's
progress.log).
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field


@dataclass
class SliceState:
    slice_id: str
    task: str
    branch: str = ""
    worktree_path: str = ""
    stage: str = "implement"
    iteration: int = 0
    repair_attempt: int = 0
    status: str = "running"  # running | complete | aborted | stuck | max_iterations | timeout
    started_at: float = field(default_factory=time.time)

    def save(self, base_dir: str) -> str:
        path = os.path.join(base_dir, self.slice_id, "state.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        return path

    @classmethod
    def load(cls, base_dir: str, slice_id: str) -> "SliceState | None":
        path = os.path.join(base_dir, slice_id, "state.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return cls(**json.load(f))


class ProgressLog:
    def __init__(self, base_dir: str, slice_id: str) -> None:
        self.path = os.path.join(base_dir, slice_id, "progress.log")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def append(self, entry: str) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def read(self) -> str:
        if not os.path.exists(self.path):
            return "(no progress yet)"
        with open(self.path, "r", encoding="utf-8") as f:
            return f.read().strip() or "(no progress yet)"
