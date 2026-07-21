"""Demo web backend: a thin FastAPI layer over the existing harness.

Imports the harness directly (no packaging). Reuses its storage, auth, and
Orchestrator; adds a demo-shaped typed SSE event stream (token /
tool_call_started / tool_call_finished / model_info / assistant_message /
done / error) that the React frontend renders as live chat + tool cards.

Run:  python -m server.app     (or via ./demo.sh)
"""

import asyncio
import json
import os
import queue
import re
import threading
from dataclasses import dataclass, replace
from datetime import datetime

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

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
from engine.mcp_client import MCPManager, MCPServerConfig
from engine.orchestrator import Orchestrator
from engine.registry import registry
from observability.usage_store import PersistentUsageTracker
from providers.factory import build_provider
from providers.model_info import effective_context_budget, effective_max_tokens
from server.demo_provider import DemoProvider
from storage.db import make_engine
from storage.models import MCPServer, ROLE_ADMIN, Session as SessionRow, UsageLog, User
from storage.session_store import DbSessionStore
from storage.user_store import DbUserStore

# Models offered in the dropdown. "demo/scripted" always works with no key
# (rehearsal + offline fallback); the rest need HARNESS_API_KEY / a provider.
DEMO_MODELS = [
    "demo/scripted",
    "anthropic/claude-opus-4-8",
    "anthropic/claude-haiku-4-5-20251001",
    "openai/gpt-4o",
    "groq/llama-3.3-70b-versatile",
    "gemini/gemini-2.0-flash",
    "ollama/llama3",
]

# Force the fake provider regardless of model (offline demo / rehearsal).
FAKE_ALL = os.getenv("HARNESS_DEMO_FAKE") in ("1", "true", "yes")

_TOOL_CAP = 2000
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per file
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


class _Cancelled(Exception):
    """Raised inside the agent loop (via on_event) to stop a turn on request."""


def _safe_filename(name: str) -> str:
    """Strip any path and confine to a safe charset, so an upload can only
    land as a plain file inside the session workspace (no traversal)."""
    base = os.path.basename(name or "").strip()
    base = _UNSAFE_NAME.sub("_", base)
    base = base.lstrip(".") or "upload"  # no hidden or ".."-style names
    return base[:120]


_MCP_TRANSPORTS = ("http", "sse", "stdio")


def _mcp_config_from_row(row: "MCPServer") -> MCPServerConfig:
    return MCPServerConfig(
        name=row.name, transport=row.transport,
        command=row.command or None,
        args=json.loads(row.args_json or "[]"),
        env=json.loads(row.env_json or "{}") or None,
        url=row.url or None,
        risk=row.risk or None,
    )


def _mcp_row_public(row: "MCPServer") -> dict:
    """Config summary safe to show in the admin UI (no env secrets)."""
    return {
        "name": row.name, "transport": row.transport,
        "command": row.command, "args": json.loads(row.args_json or "[]"),
        "url": row.url, "risk": row.risk,
    }


class Login(BaseModel):
    username: str
    password: str


class NewSession(BaseModel):
    model: str = "demo/scripted"


class Message(BaseModel):
    message: str
    model: str | None = None  # optional per-turn model switch


class NewUser(BaseModel):
    username: str
    password: str
    role: str = "user"  # "user" or "admin"


class RoleUpdate(BaseModel):
    role: str  # "user" or "admin"


class NewMCPServer(BaseModel):
    name: str
    transport: str = "http"  # "http" | "sse" | "stdio"
    url: str = ""            # http / sse
    command: str = ""        # stdio
    args: list[str] = []     # stdio
    env: dict[str, str] = {}  # stdio
    risk: str = ""           # optional override: safe | write | dangerous


@dataclass
class Principal:
    user_id: int
    role: str


def _blocked_or_error(result: str):
    """Classify a tool result string for the UI (money-shot red state)."""
    low = result.lower()
    blocked = ("escapes the workspace" in low or "blocked by permission" in low
               or "denied by the user" in low or "action denied" in low)
    error = result.startswith("Error") or "exit code: 1" in result
    return blocked, (error and not blocked)


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.load()
    db_engine = make_engine(config.db_url)
    users = DbUserStore(db_engine)
    jwt_secret = load_or_create_secret(config.jwt_secret_path)
    # Remember each session's chosen model (demo: in-memory is fine).
    session_models: dict[str, str] = {}
    # (user_id, sid) pairs whose current turn has been asked to stop.
    cancel_flags: set = set()

    # MCP: one manager owns the live connections; its tools register onto the
    # SAME global `registry` every turn's orchestrator reads, so an admin-added
    # server's tools are available to all sessions (org-wide, like mcp.json).
    # Last connect error per server name, surfaced in GET /admin/mcp.
    mcp_manager = MCPManager(registry)
    mcp_errors: dict[str, str] = {}

    # Env-aware model dropdown. When a real model is configured via
    # HARNESS_MODEL and we're NOT in offline mode, offer it and make it the
    # default -- so the demo talks to that provider using HARNESS_API_KEY /
    # HARNESS_BASE_URL from the environment. "demo/scripted" always stays in
    # the list as an on-stage safety switch (deterministic, no network).
    env_model = os.getenv("HARNESS_MODEL")
    real_model = env_model if (env_model and not FAKE_ALL) else None
    models_list = list(DEMO_MODELS)
    if real_model and real_model not in models_list:
        models_list.insert(1, real_model)  # after demo/scripted
    default_model = real_model or "demo/scripted"

    # Seed demo accounts.
    for uname, pw in (("alice", "alice123"), ("bob", "bob123")):
        if not users.exists(uname):
            users.register(uname, pw)

    def _connect_mcp(config: MCPServerConfig) -> dict:
        """Connect (or reconnect) one MCP server, recording any failure so the
        admin UI can show it. Never raises — a down server must not take the
        app (or a startup) with it."""
        try:
            tools = mcp_manager.connect(config)
            mcp_errors.pop(config.name, None)
            return {"connected": True, "tools": tools, "error": None}
        except Exception as exc:  # noqa: BLE001 -- surfaced to the admin, not fatal
            mcp_errors[config.name] = str(exc)
            return {"connected": False, "tools": [], "error": str(exc)}

    # Reconnect any admin-configured MCP servers from previous runs.
    from sqlalchemy import select as _sa_select
    from sqlalchemy.orm import Session as _OrmSession
    with _OrmSession(db_engine) as _db:
        for _row in _db.execute(_sa_select(MCPServer)).scalars().all():
            _connect_mcp(_mcp_config_from_row(_row))

    app = FastAPI(title="Agent Harness — Demo API")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
        allow_headers=["*"], allow_credentials=False,
    )
    bearer = HTTPBearer(auto_error=True)

    def principal(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> Principal:
        try:
            claims = verify_token(creds.credentials, jwt_secret)
        except TokenError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}")
        return Principal(user_id=claims["user_id"], role=claims["role"])

    def _username(user_id: int) -> str:
        from sqlalchemy import select
        from sqlalchemy.orm import Session as OrmSession
        from storage.models import User
        with OrmSession(db_engine) as db:
            name = db.scalar(select(User.username).where(User.id == user_id))
        if name is None:
            raise HTTPException(401, "unknown user")
        return name

    def _provider_for(model: str):
        if FAKE_ALL or model.startswith("demo/"):
            return DemoProvider(model=model)
        return build_provider(replace(
            config, model=model,
            max_tokens=effective_max_tokens(model, config.max_tokens),
        ))

    # --- auth -------------------------------------------------------------

    @app.post("/auth/login")
    def login(body: Login):
        if not users.verify(body.username, body.password):
            raise HTTPException(401, "invalid username or password")
        uid = users.user_id(body.username)
        role = users.role(body.username) or "user"
        token = issue_token(uid, role, jwt_secret, ttl_s=config.jwt_ttl_s)
        return {"access_token": token, "user_id": uid, "username": body.username, "role": role}

    @app.get("/models")
    def models(_p: Principal = Depends(principal)):
        return {"models": models_list, "default": default_model}

    # --- MCP tool servers (admin-configured, org-wide) --------------------

    @app.get("/admin/mcp")
    def list_mcp(p: Principal = Depends(principal)):
        _require_admin(p)
        from sqlalchemy import select
        from sqlalchemy.orm import Session as OrmSession
        connected = mcp_manager.list_connected()  # name -> tool names
        with OrmSession(db_engine) as db:
            rows = db.execute(select(MCPServer).order_by(MCPServer.name)).scalars().all()
            out = []
            for row in rows:
                info = _mcp_row_public(row)
                info["tools"] = connected.get(row.name, [])
                info["connected"] = row.name in connected
                info["error"] = mcp_errors.get(row.name)
                out.append(info)
        return out

    @app.post("/admin/mcp", status_code=201)
    def create_mcp(body: NewMCPServer, p: Principal = Depends(principal)):
        _require_admin(p)
        name = _safe_filename(body.name).strip()
        if not name:
            raise HTTPException(400, "name is required")
        transport = body.transport if body.transport in _MCP_TRANSPORTS else "http"
        if transport in ("http", "sse") and not body.url.strip():
            raise HTTPException(400, f"{transport} transport needs a url")
        if transport == "stdio" and not body.command.strip():
            raise HTTPException(400, "stdio transport needs a command")

        from sqlalchemy.orm import Session as OrmSession
        with OrmSession(db_engine) as db:
            db.merge(MCPServer(
                name=name, transport=transport,
                command=body.command.strip(),
                args_json=json.dumps(body.args or []),
                env_json=json.dumps(body.env or {}),
                url=body.url.strip(),
                risk=body.risk.strip(),
            ))
            db.commit()
            row = db.get(MCPServer, name)
            # Connect now so the admin sees success/failure immediately. A
            # failure is reported (not raised) — the config stays saved so it
            # can retry after the server is reachable.
            status_info = _connect_mcp(_mcp_config_from_row(row))
            result = _mcp_row_public(row)
        result.update(status_info)
        return result

    @app.delete("/admin/mcp/{name}", status_code=204)
    def delete_mcp(name: str, p: Principal = Depends(principal)):
        _require_admin(p)
        mcp_manager.disconnect(name)
        mcp_errors.pop(name, None)
        from sqlalchemy import delete as sa_delete
        from sqlalchemy.orm import Session as OrmSession
        with OrmSession(db_engine) as db:
            db.execute(sa_delete(MCPServer).where(MCPServer.name == name))
            db.commit()

    # --- sessions ---------------------------------------------------------

    @app.get("/sessions")
    def list_sessions(p: Principal = Depends(principal)):
        store = DbSessionStore(db_engine, p.user_id)
        return [{"session_id": sid, "model": session_models.get(sid, "demo/scripted")}
                for sid in store.list_ids()]

    @app.post("/sessions")
    def create_session(body: NewSession, p: Principal = Depends(principal)):
        sid = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        username = _username(p.user_id)
        store = DbSessionStore(db_engine, p.user_id)
        store.save(sid, Conversation(config.for_user(username).system_prompt))
        session_models[sid] = body.model
        return {"session_id": sid, "model": body.model}

    @app.delete("/sessions/{sid}", status_code=204)
    def delete_session(sid: str, p: Principal = Depends(principal)):
        store = DbSessionStore(db_engine, p.user_id)
        if not store.delete(sid):
            raise HTTPException(404, "no such session")
        session_models.pop(sid, None)

    # --- files: upload into (and list) the session's own workspace ---------

    def _session_workspace(username: str, sid: str) -> str:
        # Same per-user, per-session directory the agent's tools are confined
        # to (see the streamed turn), so an upload here is immediately
        # readable by read_file / list_dir and nothing else can reach it.
        return os.path.join(config.for_user(username).workspace_dir, sid)

    def _own_session_or_404(sid: str, user_id: int) -> None:
        if sid not in DbSessionStore(db_engine, user_id).list_ids():
            raise HTTPException(404, "no such session")

    @app.post("/sessions/{sid}/files")
    async def upload_files(sid: str, p: Principal = Depends(principal),
                           files: list[UploadFile] = File(...)):
        _own_session_or_404(sid, p.user_id)
        wsdir = _session_workspace(_username(p.user_id), sid)
        os.makedirs(wsdir, exist_ok=True)
        saved = []
        for uf in files:
            data = await uf.read()
            if len(data) > _MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"{uf.filename}: too large (max 25 MB)")
            name = _safe_filename(uf.filename)
            with open(os.path.join(wsdir, name), "wb") as f:
                f.write(data)
            saved.append({"name": name, "size": len(data)})
        return {"session_id": sid, "files": saved}

    @app.get("/sessions/{sid}/files")
    def list_files(sid: str, p: Principal = Depends(principal)):
        _own_session_or_404(sid, p.user_id)
        wsdir = _session_workspace(_username(p.user_id), sid)
        out = []
        if os.path.isdir(wsdir):
            for n in sorted(os.listdir(wsdir)):
                fp = os.path.join(wsdir, n)
                if os.path.isfile(fp):
                    out.append({"name": n, "size": os.path.getsize(fp)})
        return {"session_id": sid, "files": out}

    @app.get("/sessions/{sid}/files/{name}")
    def download_file(sid: str, name: str, p: Principal = Depends(principal)):
        _own_session_or_404(sid, p.user_id)
        safe = _safe_filename(name)  # strip any path -> stays inside the workspace
        path = os.path.join(_session_workspace(_username(p.user_id), sid), safe)
        if not os.path.isfile(path):
            raise HTTPException(404, "no such file")
        return FileResponse(path, filename=safe)

    @app.get("/sessions/{sid}/messages")
    def history(sid: str, p: Principal = Depends(principal)):
        store = DbSessionStore(db_engine, p.user_id)
        conv = Conversation("")
        if not store.load(sid, conv):
            raise HTTPException(404, "no such session")
        # Return a UI-friendly transcript (user + assistant text turns).
        out = []
        for m in conv.messages:
            role = m.get("role")
            if role == "user":
                out.append({"role": "user", "text": m.get("content", "")})
            elif role == "assistant" and (m.get("content") or "").strip():
                out.append({"role": "assistant", "text": m.get("content", ""),
                            "model": session_models.get(sid, "demo/scripted")})
        return {"session_id": sid, "model": session_models.get(sid, "demo/scripted"), "messages": out}

    # --- the streamed turn ------------------------------------------------

    @app.post("/sessions/{sid}/messages")
    async def post_message(sid: str, body: Message, p: Principal = Depends(principal)):
        store = DbSessionStore(db_engine, p.user_id)
        if sid not in store.list_ids():
            raise HTTPException(404, "no such session")
        username = _username(p.user_id)
        if body.model:
            session_models[sid] = body.model
        model = session_models.get(sid, "demo/scripted")

        loop = asyncio.get_running_loop()
        q: "queue.Queue" = queue.Queue()
        DONE = object()
        tid = [0]  # rolling id: tool_call/tool_result strictly alternate per tool

        def emit(event: str, data: dict):
            loop.call_soon_threadsafe(q.put_nowait, (event, data))

        def on_event(kind, *details):
            # Cooperative stop: if a /cancel came in, bail out at the next
            # event boundary (token or tool step) instead of running to the end.
            if (p.user_id, sid) in cancel_flags:
                raise _Cancelled()
            if kind == "token":
                emit("token", {"delta": details[0]})
            elif kind == "tool_call":
                tid[0] += 1
                emit("tool_call_started", {"id": tid[0], "name": details[0], "input": details[1]})
            elif kind == "tool_result":
                result = details[1]
                blocked, error = _blocked_or_error(result)
                out = result if len(result) <= _TOOL_CAP else result[:_TOOL_CAP] + " …[truncated]"
                emit("tool_call_finished", {"id": tid[0], "name": details[0],
                                            "output": out, "blocked": blocked, "error": error})
            elif kind == "denied":
                emit("tool_call_finished", {"id": tid[0], "name": details[0],
                                            "output": "Action denied by policy.", "blocked": True, "error": False})

        def work():
            cancel_flags.discard((p.user_id, sid))  # clear any stale request
            try:
                user_config = config.for_user(username)
                # Per-request isolation roots (own thread context -> no leakage).
                context_engine.memory_tool.set_memory_root(user_config.memory_dir)
                engine.builtin.offload.set_offload_root(user_config.offload_dir)
                engine.workspace.set_workspace_root(f"{user_config.workspace_dir}/{sid}")
                provider = _provider_for(model)
                conv = Conversation(
                    user_config.system_prompt,
                    max_context_tokens=effective_context_budget(model, user_config.max_context_tokens),
                    keep_recent_messages=user_config.keep_recent_messages,
                    summarizer=make_provider_summarizer(provider),
                )
                store.load(sid, conv)
                # Persist per-call usage so the admin dashboard has real
                # token/cost numbers attributed to this user + session.
                usage = PersistentUsageTracker(
                    db_engine, p.user_id, session_id_fn=lambda: sid,
                    task_fn=lambda: body.message,
                )
                agent = Orchestrator(
                    provider, registry, replace(user_config, permission_mode="allowlist"),
                    approver=lambda c, t: True, on_event=on_event,
                    conversation=conv, usage_tracker=usage, stream=True,
                )
                answer = agent.run(body.message)
                store.save(sid, conv)
                emit("assistant_message", {"text": answer, "model": model})
            except _Cancelled:
                # Stopped by the user: end the stream cleanly, discard the
                # partial turn (don't save), don't surface it as an error.
                emit("stopped", {})
            except Exception as exc:  # surface to the UI, don't crash the stream
                emit("error", {"message": str(exc)})
            finally:
                cancel_flags.discard((p.user_id, sid))
                loop.call_soon_threadsafe(q.put_nowait, DONE)

        threading.Thread(target=work, daemon=True).start()

        async def sse():
            yield _fmt("model_info", {"model": model})  # so the chip is right from token 1
            while True:
                item = await loop.run_in_executor(None, q.get)
                if item is DONE:
                    yield _fmt("done", {})
                    return
                event, data = item
                yield _fmt(event, data)

        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/sessions/{sid}/cancel", status_code=204)
    def cancel_turn(sid: str, p: Principal = Depends(principal)):
        # Flag the running turn to stop at its next step (see on_event).
        cancel_flags.add((p.user_id, sid))

    # --- admin (role=admin only) -----------------------------------------

    def _require_admin(p: Principal):
        if p.role != ROLE_ADMIN:
            raise HTTPException(403, "admin only")

    @app.get("/admin/stats")
    def admin_stats(p: Principal = Depends(principal)):
        _require_admin(p)
        return _collect_stats(db_engine)

    @app.post("/admin/users", status_code=201)
    def admin_create_user(body: NewUser, p: Principal = Depends(principal)):
        _require_admin(p)
        uname = body.username.strip()
        if not uname or not body.password:
            raise HTTPException(400, "username and password are required")
        if users.exists(uname):
            raise HTTPException(409, "user already exists")
        uid = users.register(uname, body.password)
        if body.role == ROLE_ADMIN:
            _set_role(db_engine, uname, ROLE_ADMIN)
        return {"username": uname, "user_id": uid, "role": body.role}

    @app.patch("/admin/users/{username}/role")
    def admin_set_role(username: str, body: RoleUpdate, p: Principal = Depends(principal)):
        _require_admin(p)
        if body.role not in ("user", ROLE_ADMIN):
            raise HTTPException(400, "role must be 'user' or 'admin'")
        if not users.exists(username):
            raise HTTPException(404, "no such user")
        # Don't allow demoting the last admin (would lock everyone out).
        if body.role == "user" and _admin_count(db_engine) <= 1 and users.role(username) == ROLE_ADMIN:
            raise HTTPException(409, "cannot demote the last admin")
        _set_role(db_engine, username, body.role)
        return {"username": username, "role": body.role}

    @app.delete("/admin/users/{username}", status_code=204)
    def admin_delete_user(username: str, p: Principal = Depends(principal)):
        _require_admin(p)
        if username == _username(p.user_id):
            raise HTTPException(409, "you cannot delete your own account")
        if not users.exists(username):
            raise HTTPException(404, "no such user")
        if users.role(username) == ROLE_ADMIN and _admin_count(db_engine) <= 1:
            raise HTTPException(409, "cannot delete the last admin")
        _delete_user(db_engine, username)

    @app.get("/health")
    def health():
        return {"status": "ok", "fake": FAKE_ALL, "models": models_list, "default": default_model}

    return app


def _fmt(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# --- admin data access (module-level so it's easy to test) ----------------

def _collect_stats(engine) -> dict:
    """Per-user rows (sessions, messages, model calls, tokens, cost) plus a
    global totals row. Lists every user, including those with no activity."""
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as db:
        users = list(db.execute(select(User.id, User.username, User.role)))

        # sessions + message counts per user (parse each snapshot once)
        sessions = {}   # user_id -> session count
        messages = {}   # user_id -> user+assistant message count
        for uid, snap in db.execute(select(SessionRow.user_id, SessionRow.snapshot_json)):
            sessions[uid] = sessions.get(uid, 0) + 1
            try:
                msgs = json.loads(snap).get("messages", [])
                messages[uid] = messages.get(uid, 0) + sum(
                    1 for m in msgs if m.get("role") in ("user", "assistant")
                )
            except Exception:
                pass

        # token / cost / call counts per user from the usage log
        usage_rows = db.execute(
            select(
                UsageLog.user_id,
                func.count(UsageLog.id),
                func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(UsageLog.completion_tokens), 0),
                func.coalesce(func.sum(UsageLog.cost_usd), 0.0),
            ).group_by(UsageLog.user_id)
        )
        usage = {r[0]: (r[1], r[2], r[3], r[4]) for r in usage_rows}

    rows, totals = [], {"sessions": 0, "messages": 0, "calls": 0,
                        "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    for uid, username, role in users:
        calls, pt, ct, cost = usage.get(uid, (0, 0, 0, 0.0))
        row = {
            "username": username, "role": role,
            "sessions": sessions.get(uid, 0),
            "messages": messages.get(uid, 0),
            "calls": calls,
            "prompt_tokens": pt, "completion_tokens": ct,
            "total_tokens": pt + ct, "cost_usd": round(cost, 4),
        }
        rows.append(row)
        for k in ("sessions", "messages", "calls", "prompt_tokens", "completion_tokens"):
            totals[k] += row[k]
        totals["cost_usd"] += cost
    rows.sort(key=lambda r: (r["total_tokens"], r["sessions"]), reverse=True)
    totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    totals["cost_usd"] = round(totals["cost_usd"], 4)
    totals["users"] = len(rows)
    return {"users": rows, "totals": totals}


def _admin_count(engine) -> int:
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session as OrmSession
    with OrmSession(engine) as db:
        return db.scalar(select(func.count(User.id)).where(User.role == ROLE_ADMIN)) or 0


def _set_role(engine, username: str, role: str) -> None:
    from sqlalchemy import update
    from sqlalchemy.orm import Session as OrmSession
    with OrmSession(engine) as db:
        db.execute(update(User).where(User.username == username).values(role=role))
        db.commit()


def _delete_user(engine, username: str) -> None:
    """Remove a user and their sessions + usage rows."""
    from sqlalchemy import delete, select
    from sqlalchemy.orm import Session as OrmSession
    with OrmSession(engine) as db:
        uid = db.scalar(select(User.id).where(User.username == username))
        if uid is None:
            return
        db.execute(delete(UsageLog).where(UsageLog.user_id == uid))
        db.execute(delete(SessionRow).where(SessionRow.user_id == uid))
        db.execute(delete(User).where(User.id == uid))
        db.commit()


app = create_app()


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
