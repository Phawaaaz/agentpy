# Demo Notes — Agent Harness Web UI

Everything you need to run the live demo, what to type, how to reset, and what
to do if something goes sideways on stage.

---

## 1. Start it (one command)

```bash
./demo.sh
```

That's it. It:
- runs the backend on **http://localhost:8000**
- runs the frontend on **http://localhost:5173**  ← open this
- seeds two accounts: **alice / alice123** and **bob / bob123**
- runs the **offline scripted provider** by default (no API key needed)

Open **http://localhost:5173** in your browser. `Ctrl-C` stops both servers.

> First run only: `demo.sh` will `pip install` the backend deps and
> `npm install` the frontend if they're missing. Do this **before** you're on
> stage so it's warm.

### Optional: use a real model instead of the scripted one

```bash
export HARNESS_API_KEY=sk-...      # your provider key
export HARNESS_MODEL=anthropic/claude-opus-4-8
./demo.sh --live
```

Everything works the same; the agent's replies just come from the real model.
**For the presentation, prefer the default (offline) mode** — it's
deterministic and can't be broken by a flaky network. See the fallback plan.

---

## 2. The script — exactly what to click and type

Open **http://localhost:5173**.

1. **Login screen.** Log in as `alice` / `alice123`.

2. **Create a session, pick a model, chat.**
   - Click **+ New session**.
   - Note the **model dropdown** (top right) and the green **Sandbox ON** badge.
   - Type: `hello` → the reply **streams in live** with a blinking cursor.

3. **Trigger tools.** Type exactly:
   ```
   create a file listing the planets, then show me its contents
   ```
   Two **tool-call cards** appear in real time — `write_file`, then
   `read_file`. Click a card to expand its input/output.

4. **The money shot — sandbox block.** Type exactly:
   ```
   read /etc/passwd outside the workspace
   ```
   The `read_file` card turns **red** with a **BLOCKED** badge and
   "Error: path '/etc/passwd' escapes the workspace directory". This is the
   workspace confinement stopping the agent from leaving its per-session
   directory. Expand the card to show the shield note.

5. **Switch model mid-conversation.** Change the **model dropdown** (e.g. to
   `anthropic/claude-opus-4-8`). Type `hello again`. The **model chip** on the
   new reply shows the new model — same conversation, different model.

6. **Isolation — second browser as bob.** Open a **second browser** (or an
   incognito window) at http://localhost:5173. Log in as `bob` / `bob123`.
   Bob's session list is **empty** — he cannot see any of alice's sessions.

7. **Resume — kill and reopen.** Back in alice's browser, **close the tab**,
   then reopen http://localhost:5173. You're still logged in and the **session
   history is intact** (log back in as alice if prompted — her sessions and
   transcript are all there).

---

## 3. Reset between rehearsals

Wipe all demo state (sessions, workspaces, accounts get re-seeded next start):

```bash
./demo.sh --reset
```

Or just delete the state dir: `rm -rf /tmp/harness-demo`. A fresh `./demo.sh`
re-seeds alice/bob automatically. (A quick reset mid-demo: click the **×** on a
session in the sidebar to delete it, or **+ New session** to start clean.)

---

## 4. If something breaks on stage (fallback plan)

- **A real model is slow or down** (only relevant with `--live`): stop, run
  plain `./demo.sh` (offline scripted provider). Every step above works
  identically with **no network**. This is the safe default and what you
  should present with.
- **The stream drops / backend hiccups mid-reply:** the UI shows a calm
  **"Connection lost — retry your message"** banner instead of crashing.
  Restart the backend (`Ctrl-C`, `./demo.sh` again) and **re-send the same
  message** — it just works. (This exact failure is rehearsed and handled.)
- **Frontend won't load:** confirm both ports — `curl localhost:8000/health`
  and open `localhost:5173`. If a port is stuck, `pkill -f server.app;
  pkill -f vite` and re-run `./demo.sh`.
- **Model dropdown lists real providers you have no key for:** in offline mode
  they all route through the scripted provider anyway, so picking any of them
  is safe during the demo — the reply and the model chip still update.

---

## 5. Shortcuts taken (this is a demo build, not production)

These are deliberate, to hit the presentation on time. None affect what the
audience sees; they'd be the follow-up hardening list.

- **Scripted `DemoProvider`.** `server/demo_provider.py` returns deterministic
  tool activity + text so the demo runs with no API key. It's keyword-driven,
  not a real model. (Real models work via `--live`.)
- **Seeded accounts + open CORS.** alice/bob are hard-seeded and CORS is
  wide-open for `localhost`. Fine for a laptop demo; a real deployment needs
  proper user management and locked-down origins.
- **Session's chosen model kept in memory.** The per-session model lives in a
  process dict, so a backend restart forgets it (defaults back to
  `demo/scripted`). Conversation history itself is persisted in SQLite and
  survives restarts.
- **JWT in `localStorage`.** Simple and enough for the demo; a production app
  would use httpOnly cookies / refresh tokens.
- **SQLite, single file.** `HARNESS_DB_URL` can point at Postgres with no code
  change (that path exists in the harness), but the demo uses SQLite for
  zero-setup.
- **No automated frontend unit tests.** The frontend was verified by driving
  the full 7-step script end-to-end with Playwright (twice, clean) plus a
  kill-the-backend-mid-stream reconnect test. The backend was curl-smoke-tested
  and the harness's own Python test suite is unchanged.
- **highlight.js bundles all languages** (~1 MB JS). Irrelevant on localhost;
  a production build would trim to the languages actually used.

---

## 6. Under the hood (one paragraph)

The frontend is React/Vite (`frontend/`). The backend is a thin FastAPI layer
(`server/`) that **imports the existing harness directly** — same Orchestrator,
storage, auth, workspace confinement, and providers as the CLI. A turn is
streamed to the browser as Server-Sent Events (`model_info`, `token`,
`tool_call_started`, `tool_call_finished`, `assistant_message`, `done`,
`error`); the Orchestrator runs in a worker thread and its events are bridged
onto the async event loop. The web UI adds **no agent logic** — it's just a new
interface over the harness.
```
frontend/  →  server/app.py (FastAPI + SSE)  →  engine/orchestrator.py (unchanged loop)
```
