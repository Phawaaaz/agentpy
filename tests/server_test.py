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

    class FakeProvider(Provider):
        def complete(self, messages, tools):
            # Echo the last user message so the test can assert the turn ran.
            last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
            text = f"handled: {last}"
            return Response(text=text, tool_calls=[], assistant_message={"role": "assistant", "content": text})

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
