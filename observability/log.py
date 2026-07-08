"""EventLogger — append structured events to a per-session JSONL file.

A thin, append-only trace of what the agent did (model calls, tool calls,
results, denials). Useful for debugging and audit. It is deliberately dumb: it
writes lines, nothing more. Interfaces compose it with their own display.
"""

import json
import os
import time


class EventLogger:
    def __init__(self, directory: str, session_id: str) -> None:
        os.makedirs(directory, exist_ok=True)
        safe = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
        self.path = os.path.join(directory, f"{safe}.jsonl")

    def log(self, kind: str, **fields) -> None:
        record = {"ts": time.time(), "kind": kind, **fields}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception:
            # Logging must never break a run.
            pass
