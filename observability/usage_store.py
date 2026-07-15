"""Durable, per-user usage accounting (D30) -- the admin-monitoring half of
"who is spending tokens, how many, on what."

PersistentUsageTracker extends the in-memory UsageTracker (which keeps
powering /cost exactly as before): every record() additionally inserts one
usage_log row -- user, session, model, tokens in/out, estimated cost, and
the task text current at that moment. Inserts are best-effort: accounting
must never break a run (same rule as EventLogger/MemoryTracker), so a
database hiccup is swallowed and the in-memory totals still update.

The aggregation queries below are what the CLI's admin-only /usage
commands print -- and, verbatim, what a future server's admin endpoints
would serve.
"""

from typing import Callable

from sqlalchemy import Engine, desc, func, select
from sqlalchemy.orm import Session as OrmSession

from providers.base import Usage
from storage.models import UsageLog, User

from .usage import UsageTracker, cost_for


class PersistentUsageTracker(UsageTracker):
    """A UsageTracker that also writes one usage_log row per model call.

    `session_id_fn`/`task_fn` are callables (not values) because both
    change over a CLI run (/new, /load, each new task) -- they're read at
    record time, so every row is attributed to the session and task that
    were actually current."""

    def __init__(
        self,
        engine: Engine,
        user_id: int,
        session_id_fn: Callable[[], str],
        task_fn: Callable[[], str],
    ) -> None:
        super().__init__()
        self._engine = engine
        self._user_id = user_id
        self._session_id_fn = session_id_fn
        self._task_fn = task_fn

    def record(self, model: str, usage: Usage | None) -> None:
        super().record(model, usage)
        if usage is None:
            return
        try:
            with OrmSession(self._engine) as db:
                db.add(
                    UsageLog(
                        user_id=self._user_id,
                        session_id=self._session_id_fn(),
                        model=model,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        cost_usd=cost_for(model, usage),
                        task=self._task_fn(),
                    )
                )
                db.commit()
        except Exception:
            pass  # accounting must never break a run


def usage_by_user(engine: Engine) -> list[dict]:
    """Per-user totals, biggest spender first: the admin /usage overview."""
    stmt = (
        select(
            User.username,
            func.count(UsageLog.id).label("calls"),
            func.coalesce(func.sum(UsageLog.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(UsageLog.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(UsageLog.cost_usd), 0.0).label("cost_usd"),
        )
        .join(UsageLog, UsageLog.user_id == User.id)
        .group_by(User.username)
        .order_by(desc("cost_usd"))
    )
    with OrmSession(engine) as db:
        return [dict(row._mapping) for row in db.execute(stmt)]


def usage_for_user(engine: Engine, username: str) -> list[dict]:
    """One user's per-session totals with the most recent task text --
    the admin /usage <username> drill-down."""
    stmt = (
        select(
            UsageLog.session_id,
            func.count(UsageLog.id).label("calls"),
            func.coalesce(func.sum(UsageLog.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(UsageLog.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(UsageLog.cost_usd), 0.0).label("cost_usd"),
            func.max(UsageLog.created_at).label("last_call_at"),
        )
        .join(User, UsageLog.user_id == User.id)
        .where(User.username == username)
        .group_by(UsageLog.session_id)
        .order_by(desc("last_call_at"))
    )
    with OrmSession(engine) as db:
        rows = [dict(row._mapping) for row in db.execute(stmt)]
        # Attach each session's most recent non-empty task.
        for row in rows:
            row["last_task"] = db.scalar(
                select(UsageLog.task)
                .join(User, UsageLog.user_id == User.id)
                .where(User.username == username)
                .where(UsageLog.session_id == row["session_id"])
                .where(UsageLog.task != "")
                .order_by(desc(UsageLog.created_at))
                .limit(1)
            ) or "(no task recorded)"
        return rows
