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


_TITLE_MAX = 48


def _title_from_snapshot(snapshot_json: str) -> str:
    """A short title from the first user message in a saved snapshot."""
    try:
        messages = json.loads(snapshot_json).get("messages", [])
    except (ValueError, AttributeError):
        return ""
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content") or ""
        if isinstance(content, list):  # vision turns carry content blocks
            content = " ".join(b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text")
        # Drop the injected "[Files are now in your workspace: ...]" note and
        # collapse whitespace so the title reads like what the user typed.
        text = content.split("\n[Files are now in your workspace")[0]
        text = " ".join(text.split()).strip()
        if not text:
            continue
        return text[:_TITLE_MAX] + ("…" if len(text) > _TITLE_MAX else "")
    return ""


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

    def titles(self) -> dict[str, str]:
        """A short human title per session, derived from its first user
        message (so the sidebar reads like the conversation, not a timestamp).
        Empty sessions get "" — the caller can fall back to "New chat"."""
        out: dict[str, str] = {}
        with OrmSession(self.engine) as db:
            rows = db.execute(
                select(SessionRow.id, SessionRow.snapshot_json)
                .where(SessionRow.user_id == self.user_id)
            )
            for sid, snap in rows:
                out[sid] = _title_from_snapshot(snap)
        return out

    def delete(self, session_id: str) -> bool:
        """Remove a saved session. Returns False if it didn't exist."""
        with OrmSession(self.engine) as db:
            row = db.get(SessionRow, (_safe_id(session_id), self.user_id))
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True
