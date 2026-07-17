"""HTTP server tests (interfaces/server.py): auth enforcement, session APIs,
a real turn through the loop (fake provider), cross-user isolation over HTTP,
and admin gating. Uses FastAPI's TestClient -- no network, no API key.
Skips (with a notice) if fastapi isn't installed.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fastapi_available() -> bool:
    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401  (TestClient dependency)
        return True
    except Exception:
        return False


def _run():
    from fastapi.testclient import TestClient

    import interfaces.server as server
    from config import Config
    from providers.base import Provider, Response

    from providers.base import ToolCall

    class FakeProvider(Provider):
        def complete(self, messages, tools):
            # Echo the last user message so the test can assert the turn ran.
            last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
            text = f"handled: {last}"
            return Response(text=text, tool_calls=[], assistant_message={"role": "assistant", "content": text})

    class ToolThenFinalProvider(Provider):
        """First turn: call list_dir; second turn: final answer. Lets the
        streaming test observe tool_call + tool_result + answer events."""
        def __init__(self):
            self._n = 0

        def complete(self, messages, tools):
            self._n += 1
            if self._n == 1:
                return Response(
                    text=None,
                    tool_calls=[ToolCall(id="c1", name="list_dir", arguments={"path": "."})],
                    assistant_message={"role": "assistant", "content": "", "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]},
                )
            return Response(text="all done", tool_calls=[], assistant_message={"role": "assistant", "content": "all done"})

    tmp = tempfile.mkdtemp()
    cfg = Config(
        model="anthropic/claude-opus-4-8",
        db_url=f"sqlite:///{os.path.join(tmp, 'srv.db')}",
        jwt_secret_path=os.path.join(tmp, "jwt_secret"),
        memory_dir=os.path.join(tmp, "memory"),
        offload_dir=os.path.join(tmp, "offload"),
        workspace_dir=os.path.join(tmp, "workspaces"),
        sessions_dir=os.path.join(tmp, "sessions"),
        logs_dir=os.path.join(tmp, "logs"),
    )
    server.build_provider = lambda config: FakeProvider()  # no real model
    app = server.create_app(cfg)
    client = TestClient(app)

    # health is open
    assert client.get("/health").status_code == 200

    # register (first account -> admin), login
    r = client.post("/auth/register", json={"username": "alice", "password": "pw-alice"})
    assert r.status_code == 201, r.text
    alice = r.json()
    assert alice["role"] == "admin" and alice["access_token"]
    a_auth = {"Authorization": f"Bearer {alice['access_token']}"}

    r = client.post("/auth/register", json={"username": "bob", "password": "pw-bob"})
    assert r.status_code == 201
    bob = r.json()
    assert bob["role"] == "user"
    b_auth = {"Authorization": f"Bearer {bob['access_token']}"}

    # login round-trips
    assert client.post("/auth/login", json={"username": "alice", "password": "pw-alice"}).status_code == 200
    assert client.post("/auth/login", json={"username": "alice", "password": "WRONG"}).status_code == 401

    # AUTH ENFORCED: no token / bad token -> 401
    assert client.get("/sessions").status_code == 403 or client.get("/sessions").status_code == 401
    assert client.get("/sessions", headers={"Authorization": "Bearer garbage"}).status_code == 401
    print("  auth enforced: missing/invalid token rejected OK")

    # alice creates a session, lists it, runs a turn
    sid = client.post("/sessions", headers=a_auth).json()["session_id"]
    assert [s["session_id"] for s in client.get("/sessions", headers=a_auth).json()] == [sid]
    turn = client.post(f"/sessions/{sid}/messages", headers=a_auth, json={"message": "ping"})
    assert turn.status_code == 200, turn.text
    assert turn.json()["answer"] == "handled: ping"
    print("  register/login/create-session/run-turn OK")

    # CROSS-USER ISOLATION: bob cannot see, message, or delete alice's session
    assert client.get("/sessions", headers=b_auth).json() == []
    assert client.post(f"/sessions/{sid}/messages", headers=b_auth, json={"message": "x"}).status_code == 404
    assert client.delete(f"/sessions/{sid}", headers=b_auth).status_code == 404
    # alice's session still intact
    assert [s["session_id"] for s in client.get("/sessions", headers=a_auth).json()] == [sid]
    print("  cross-user isolation over HTTP (bob can't reach alice's session) OK")

    # delete removes it (and would purge the workspace dir)
    assert client.delete(f"/sessions/{sid}", headers=a_auth).status_code == 204
    assert client.get("/sessions", headers=a_auth).json() == []
    print("  delete session OK")

    # admin gating: alice (admin) can read usage, bob (user) is 403
    assert client.get("/admin/usage", headers=a_auth).status_code == 200
    assert client.get("/admin/usage", headers=b_auth).status_code == 403
    print("  admin usage endpoint gated to admins OK")

    # STREAMING (SSE): a turn that makes a tool call streams tool_call +
    # tool_result + answer + done events, isolated to alice.
    server.build_provider = lambda config: ToolThenFinalProvider()
    sid2 = client.post("/sessions", headers=a_auth).json()["session_id"]
    with client.stream("POST", f"/sessions/{sid2}/messages/stream",
                       headers=a_auth, json={"message": "list things"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    events = [ln[len("event: "):] for ln in body.splitlines() if ln.startswith("event: ")]
    assert "tool_call" in events, events
    assert "tool_result" in events, events
    assert "answer" in events and events[-1] == "done", events
    assert "all done" in body  # the final answer text made it into the stream

    # TOKEN streaming (D35): a provider that streams deltas -> `token` events
    # carrying incremental text arrive over SSE.
    class StreamingProvider(Provider):
        def complete(self, messages, tools):
            return Response(text="streamed reply", tool_calls=[],
                            assistant_message={"role": "assistant", "content": "streamed reply"})
        def stream(self, messages, tools):
            for piece in ("stream", "ed ", "reply"):
                yield ("delta", piece)
            yield ("response", self.complete(messages, tools))

    server.build_provider = lambda config: StreamingProvider()
    sid3 = client.post("/sessions", headers=a_auth).json()["session_id"]
    with client.stream("POST", f"/sessions/{sid3}/messages/stream",
                       headers=a_auth, json={"message": "go"}) as resp:
        tbody = "".join(resp.iter_text())
    tevents = [ln[len("event: "):] for ln in tbody.splitlines() if ln.startswith("event: ")]
    assert tevents.count("token") == 3, tevents
    assert '"delta": "stream"' in tbody and '"delta": "reply"' in tbody, tbody
    print("  SSE token streaming: incremental delta events arrive OK")
    # streaming endpoint is auth-enforced + isolated too
    assert client.post(f"/sessions/{sid2}/messages/stream", headers=b_auth,
                       json={"message": "x"}).status_code == 404
    print("  SSE streaming: tool_call/tool_result/answer/done events, auth-enforced OK")

    # HITL approval mechanism + endpoints (concurrency-free): drive the
    # approval registry directly, then resolve through the HTTP endpoints.
    state = app.state.harness
    import threading

    # timeout -> deny (fail safe)
    aid_to = state.create_approval(alice["user_id"], sid, "write_file", {"path": "x"}, "write")
    assert state.wait_approval(aid_to, timeout=0.05) is False

    # a decision from a background resolver unblocks wait_approval -> allow
    aid = state.create_approval(alice["user_id"], sid, "write_file", {"path": "y"}, "write")
    assert client.get(f"/sessions/{sid}/approvals", headers=a_auth).json()[0]["approval_id"] == aid
    # bob cannot resolve alice's approval
    assert client.post(f"/sessions/{sid}/approvals/{aid}", headers=b_auth, json={"approved": True}).status_code == 404
    result = {}
    def waiter():
        result["allowed"] = state.wait_approval(aid, timeout=5)
    t = threading.Thread(target=waiter); t.start()
    r = client.post(f"/sessions/{sid}/approvals/{aid}", headers=a_auth, json={"approved": True})
    assert r.status_code == 200 and r.json()["approved"] is True
    t.join(timeout=5)
    assert result["allowed"] is True, "resolved approval must unblock the waiter as allowed"
    # unknown approval id -> 404
    assert client.post(f"/sessions/{sid}/approvals/nope", headers=a_auth, json={"approved": True}).status_code == 404

    # denying (approved=false) unblocks the waiter as NOT allowed
    aid_d = state.create_approval(alice["user_id"], sid, "run_command", {"command": "rm -rf /"}, "dangerous")
    denied = {}
    def deny_waiter():
        denied["allowed"] = state.wait_approval(aid_d, timeout=5)
    td = threading.Thread(target=deny_waiter); td.start()
    assert client.post(f"/sessions/{sid}/approvals/{aid_d}", headers=a_auth, json={"approved": False}).status_code == 200
    td.join(timeout=5)
    assert denied["allowed"] is False, "a denied approval must resolve as not-allowed"
    print("  HITL approval: timeout denies, resolve allow/deny works, owner-scoped, unknown id 404 OK")


def test_server():
    if not _fastapi_available():
        print("  [skipped] server tests (fastapi/httpx not installed -- pip install -r requirements-server.txt)")
        return
    _run()


def main():
    test_server()
    print("SERVER TESTS PASSED")


if __name__ == "__main__":
    main()
