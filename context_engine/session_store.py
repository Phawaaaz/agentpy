"""SessionStore — persist a Conversation to disk as JSON and load it back.

One responsibility: turn a conversation snapshot into a file and back. It does
not know how conversations are used, only how to store them. Backed by the local
filesystem now; the same interface could later wrap a database.
"""

import json
import os

from context_engine.compaction import Conversation


class SessionStore:
    def __init__(self, directory: str) -> None:
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, session_id: str) -> str:
        safe = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
        return os.path.join(self.directory, f"{safe}.json")

    def save(self, session_id: str, conversation: Conversation) -> str:
        path = self._path(session_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(conversation.snapshot(), f, indent=2)
        return path

    def load(self, session_id: str, into: Conversation) -> bool:
        """Restore a saved session into `into`. Returns False if not found."""
        path = self._path(session_id)
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            into.restore(json.load(f))
        return True

    def list_ids(self) -> list[str]:
        if not os.path.isdir(self.directory):
            return []
        return sorted(
            name[:-5] for name in os.listdir(self.directory) if name.endswith(".json")
        )
