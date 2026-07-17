"""HTTP API server -- the multi-user interface the harness was built toward.

This is a *thin interface* in exactly the sense the architecture intends
(D7): it captures HTTP input, verifies identity, and reports results, while
all agent logic stays in engine/. It reuses -- unchanged -- the pieces the
milestones made server-ready:

- auth/tokens.py           issue/verify JWTs (per-request enforcement lives here now)
- storage/user_store.py    DbUserStore: accounts + roles
- storage/session_store.py DbSessionStore: sessions keyed by (session_id, user_id)
- config.for_user + D28 ContextVars: per-request memory/offload/workspace isolation
- engine/orchestrator.py   the loop, run once per turn request

Auth is now ENFORCED: every /sessions and /agent route depends on a valid
bearer token, and the user_id it carries is the only key used to reach
storage -- so one user's token cannot touch another user's data.

Run:  uvicorn interfaces.server:app   (after `pip install -r requirements-server.txt`)
or:   python -m interfaces.server
"""

import asyncio
import contextvars
import json
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

import context_engine.memory_tool
import engine.builtin.filesystem  # noqa: F401  (register tools)
import engine.builtin.git_tool  # noqa: F401
import engine.builtin.offload
import engine.builtin.planning  # noqa: F401
import engine.builtin.search_files  # noqa: F401
import engine.builtin.shell  # noqa: F401
import engine.builtin.web  # noqa: F401
import engine.workspace
from auth.tokens import TokenError, issue_token, load_or_create_secret, verify_token
from config import Config
from context_engine.compaction import Conversation, make_provider_summarizer
from engine.orchestrator import Orchestrator
from engine.registry import registry
from observability.usage import UsageTracker
from observability.usage_store import PersistentUsageTracker, usage_by_user, usage_for_user
from providers.factory import build_provider
from providers.model_info import effective_context_budget, effective_max_tokens
from storage.db import make_engine
from storage.models import ROLE_ADMIN
from storage.session_store import DbSessionStore
from storage.user_store import DbUserStore


# --- request/response models ------------------------------------------------

class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    role: str


class SessionInfo(BaseModel):
    session_id: str


class MessageRequest(BaseModel):
    message: str = Field(min_length=1)


class MessageResponse(BaseModel):
    session_id: str
    answer: str
    usage: str


class ApprovalDecision(BaseModel):
    approved: bool


class Identity(BaseModel):
    """Serialized identity (response model for /auth/me)."""
    user_id: int
    role: str


@dataclass
class Principal:
    """The authenticated caller, injected per request. A plain dataclass --
    NOT a Pydantic model -- so FastAPI treats it as a dependency result, not
    a query/body parameter to parse."""
    user_id: int
    role: str


# --- app state (built once) -------------------------------------------------

class _State:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.engine = make_engine(config.db_url)
        self.users = DbUserStore(self.engine)
        self.jwt_secret = load_or_create_secret(config.jwt_secret_path)
        # Pending human-in-the-loop approvals (D34 HITL): approval_id -> record.
        # The worker thread running a turn blocks on the record's Event until a
        # separate HTTP request resolves it (or it times out -> deny).
        self._approvals: dict[str, dict] = {}
        self._approvals_lock = threading.Lock()

    def create_approval(self, user_id, session_id, tool, arguments, risk) -> str:
        import uuid
        aid = uuid.uuid4().hex
        with self._approvals_lock:
            self._approvals[aid] = {
                "event": threading.Event(), "decision": False,
                "user_id": user_id, "session_id": session_id,
                "tool": tool, "arguments": arguments, "risk": risk,
            }
        return aid

    def wait_approval(self, aid: str, timeout: float) -> bool:
        with self._approvals_lock:
            rec = self._approvals.get(aid)
        if rec is None:
            return False
        got = rec["event"].wait(timeout)  # blocks the worker thread
        with self._approvals_lock:
            rec = self._approvals.pop(aid, None)
        return bool(got and rec and rec["decision"])  # timeout -> deny (fail safe)

    def resolve_approval(self, aid: str, user_id: int, approved: bool) -> bool:
        with self._approvals_lock:
            rec = self._approvals.get(aid)
            if rec is None or rec["user_id"] != user_id:  # scoped to the owner
                return False
            rec["decision"] = approved
            rec["event"].set()
            return True

    def pending_approvals(self, user_id: int, session_id: str) -> list[dict]:
        with self._approvals_lock:
            return [
                {"approval_id": aid, "tool": r["tool"],
                 "arguments": r["arguments"], "risk": r["risk"]}
                for aid, r in self._approvals.items()
                if r["user_id"] == user_id and r["session_id"] == session_id
            ]


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.load()
    state = _State(config)
    app = FastAPI(title="Agentic Harness API", version="1.0")
    app.state.harness = state
    bearer = HTTPBearer(auto_error=True)

    def get_identity(
        creds: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
    ) -> Principal:
        """Verify the bearer token on every protected request. This is the
        per-request enforcement the CLI's login could only scaffold."""
        try:
            claims = verify_token(creds.credentials, state.jwt_secret)
        except TokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"invalid token: {exc}",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return Principal(user_id=claims["user_id"], role=claims["role"])

    IdentityDep = Annotated[Principal, Depends(get_identity)]

    def _user_config(username: str) -> Config:
        return state.config.for_user(username)

    def _turn(username, user_id, session_id, message, on_event=None, approver=None, stream=False):
        """Run one agent turn for a specific user+session in an ISOLATED
        execution context (D28): the memory/offload/workspace roots are set
        as ContextVars inside a copied context, so concurrent requests for
        different users never see each other's state. `on_event`, if given,
        receives the orchestrator's live events (used for SSE streaming)."""
        user_config = _user_config(username)
        store = DbSessionStore(state.engine, user_id)

        def turn():
            context_engine.memory_tool.set_memory_root(user_config.memory_dir)
            engine.builtin.offload.set_offload_root(user_config.offload_dir)
            if user_config.confine_workspace:
                engine.workspace.set_workspace_root(
                    f"{user_config.workspace_dir}/{_safe(session_id)}"
                )
            else:
                engine.workspace.set_workspace_root(None)

            provider = build_provider(
                replace(user_config, max_tokens=effective_max_tokens(user_config.model, user_config.max_tokens))
            )
            conversation = Conversation(
                user_config.system_prompt,
                max_context_tokens=effective_context_budget(user_config.model, user_config.max_context_tokens),
                keep_recent_messages=user_config.keep_recent_messages,
                summarizer=make_provider_summarizer(provider),
            )
            store.load(session_id, conversation)  # resume if it exists

            usage: UsageTracker = PersistentUsageTracker(
                state.engine, user_id, lambda: session_id, lambda: message
            )
            agent = Orchestrator(
                provider, registry, user_config,
                # Non-streaming turns have no channel to ask a human, so an
                # "ask" decision is denied (fail safe). Streaming turns pass a
                # real approver that surfaces an `approval_required` event and
                # waits for the client to resolve it (HITL, below).
                approver=approver or (lambda call, tool: False),
                on_event=on_event or (lambda *a: None),
                conversation=conversation,
                usage_tracker=usage,
                stream=stream,  # token-level streaming when the caller asks (D35)
            )
            answer = agent.run(message)
            store.save(session_id, conversation)
            return answer, usage.summary()

        return contextvars.copy_context().run(turn)

    def _run_turn(username, user_id, session_id, message) -> MessageResponse:
        answer, usage = _turn(username, user_id, session_id, message)
        return MessageResponse(session_id=session_id, answer=answer, usage=usage)

    # --- auth -------------------------------------------------------------

    @app.post("/auth/register", response_model=TokenResponse, status_code=201)
    def register(creds: Credentials) -> TokenResponse:
        try:
            user_id = state.users.register(creds.username, creds.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        role = state.users.role(creds.username) or "user"
        token = issue_token(user_id, role, state.jwt_secret, ttl_s=state.config.jwt_ttl_s)
        return TokenResponse(access_token=token, user_id=user_id, role=role)

    @app.post("/auth/login", response_model=TokenResponse)
    def login(creds: Credentials) -> TokenResponse:
        if not state.users.verify(creds.username, creds.password):
            raise HTTPException(status_code=401, detail="invalid username or password")
        user_id = state.users.user_id(creds.username)
        role = state.users.role(creds.username) or "user"
        token = issue_token(user_id, role, state.jwt_secret, ttl_s=state.config.jwt_ttl_s)
        return TokenResponse(access_token=token, user_id=user_id, role=role)

    @app.get("/auth/me", response_model=Identity)
    def me(identity: IdentityDep) -> Identity:
        return Identity(user_id=identity.user_id, role=identity.role)

    # --- sessions ---------------------------------------------------------

    def _username_for(user_id: int) -> str:
        # user_id -> username, needed to build per-user config paths.
        from sqlalchemy import select
        from sqlalchemy.orm import Session as OrmSession
        from storage.models import User
        with OrmSession(state.engine) as db:
            name = db.scalar(select(User.username).where(User.id == user_id))
        if name is None:
            raise HTTPException(status_code=401, detail="unknown user")
        return name

    @app.get("/sessions", response_model=list[SessionInfo])
    def list_sessions(identity: IdentityDep) -> list[SessionInfo]:
        store = DbSessionStore(state.engine, identity.user_id)
        return [SessionInfo(session_id=sid) for sid in store.list_ids()]

    @app.post("/sessions", response_model=SessionInfo, status_code=201)
    def create_session(identity: IdentityDep) -> SessionInfo:
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        username = _username_for(identity.user_id)
        store = DbSessionStore(state.engine, identity.user_id)
        # Persist an empty conversation so the session exists to list/resume.
        store.save(session_id, Conversation(_user_config(username).system_prompt))
        return SessionInfo(session_id=session_id)

    @app.delete("/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str, identity: IdentityDep) -> None:
        store = DbSessionStore(state.engine, identity.user_id)
        if not store.delete(session_id):
            raise HTTPException(status_code=404, detail="no such session")
        # Purge the on-disk workspace too (mirrors the CLI's /delete fix).
        _delete_workspace(_user_config(_username_for(identity.user_id)), session_id)

    @app.post("/sessions/{session_id}/messages", response_model=MessageResponse)
    def post_message(session_id: str, body: MessageRequest, identity: IdentityDep) -> MessageResponse:
        store = DbSessionStore(state.engine, identity.user_id)
        if session_id not in store.list_ids():
            raise HTTPException(status_code=404, detail="no such session")
        username = _username_for(identity.user_id)
        return _run_turn(username, identity.user_id, session_id, body.message)

    @app.post("/sessions/{session_id}/messages/stream")
    async def post_message_stream(session_id: str, body: MessageRequest, identity: IdentityDep):
        """Same turn, streamed as Server-Sent Events: the client sees
        `thinking`, `tool_call`, `tool_result`, `usage`, and a final `answer`
        event as they happen, instead of waiting for one blob. The
        synchronous Orchestrator runs in a worker thread; its on_event
        callback bridges events to this request's asyncio loop via a queue."""
        store = DbSessionStore(state.engine, identity.user_id)
        if session_id not in store.list_ids():
            raise HTTPException(status_code=404, detail="no such session")
        username = _username_for(identity.user_id)

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        DONE = object()

        def on_event(kind, *details):
            loop.call_soon_threadsafe(q.put_nowait, (kind, details))

        def approver(tool_call, tool):
            # Surface the pending action to the client and block this worker
            # thread until it's resolved via POST .../approvals/{id} (or times
            # out -> deny). Only reached when the server runs in `ask` mode.
            aid = state.create_approval(identity.user_id, session_id, tool_call.name, tool_call.arguments, tool.risk)
            on_event("approval_required", aid, tool_call.name, tool_call.arguments, tool.risk)
            return state.wait_approval(aid, _APPROVAL_TIMEOUT_S)

        def work():
            try:
                answer, usage = _turn(
                    username, identity.user_id, session_id, body.message,
                    on_event=on_event, approver=approver, stream=True,
                )
                loop.call_soon_threadsafe(q.put_nowait, ("answer", (answer, usage)))
            except Exception as exc:  # surfaced to the client as an error event
                loop.call_soon_threadsafe(q.put_nowait, ("error", (str(exc),)))
            finally:
                loop.call_soon_threadsafe(q.put_nowait, DONE)

        threading.Thread(target=work, daemon=True).start()

        async def sse():
            while True:
                item = await q.get()
                if item is DONE:
                    yield "event: done\ndata: {}\n\n"
                    return
                kind, details = item
                yield f"event: {kind}\ndata: {json.dumps(_event_payload(kind, details))}\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    @app.get("/sessions/{session_id}/approvals")
    def list_approvals(session_id: str, identity: IdentityDep) -> list[dict]:
        """Pending human-in-the-loop approvals for this session (a client can
        poll this instead of, or alongside, reading the SSE stream)."""
        return state.pending_approvals(identity.user_id, session_id)

    @app.post("/sessions/{session_id}/approvals/{approval_id}")
    def resolve_approval(
        session_id: str, approval_id: str, body: ApprovalDecision, identity: IdentityDep
    ) -> dict:
        """Approve or deny a pending action, unblocking the waiting turn.
        Scoped to the owner: another user's token gets 404."""
        if not state.resolve_approval(approval_id, identity.user_id, body.approved):
            raise HTTPException(status_code=404, detail="no such pending approval")
        return {"approval_id": approval_id, "approved": body.approved}

    # --- admin ------------------------------------------------------------

    @app.get("/admin/usage")
    def admin_usage(identity: IdentityDep) -> list[dict]:
        if identity.role != ROLE_ADMIN:
            raise HTTPException(status_code=403, detail="admin only")
        return usage_by_user(state.engine)

    @app.get("/admin/usage/{username}")
    def admin_usage_user(username: str, identity: IdentityDep) -> list[dict]:
        if identity.role != ROLE_ADMIN:
            raise HTTPException(status_code=403, detail="admin only")
        return usage_for_user(state.engine, username)

    # --- ops --------------------------------------------------------------

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "model": state.config.model}

    return app


_SSE_RESULT_CAP = 2_000  # don't flood the stream with a huge tool result
_APPROVAL_TIMEOUT_S = 300  # how long a turn waits for a human decision before denying


def _event_payload(kind: str, details: tuple) -> dict:
    """Map an orchestrator on_event (kind, *details) to a JSON SSE payload.
    Mirrors interfaces/cli.py's event handling, trimmed for the wire."""
    if kind == "token":
        return {"delta": details[0]}
    if kind == "thinking":
        return {"text": details[0]}
    if kind == "tool_call":
        return {"name": details[0], "arguments": details[1]}
    if kind == "tool_result":
        result = details[1]
        if len(result) > _SSE_RESULT_CAP:
            result = result[:_SSE_RESULT_CAP] + " …[truncated]"
        payload = {"name": details[0], "result": result}
        if len(details) > 2:
            payload["duration_ms"] = details[2]
        return payload
    if kind == "approval_required":
        return {"approval_id": details[0], "tool": details[1],
                "arguments": details[2], "risk": details[3]}
    if kind == "denied":
        return {"tool": details[0]}
    if kind == "compacted":
        return {"tokens": details[0]}
    if kind == "usage":
        payload = {"prompt_tokens": details[0], "completion_tokens": details[1]}
        if len(details) > 2:
            payload["duration_ms"] = details[2]
        return payload
    if kind == "answer":
        return {"answer": details[0], "usage": details[1]}
    if kind == "error":
        return {"error": details[0]}
    return {"details": list(details)}


def _safe(session_id: str) -> str:
    return "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))


def _delete_workspace(config: Config, session_id: str) -> None:
    import os
    import shutil
    safe = _safe(session_id)
    if not safe:
        return
    root = os.path.abspath(config.workspace_dir)
    target = os.path.abspath(os.path.join(config.workspace_dir, safe))
    if target != root and target.startswith(root + os.sep) and os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)


# Module-level app for `uvicorn interfaces.server:app`.
app = create_app()


def main() -> None:
    import uvicorn
    config = Config.load()
    uvicorn.run(app, host="0.0.0.0", port=8000)  # noqa: S104 -- container-facing


if __name__ == "__main__":
    main()
