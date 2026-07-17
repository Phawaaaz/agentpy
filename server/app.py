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
import threading
from dataclasses import dataclass, replace
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
from engine.orchestrator import Orchestrator
from engine.registry import registry
from observability.usage import UsageTracker
from providers.factory import build_provider
from providers.model_info import effective_context_budget, effective_max_tokens
from server.demo_provider import DemoProvider
from storage.db import make_engine
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


class Login(BaseModel):
    username: str
    password: str


class NewSession(BaseModel):
    model: str = "demo/scripted"


class Message(BaseModel):
    message: str
    model: str | None = None  # optional per-turn model switch


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

    # Seed demo accounts.
    for uname, pw in (("alice", "alice123"), ("bob", "bob123")):
        if not users.exists(uname):
            users.register(uname, pw)

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
        return {"models": DEMO_MODELS, "default": "demo/scripted"}

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
                agent = Orchestrator(
                    provider, registry, replace(user_config, permission_mode="allowlist"),
                    approver=lambda c, t: True, on_event=on_event,
                    conversation=conv, usage_tracker=UsageTracker(), stream=True,
                )
                answer = agent.run(body.message)
                store.save(sid, conv)
                emit("assistant_message", {"text": answer, "model": model})
            except Exception as exc:  # surface to the UI, don't crash the stream
                emit("error", {"message": str(exc)})
            finally:
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

    @app.get("/health")
    def health():
        return {"status": "ok", "fake": FAKE_ALL, "models": DEMO_MODELS}

    return app


def _fmt(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


app = create_app()


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
