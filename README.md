# Agentic Harness

A small, model-independent agentic harness: an LLM wrapped in a loop that lets it
use tools (read/write files, run commands) to complete real tasks. Built to start
minimal and grow into a company-wide coding + automation assistant.

## Documentation

- [AGENTS.md](AGENTS.md) — **start here if you're an AI coding assistant** (vendor-neutral instructions)
- [ARCHITECTURE.md](ARCHITECTURE.md) — structure, request lifecycle, the message-format contract, extension points
- [DESIGN.md](DESIGN.md) — the key decisions and why (ADR-style)
- [PRINCIPLES.md](PRINCIPLES.md) — SOLID + best practices this code must follow, with a PR checklist
- [CONTRIBUTING.md](CONTRIBUTING.md) — step-by-step: add a tool, a provider, or an interface
- [SANDBOX_DESIGN.md](SANDBOX_DESIGN.md) — the design + build record for containerized command execution (D33)

## Architecture

```
interfaces/     thin entry points (CLI, pipeline CLI now; Slack / API later)
engine/         orchestrator (the loop) + permissions + registry + MCP client + built-in tools (engine/builtin/)
context_engine/ conversation compaction + memory tool + activity tracker + session persistence
auth/           user accounts: salted/hashed passwords, per-user session isolation
providers/      model abstraction (anthropic + openai SDKs => any model)
pipeline/       optional outer loop: multi-stage autonomous runs
observability/  token usage + cost estimate + event logging
config.py       model, key, permission mode, limits
```

The loop is fixed: **observe → think → act → repeat.** New capabilities are added
by registering new tools — not by changing the loop.

## Setup

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then edit .env: set HARNESS_MODEL and HARNESS_API_KEY
```

The harness is model-independent. To use a different model, change two values in
`.env`:

```
HARNESS_MODEL=openai/gpt-4o          # or anthropic/claude-opus-4-8, ollama/llama3, ...
HARNESS_API_KEY=sk-...
```

Known prefixes: `anthropic/`, `openai/`, `openrouter/`, `groq/`, `together/`,
`ollama/`. Any other OpenAI-compatible server works by setting `HARNESS_BASE_URL`.

Transient failures (rate limits, dropped connections) are retried with
exponential backoff inside each provider adapter. Optionally set
`HARNESS_FALLBACK_MODEL` to a second model to retry a still-failing call on
(same credentials — use a sibling model or a key-less local one).

To switch models without restarting, use `/model <name>` inside a running
session (e.g. `/model ollama/llama3.2:3b`) — it rebuilds the provider and
keeps your conversation history.

## Run

```bash
python main.py
```

The CLI starts with a sign-in prompt: enter a username, and if it doesn't
exist yet you'll be asked to choose a password and it's created on the spot
(passwords are PBKDF2-hashed with a random per-user salt — see DESIGN.md
D22 — never stored in plaintext, in `HARNESS_USERS_FILE`, default
`.harness/users.json`). Each user's sessions, memory, logs, and offloaded
tool output live under their own subdirectory, so concurrent users never see
each other's data. For scripted or demo use, set `HARNESS_USER` +
`HARNESS_PASSWORD` in `.env` to skip the interactive prompt.

Then type a task, e.g. *"list the files here and tell me what this project is."*

## MCP servers

Connect external [MCP](https://modelcontextprotocol.io) servers so their
tools show up alongside the built-in ones. Copy `mcp.json.example` to
`.harness/mcp.json` (or point `HARNESS_MCP_CONFIG` elsewhere) and list your
servers — local (stdio, launched as a subprocess) or remote (`http`/`sse`).
They connect automatically on startup; `/mcp`, `/mcp connect <name>`, and
`/mcp disconnect <name>` manage them at runtime. Their tools are namespaced
`mcp__<server>__<tool>` and go through the same permission modes as everything
else (risk is inferred from the server's own tool annotations, defaulting to
`write` when it doesn't say).

## Memory

Two independent pieces (see DESIGN.md D16 for why they're separate):

- A `memory` **tool** the model can call itself (`view`/`create`/
  `str_replace`/`insert`/`delete`/`rename`) to persist notes across turns and
  sessions — same shape as Anthropic's memory-tool convention, but a plain
  neutral tool so it works on every provider, not just Claude.
- An automatic **activity tracker** that needs no tool call at all: it
  watches the same events the CLI already prints and keeps
  `.harness/memory/activity.md` up to date with the current task, files
  touched, and tool usage counts. Check it anytime with `/memory`.

Both write into `HARNESS_MEMORY_DIR` (default `.harness/memory`). At the
start of every new session, a capped digest of your memory directory is
injected into the system prompt automatically (DESIGN.md D31) — the agent
sees prior notes without having to think to look for them.

## Skills

`/review`, `/verify`, `/test`, `/docs` run one of the pipeline's stage
prompts on demand, in your current conversation (not an isolated run) —
useful when you want "review what we just did" without kicking off the full
autonomous pipeline. Give a task explicitly (`/verify the login flow`) or
omit it to reuse the current task tracked in memory.

Add your own by copying `skills.json.example` to `.harness/skills.json` — no
code needed:

```json
{"skills": {"style-check": {
  "description": "Review the diff against our style guide",
  "prompt": "Review this change against our style guide.\n\nTASK:\n{task}\n\nCHANGES (--stat):\n{diff_stat}"
}}}
```

`{task}` and `{diff_stat}` get substituted in; the new command (`/style-check`
here) shows up alongside the built-ins. Naming a skill the same as a
built-in overrides it (with a startup notice), so you can also replace
`/verify`, `/test`, etc. by defining a skill with that exact name.

## Multi-agent

Copy `roles.json.example` to `.harness/roles.json` (or point
`HARNESS_ROLES_CONFIG` elsewhere) to define sub-agent roles — e.g. a
read-only `researcher`, a `reviewer`, a `coder`. Once any roles are
configured, a `delegate(role, task)` tool appears: the agent can hand a
self-contained sub-task to a role-specific sub-agent and get back its final
answer, same as calling any other tool. Sub-agents share your `model`,
`permission_mode`, and memory directory — write something to memory and any
sub-agent (or the coordinator) can read it back. `/roles` lists what's
configured. No roles configured = no `delegate` tool = unchanged behavior.

## Planning

The agent has `todo_write`/`todo_read` tools to keep an explicit, visible
step-by-step checklist for the current task instead of only holding a plan
in its own reasoning — each step tracked as `pending`/`in_progress`/
`completed`. No configuration needed; it resets on `/new` or `/load`.

## Web search

The `web_search` tool is always available, with two backends behind the one
name: set `HARNESS_SEARCH_API_KEY` to a [Tavily](https://tavily.com) API key
(free tier available) for reliable results, or leave it unset and the tool
falls back to scraping DuckDuckGo — works with zero configuration, just
less reliably (bot-detection pages, occasional empty results). Use it for
current information not in the model's training data; `fetch_url` is still
what you want for a URL you already know.

## Large tool output

Any tool result over ~20k characters (a big file, a noisy command, a large
page fetch) no longer gets hard-truncated and lost — the full output is
written to a file under `HARNESS_OFFLOAD_DIR` (default `.harness/offload/`),
and the tool returns a preview plus that path. The model can `read_file` the
rest if it actually needs it. Applies to `read_file`, `run_command`,
`fetch_url`, and the memory tool's `view`.

## Autonomous pipeline

For a task you want worked end-to-end unattended:

```bash
python pipeline.py "add a health check endpoint and its test"
```

This runs a multi-stage loop — implement (iterating with stuck/timeout
safety rails) → self-review → verify → test (with a bounded repair loop on
failure) → sync-docs — inside an isolated git worktree + branch, so it never
touches your current checkout. **It stops before pushing or opening a PR**:
you get a committed branch and a summary, and you push/PR it yourself.
Because no human is present to approve actions mid-run, set
`HARNESS_PERMISSION_MODE=allowlist` or `auto` — in `ask` mode every write
gets denied and the pipeline will report "stuck" almost immediately.

### Session commands

Inside the CLI, lines starting with `/` are commands (everything else is a task):

| Command | Does |
|---------|------|
| `/new` | Start a fresh conversation |
| `/save [id]` | Save the current session |
| `/load <id>` | Resume a saved session |
| `/delete <id>` | Delete a saved session |
| `/sessions` | List saved sessions |
| `/cost` | Show token usage + estimated cost |
| `/memory` | Show what the harness has been working on |
| `/model` | Show the current model |
| `/model <name>` | Switch model mid-session, e.g. `/model ollama/llama3.2:3b` (conversation history kept) |
| `/whoami` | Show the logged-in user and role |
| `/usage [username]` | Admin only: token/cost usage per user, or one user's sessions + tasks |
| `/users` | Admin only: list accounts and roles |
| `/users role <u> <r>` | Admin only: promote/demote an account |
| `/review`, `/verify`, `/test`, `/docs` | Run a pipeline stage's prompt on demand (skills) |
| `/roles` | List configured sub-agent roles (`delegate` target) |
| `/help` | List commands |

Sessions auto-save after each turn to the relational store (`HARNESS_DB_URL`,
default a SQLite file at `.harness/harness.db`; point it at Postgres for a
multi-user server — no code change). Events are traced to `.harness/logs/`.
Upgrading from an older checkout with JSON-file sessions/accounts? Run
`python scripts/migrate_json_to_db.py` once — passwords and history carry
over exactly. Long conversations are automatically compacted (older messages
summarized) so they don't overflow the model's context window.

## Workspace confinement (opt-in)

Set `HARNESS_CONFINE_WORKSPACE=true` to confine the filesystem tools and
`run_command` to a per-user, per-session directory
(`workspaces/{user}/{session}/`) — `../` traversal, outside absolute paths,
and symlink escapes are all rejected. Off by default: the single-user CLI
historically works directly in whatever directory you launch it from, and
that stays the default. This is a path boundary; for true host isolation of shell commands, turn on
the sandbox below.

## Sandbox (opt-in, needs Docker)

Set `HARNESS_SANDBOX=docker` to run every `run_command` inside a per-session
Docker container that mounts **only** that session's workspace, with memory/
CPU/PID limits, dropped capabilities, a read-only rootfs, and networking
denied by default (`HARNESS_SANDBOX_NETWORK=bridge` to allow it). It implies
workspace confinement and verifies the Docker daemon at startup (failing
loud if it's unreachable). The permission layer stays the first gate; the
container is a second, independent one. Off by default — commands run on the
host. See [SANDBOX_DESIGN.md](SANDBOX_DESIGN.md) and DESIGN.md D33.

## Admin monitoring

The first account ever created becomes the **admin**; everyone after is a
regular user (an admin can promote/demote with `/users role <name>
<admin|user>` — the last admin can't be demoted). Every model call is
durably logged (user, session, model, tokens, estimated cost, and the task
text), so an admin can answer "who is spending tokens, and on what":
`/usage` shows per-user totals, `/usage <username>` shows that user's
sessions with their last task. Regular users still see their own session's
`/cost`. On login the CLI also issues and verifies a JWT (see DESIGN.md
D30) — scaffolding the future server's per-request auth will reuse as-is.

## HTTP API server (multi-user)

Beyond the CLI, the harness runs as an HTTP API (`interfaces/server.py`, FastAPI)
that turns the multi-user design into an enforced runtime: **every request
carries a JWT**, and the `user_id` it verifies is the only key used to reach
storage, so one user's token cannot touch another's data.

```bash
pip install -r requirements.txt -r requirements-server.txt
export HARNESS_MODEL=anthropic/claude-opus-4-8 HARNESS_API_KEY=sk-...
export HARNESS_JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_hex(32))")
uvicorn interfaces.server:app --host 0.0.0.0 --port 8000   # or: python main_server.py
```

Endpoints: `POST /auth/register`, `POST /auth/login` (→ JWT), `GET /auth/me`;
`GET/POST /sessions`, `DELETE /sessions/{id}`, `POST /sessions/{id}/messages`
(runs one agent turn); admin-only `GET /admin/usage[/{username}]`; `GET /health`.

Notes:
- The first registered account becomes **admin**. Auth is now enforced
  per-request (unlike the CLI's login scaffolding).
- An HTTP turn has no human attached, so an `ask` permission decision is
  **denied** (fail-safe) -- run the server in `allowlist` mode. Streaming
  responses and human-in-the-loop approval over HTTP are the next milestones.
- Each request runs in an isolated execution context (D28): memory/offload/
  workspace roots are per-user, so concurrent requests never cross.

### Deploy

`Dockerfile` + `docker-compose.yml` bring up the API plus Postgres:

```bash
cp .env.example .env    # set HARNESS_API_KEY, HARNESS_JWT_SECRET, POSTGRES_PASSWORD
docker compose up --build
```

The compose file points `HARNESS_DB_URL` at Postgres and sets safe server
defaults (`HARNESS_CONFINE_WORKSPACE=true`, `allowlist`). The container-based
command sandbox (`HARNESS_SANDBOX=docker`) needs an external Docker daemon;
enable it with the isolated dind-sidecar override:

```bash
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up --build
```

See **[DEPLOY.md](DEPLOY.md)** for the full deployment guide — the three ways
to give the sandbox a Docker daemon (host socket vs. dind sidecar vs.
running on a Docker host), getting Docker on a fresh box, and a production
checklist.

## Permission modes (set `HARNESS_PERMISSION_MODE` in `.env`)

| Mode | Behavior |
|------|----------|
| `ask` | Auto-allow reads; prompt before writes / shell / risky actions |
| `allowlist` | Run safe + write actions automatically; block dangerous ones |
| `auto` | Run everything without asking (sandbox only) |

## Verify without an API key

```bash
python tests/smoke_test.py    # full agent loop against a fake model
python tests/phase2_test.py   # context compaction, persistence, usage tracking
python tests/mcp_test.py      # MCP tool wrapping, risk mapping, call dispatch
python tests/pipeline_test.py # stage sequencing, stuck detection, repair loop
python tests/memory_test.py   # memory tool CRUD/confinement, activity tracker
python tests/cli_skills_test.py     # /review /verify /test /docs commands
python tests/external_skills_test.py # skills.json loading + prompt substitution
python tests/multiagent_test.py     # delegate tool, FilteredRegistry, no recursion
python tests/offload_test.py        # oversized output -> file + preview, not lost
python tests/model_switch_test.py   # /model command, history preserved across a switch
python tests/auth_test.py           # password hashing, UserStore, login flow, per-user dirs
python tests/planning_test.py       # todo_write/todo_read checklist tool
python tests/search_test.py         # web_search: Tavily + DuckDuckGo fallback, mocked urlopen
python tests/retry_test.py          # transient-error retry/backoff + FallbackProvider
python tests/model_info_test.py     # per-model window/output limits, factory wiring
python tests/config_yaml_test.py    # .harness.yaml config + pipeline auto-push/PR
python tests/workspace_test.py      # opt-in workspace confinement (D27)
python tests/concurrency_test.py    # two sessions, zero state leakage (D28)
python tests/storage_test.py        # DB users/sessions, isolation, JSON->DB migration (D29)
python tests/token_test.py          # JWT issue/verify/expiry/tamper (D30)
python tests/usage_store_test.py    # durable usage rows + admin gating (D30)
python tests/hooks_test.py          # pre/post model+tool interception points (D32)
python tests/search_files_test.py   # find_files/grep_files, git_commit, event latency
python tests/sandbox_test.py         # Docker sandbox: isolation flags + gated real-container run (D33)
```

All twenty-four run against fakes — no key, no network — and should print `... PASSED`.
