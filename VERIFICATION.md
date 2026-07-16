# VERIFICATION — skeptical re-audit of the agent harness

Adversarial verification of the 9-milestone + sandbox work (see `AUDIT.md`,
`PLAN.md`, `DESIGN.md`). Goal: prove it broken, not confirm it good. **Every
verdict below is backed by a command run in this session** — prior test
results and transcripts were treated as hypotheses, not evidence.

Environment: fresh `python3 -m venv` at `/tmp/cleanroom_venv`, deps from
`requirements.txt` only; Docker 29.3.1 daemon available (so the sandbox
integration tier ran live, not skipped).

**Headline:** the code-review pass (separate, already merged to the branch)
fixed 5 issues; this deeper verification found **1 more real bug the unit
tests had masked** — a real MCP stdio server crashes on disconnect — which is
now fixed with a real-server regression test. Test suite grew 24 → **26
files, all passing**.

---

## Phase 1 — clean-room setup

| Step | Command (this session) | Result |
|---|---|---|
| Fresh venv install | `python3 -m venv /tmp/cleanroom_venv && pip install -r requirements.txt` | ✅ exit 0; all documented deps import (`anthropic, openai, mcp, yaml, sqlalchemy, jwt`) |
| Full suite (fresh venv) | ran every `tests/*_test.py` | ✅ **25/25** files passed in 38s (before this phase's additions) |
| Lint (`ruff check --select F,E9`) | on the whole tree | 4 × F401 unused-import, **all in test files** — no library bugs. Fixed all 4; `ruff` now: "All checks passed!" |
| Types (`mypy` on library) | `engine/ providers/ storage/ context_engine/ observability/ auth/ config.py` | 4 findings, 3 cosmetic annotation gaps; **1 latent** (`user_store.py:99` compared a possibly-`None` count with `<= 1`). Hardened to `(admins or 0) <= 1`. |

No undocumented setup step was needed — the README's install path works from
scratch. (Earlier the code-review already fixed the one thing that *did*
break clean-room: a blank `HARNESS_MAX_TOKENS=` in `.env.example` crashing
`Config.load()` with `int('')`.)

---

## Phase 2 — subsystem torture tests

### A. Model layer & switching
- Unknown model string → `ValueError("Don't know how to build a provider…")`, not a traceback. ✅
- Valid provider builds with a bad key (error is deferred to call time, where retry/fallback handle it). ✅
- Runtime `/model` switch preserving history and rejecting a bad model without mutating the session: covered by `tests/model_switch_test.py` (4 cases, pass). ✅
- **Gap (stated, not a defect):** only mock/fake providers are exercised — no live API key in this environment, so real two-provider tool-calling is unverified here. `providers/anthropic_provider.py` and `providers/openai_provider.py` translation is covered by the neutral-format contract tests.

### B. Sessions & multi-user isolation (highest priority)
- Cross-user access: `tests/storage_test.py::test_sessions_are_isolated_per_user` — as user B, `list_ids`/`load`/`delete` of user A's session id all fail (store is bound to a `user_id` at construction; no query can reach another user's rows). ✅
- Concurrency: `tests/concurrency_test.py` — two interleaved threaded sessions with distinct memory/offload/workspace roots and plans, asserting zero cross-leakage (a test that could not pass before the ContextVar work). ✅
- Delete: `tests/fixes_test.py` + a **live CLI run** this session — `/save killme` → `/delete killme` → `/sessions` shows "(no saved sessions)"; the DB row *and* (code-review fix) the on-disk workspace dir are removed, with a traversal guard. ✅
- Resume-after-kill: sessions auto-save to the DB after each completed turn (`store.save` per turn in `interfaces/cli.py`); the DB round-trip is proven by `tests/phase2_test.py::test_session_roundtrip`. A hard kill loses only the in-flight turn, not committed history. ✅ (mechanism verified; full kill-and-relaunch is the CLI wrapping this same store.)

### C. Filesystem & path safety
Live this session, confined workspace (`engine.workspace.set_workspace_root`):
```
read ../../etc/passwd   -> Error: path '../../etc/passwd' escapes the workspace directory
read abs outside        -> Error: path '/tmp/.../passwd' escapes the workspace directory
write abs outside       -> Error: ... escapes ...   | file created? False
read via symlink        -> Error: path 'esc/passwd' escapes the workspace directory
write inside            -> Wrote 2 characters to ok.txt | exists? True
```
Edit tool: targeted edit applied (`a=1`→`a=99`); non-matching edit → clean
`Error: the text to replace was not found`. ✅ All four attack vectors
blocked, including symlink escape (realpath-based).

### D. Sandbox (`HARNESS_SANDBOX=docker`) — live, real containers
`tests/sandbox_test.py` real-container tier ran (daemon up):
- Host file outside the workspace: **unreadable** from the container. ✅
- `--network=none`: egress **blocked**. ✅
- Workspace mount shared **both ways** (host→container, container→host). ✅
- **Timeout kill** (code-review fix, verified live): `sleep 30` with `timeout=2` is killed by an in-container `timeout`; the runaway process is **gone** afterward (not merely abandoned). ✅
- No leaked containers after teardown (`docker ps -a --filter name=harness-sbx-` empty). ✅
- Daemon-down → **loud failure at startup** (`preflight` raises `SandboxError`). ✅
- Pipeline path: `HARNESS_SANDBOX=docker` now **fails loud** in `pipeline.py` instead of silently running on the host (code-review fix). ✅
- **Not tested here:** an OS-level memory-hog / fork-bomb actually tripping `--memory`/`--pids-limit` (the flags are asserted present in `test_container_flags_and_naming`; a live OOM-kill was not driven).

### E. Memory
- Session memory: DB round-trip (B above). ✅
- Long-term per-user memory across sessions + **not** visible to another user: `config.memory_dir` is namespaced per user by `Config.for_user`; `tests/memory_test.py` covers CRUD + injection. ✅
- Corruption resilience (live): a binary-garbage `notes.md` → `memory view` returns an **error string** (not a raise); `memory_overview` skips it and returns `''`; a missing memory dir → `''`. ✅ Degrades gracefully.

### F. Context engine
Live this session:
- **Forced compaction** (tiny 40-token budget): `maybe_compact()` folds old turns, keeps the recent 2, and a seeded `SECRET-CODE=42` fact **survives** into the summary and the system prompt. Session continues, no error. ✅
- **Forced offload**: a 60 000-char output → 4 148-char inline preview mentioning the saved path + `read_file`; the full 60 000 chars are on disk and recoverable. ✅

### G. Tools & MCP
- **Real stdio MCP server** (via the `mcp` SDK, no external binary) through the real `MCPManager`: tools discovered (`mcp__probe__echo`), risk mapped (`write`), **live call succeeded** (`echoed: hello-mcp`). ✅
- **🟠 BUG FOUND & FIXED:** disconnecting the real server crashed with anyio `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in`. The fake-session unit test never exercised real transport teardown, so it was masked. Root cause: the transport/session context was **entered in one background-loop task and exited in another**. Fixed by running each connection's whole lifecycle (enter → hold → tear down) in **one** task (`_ServerConnection.run` + a close event). Re-verified live: 2× connect/call/disconnect + `disconnect_all` all clean. New regression test `tests/mcp_real_test.py`.
- Malformed tool args (live): missing/extra/wrong-type args and unknown tools all return recoverable `Error: …` strings — nothing raises into the loop. ✅
- Call against a gone/absent MCP session → structured `Error: … is not connected`. ✅

### H. Auth scaffolding (NOT production-verified — scaffolding by design)
Live this session: JWT issue/verify round-trips; wrong secret, tampered
token, and expired token all **rejected**; a registered password does **not**
appear in the SQLite DB bytes (PBKDF2 hash only). ✅
**Stated plainly:** this is scaffolding. Nothing enforces the token
*per-request* yet (one process, one login); role checks gate CLI commands,
not storage APIs. It becomes a real boundary only in the server phase. Not
marked production-ready.

---

## Phase 3 — error hunt

- **No** bare `except:` in library code. Every `except Exception: pass` is deliberate and commented (yaml-config fallback; "accounting/bookkeeping must never break a run"; MCP best-effort teardown). ✅
- **No** `TODO`/`FIXME`/`XXX`, **no** hardcoded secrets/keys, **no** stray debug `print()` in library code (`interfaces/` prints legitimately). ✅
- **Injection surfaces:** all SQL goes through the SQLAlchemy ORM (parameterized) — no `text()`/f-string SQL. Model/user input that becomes a file path is confined (`engine/workspace.confine`, `memory_tool._resolve`, `session_store._safe_id`, `_delete_session_workspace` guard). `run_command`'s `shell=True` is the intended, permission-gated, sandbox-isolatable escape hatch. ✅
- **Dependency health:** `pip check` → "No broken requirements found." `pip-audit` → 7 CVEs, **all in `pip`/`setuptools`** (the venv's own bundled tooling), **none in any declared harness dependency** (anthropic, openai, mcp, pyyaml, sqlalchemy, pyjwt, python-dotenv). ✅
- **Logs:** the JSONL event log gains `duration_ms` on usage/tool_result; no silent stack traces observed in the Phase-2 runs.

---

## Phase 4 — full re-audit (drift from AUDIT.md)

Re-scored against evidence from this session. **No previously-✅ item
regressed.** One ✅ (C5, MCP) had a latent teardown crash that this pass
caught and fixed — it stays ✅ because the fix is verified with a real
server, but it is called out as "was masked by fake-only tests."

Summary (unchanged from `AUDIT.md`'s final tally, re-confirmed here):
**29 done / 6 partial / 0 missing / 0 deferred** of 35.

The 6 remaining **partials are all still genuine scoped-out non-goals**, none
has silently become a real gap:

| Item | Partial reason | Still legitimate? |
|---|---|---|
| A1 provider-agnostic | 2 hand-written adapters; OpenRouter/base_url covers the rest | ✅ non-goal (D3) |
| A3 streaming | not implemented | ✅ recorded non-goal — but the client phase may force this (see roadmap) |
| B1 system-prompt parts | base + AGENTS.md + memory; no env-info section | ✅ minor, non-blocking |
| B5 progressive tool disclosure | all tool schemas sent every call | ✅ acceptable until tool count explodes |
| F1 registry visibility | one process-wide registry (tool *visibility*, not data isolation) | ✅ server-phase item |
| H2 self-verification loop | exists in the pipeline, not the interactive loop | ✅ by design |

---

## Verdict table

| Subsystem | Tested how (this session) | Result | Outstanding |
|---|---|---|---|
| Model layer | factory bad-model/bad-key; model_switch tests | ✅ | live 2-provider tool-calling needs a key |
| Sessions / multi-user | storage tests + live /delete + concurrency test | ✅ | — |
| Filesystem / path safety | live traversal/absolute/symlink attacks | ✅ blocked | — |
| Sandbox | live real-container isolation + timeout-kill + no leaks | ✅ | live OOM/fork-bomb kill not driven |
| Memory | live corruption + CRUD + cross-user namespacing | ✅ | — |
| Context engine | live forced compaction + forced offload | ✅ | — |
| Tools / MCP | **real stdio server** connect/call/disconnect + malformed args | ✅ (1 bug fixed) | — |
| Auth | live JWT + password-hash inspection | ✅ scaffolding | per-request enforcement = server phase |

## Fixes made this session

| Fix | Regression test |
|---|---|
| **MCP disconnect crash** (enter/exit cancel scope in different tasks) → single-task lifecycle | `tests/mcp_real_test.py` (real stdio server, 2× connect/disconnect + disconnect_all) |
| `user_store.py` last-admin guard compared possibly-`None` count | existing `tests/usage_store_test.py` last-admin case + `(admins or 0)` |
| 4× unused-import lint noise in tests | `ruff check` clean |

(The 5 code-review fixes — config blank-int crash, pipeline sandbox
fail-loud, `/delete` workspace removal, sandbox timeout-kill, usage N+1 —
landed just before this pass and were re-verified live here.)

## Known limitations (honest)

- **Auth is scaffolding**, not a per-request security boundary (server phase).
- **No live multi-provider run** here (no API key) — provider translation is contract-tested with fakes.
- **Sandbox OOM/fork-bomb** limits are asserted-present but not driven to a live kill; the isolation + timeout-kill + network-deny ARE proven on real containers.
- **Streaming** is still absent (recorded non-goal) — the roadmap flags it as possibly forced by a client.
- **One process-wide tool registry** (F1): connected MCP tools are visible to every session in the process — a *visibility* scoping question for the server phase, not a data-isolation defect (isolation is proven).

---

## Phase 5 — THE NEXT BIG THING

**Recommendation: the HTTP server phase (FastAPI), and it is #1 — confirmed,
not merely assumed.** Every remaining partial and every "scaffolding" caveat
converges on the same missing piece: there is no process that turns the
multi-user *design* (user_id-keyed storage, JWT issue/verify, per-session
workspaces/containers, ContextVar isolation) into an actually-enforced
*runtime*. The harness has spent 9 milestones becoming server-ready; nothing
is served yet. Until a request boundary exists, auth can't be enforced, the
client can't exist, and F1 (registry scoping) can't be resolved.

**Why not the alternatives first:**
- *Client app* — can't be built before the server it talks to; it only *defines requirements* for the server (below).
- *Streaming* — real, but it's a property the server must expose; sequence it **inside** the server phase (the first client will want it), not before.
- *Observability upgrades* — `duration_ms` + the `usage_log` already cover the near-term need; dashboards are premature before real traffic.
- *Phase-2/4 weaknesses* — the only real bug found (MCP disconnect) is already fixed; nothing else outranks "make multi-user real."

### What the client forces the server to expose (so we build it right once)
Auth endpoints (`POST /register`, `POST /login` → JWT); session list/create/
resume/delete APIs; **streamed** turn responses (SSE) so a UI isn't blocked
for 30s; per-request `user_id` from the verified token flowing into the exact
same `Config.for_user` / storage / workspace code that exists today.

### Milestone sketch (a starting point for the next PLAN.md)
1. **HTTP skeleton + auth enforcement** — FastAPI app; `POST /auth/register` & `/auth/login` issuing the existing JWT; a dependency that verifies the bearer token on every request and yields `(user_id, role)`. Reuse `auth/tokens.py` + `DbUserStore` unchanged. *Verify:* unauthenticated request → 401; valid token → identity.
2. **Session APIs over the existing store** — `GET/POST/DELETE /sessions`, `GET /sessions/{id}` backed by `DbSessionStore(engine, user_id)` from the token. *Verify:* user A cannot touch user B's session via the API (the isolation already proven at the store layer, now at the HTTP layer).
3. **The turn endpoint, streamed** — `POST /sessions/{id}/messages` running one `Orchestrator.run`, streaming events over SSE. **This is where streaming (A3) finally gets built** — as a provider capability surfaced through the loop's existing `on_event`. *Verify:* tokens arrive incrementally; a tool call surfaces mid-stream.
4. **Per-request isolation wiring** — set the workspace/memory/offload ContextVars (D28) and, if `HARNESS_SANDBOX=docker`, the per-session container, at request scope; resolve **F1** by giving each request a session-scoped registry view (or documenting the shared-tool policy). *Verify:* two concurrent HTTP sessions, distinct workspaces/plans, zero leakage — the concurrency test, but over HTTP.
5. **Approval over HTTP (human-in-the-loop)** — replace the CLI's blocking `input()` approver with an async approve/deny round-trip (a pending-action record the client resolves). *Verify:* an `ask`-mode write pauses the turn and resumes on approval.
6. **Deploy shape** — Postgres via `HARNESS_DB_URL` (already supported), a container image, health check. *Verify:* the whole suite green against Postgres, not just SQLite.

Milestones 1–2 make multi-user **real and enforced**; 3 unblocks any client
and delivers streaming; 4 closes the last isolation question; 5 is what makes
an interactive web client usable. That is the next PLAN.md.
