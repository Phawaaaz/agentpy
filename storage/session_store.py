"""Database-backed session persistence (D29) -- replaces the JSON-file
context_engine SessionStore behind the same save/load/list_ids interface,
keyed by (user_id, session_id) so no query can ever cross users, plus the
delete() the JSON store never had.

The conversation still travels as one Conversation.snapshot() JSON blob --
the shape is identical to what the file store wrote, which is what makes
the migration script an exact copy rather than a transformation.
"""

import json
import time

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from context_engine.compaction import Conversation

from .models import Session as SessionRow


def _safe_id(session_id: str) -> str:
    # Same sanitization the file store applied to filenames -- kept so ids
    # remain interchangeable between the two backends.
    return "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))


class DbSessionStore:
    """Save/load/list/delete one user's conversations. Bound to a user_id
    at construction so callers cannot accidentally reach across users."""

    def __init__(self, engine: Engine, user_id: int) -> None:
        self.engine = engine
        self.user_id = user_id

    def save(self, session_id: str, conversation: Conversation) -> str:
        sid = _safe_id(session_id)
        snapshot = json.dumps(conversation.snapshot())
        now = time.time()
        with OrmSession(self.engine) as db:
            row = db.get(SessionRow, (sid, self.user_id))
            if row is None:
                row = SessionRow(
                    id=sid, user_id=self.user_id, snapshot_json=snapshot,
                    created_at=now, updated_at=now,
                )
                db.add(row)
            else:
                row.snapshot_json = snapshot
                row.updated_at = now
            db.commit()
        return f"session '{sid}'"

    def load(self, session_id: str, into: Conversation) -> bool:
        """Restore a saved session into `into`. Returns False if not found."""
        with OrmSession(self.engine) as db:
            row = db.get(SessionRow, (_safe_id(session_id), self.user_id))
        if row is None:
            return False
        into.restore(json.loads(row.snapshot_json))
        return True

    def list_ids(self) -> list[str]:
        with OrmSession(self.engine) as db:
            return sorted(
                db.scalars(select(SessionRow.id).where(SessionRow.user_id == self.user_id))
            )

    def delete(self, session_id: str) -> bool:
        """Remove a saved session. Returns False if it didn't exist."""
        with OrmSession(self.engine) as db:
            row = db.get(SessionRow, (_safe_id(session_id), self.user_id))
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True
