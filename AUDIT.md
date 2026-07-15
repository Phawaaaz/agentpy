# AUDIT — Agentic Harness

Full discovery + gap analysis against the LangChain "Anatomy of an Agent Harness"
component model, LangGraph/deepagents' out-of-the-box primitives, and the
product requirements (multi-provider model layer, client/server-ready
architecture, multi-user sessions, per-session + long-term memory, sandbox
planned, storage undecided).

Methodology: every source file was read in full (43 Python modules, ~4,300
LOC excluding tests; 1,974 LOC of tests). All 14 test files were installed
and run (`pip install -r requirements.txt` then each `python tests/X_test.py`)
— **all 14 pass**, including one (`config_yaml_test.py`) not mentioned in
README/AGENTS.md's documented list of 13. No claim below is "probably" —
each cites a file and, where relevant, a line range.

---

## 1. Component Map

One line per module, based on reading the code:

| File | What it actually does |
|---|---|
| `config.py` | `Config` dataclass resolved once from env/`.env`/`.harness.yaml`; `Config.for_user()` namespaces 4 directories by username with regex validation against path traversal. |
| `main.py` | Entry point, delegates to `interfaces.cli.main`. |
| `pipeline.py` | Entry point, delegates to `interfaces.pipeline_cli.main`. |
| `providers/base.py` | Abstract `Provider` (one method: `complete`), plus neutral `ToolCall`/`Usage`/`Response` dataclasses. |
| `providers/anthropic_provider.py` | Translates neutral OpenAI-style history ↔ Claude's native shape (system extraction, `tool_use`/`tool_result` blocks, coalescing consecutive tool results into one user turn). |
| `providers/openai_provider.py` | Pass-through wrapper over the OpenAI SDK; accepts `base_url` so it drives any OpenAI-compatible endpoint. |
| `providers/factory.py` | `build_provider(config)`: model-string prefix → concrete `Provider`. Hardcoded map of 5 OpenAI-compatible prefixes (`openai`, `ollama`, `openrouter`, `groq`, `together`) + `anthropic`; unknown prefix + `base_url` set still works; otherwise raises. |
| `engine/orchestrator.py` | The agent loop: append user msg → compact if needed → `provider.complete()` → append assistant msg → record usage → if no tool calls, return text → else run each tool through `permissions.check` → append tool results → repeat up to `max_steps`. **No try/except around `provider.complete()`** — a raised exception (network error, rate limit, malformed SDK response) propagates out of `run()` uncaught. |
| `engine/registry.py` | `Tool` dataclass + `Registry` (dict by name); `run()` catches all exceptions from handlers so a tool never crashes the loop. One module-level `registry` singleton. |
| `engine/permissions.py` | Pure function `check(tool, args, mode) -> allow/ask/deny` from `tool.risk` × `mode` (`ask`/`allowlist`/`auto`). Coarse: 3 risk levels, no per-argument policy. |
| `engine/mcp_client.py` | `MCPManager`: connects to MCP servers over stdio/SSE/streamable-HTTP, runs one background asyncio event-loop thread, registers/deregisters `mcp__<server>__<tool>` tools live. Risk inferred from `readOnlyHint`/`destructiveHint` annotations, defaulting to `write`. |
| `engine/builtin/filesystem.py` | `read_file`, `write_file`, `edit_file` (single-occurrence exact-match replace), `list_dir`. **No path confinement of any kind** — any absolute or `../` path the model supplies is opened directly. |
| `engine/builtin/shell.py` | `run_command`: `subprocess.run(shell=True)` in the process's current working directory, 60s default timeout, risk=`dangerous`. |
| `engine/builtin/git_tool.py` | Read-only `git_status`/`git_diff`/`git_log` wrapping `git` via subprocess, risk=`safe`. No commit/checkpoint tool exposed to the interactive agent (checkpointing exists only inside `pipeline/worktree.py`, not reachable from `main.py`). |
| `engine/builtin/github_tool.py` | `github_pr_create`/`github_pr_view`/`github_ci_status` wrapping the `gh` CLI via subprocess. |
| `engine/builtin/offload.py` | `maybe_offload(text, max_inline, label)`: over the limit, content-hashed write to `<offload_dir>/<label>-<sha256>.txt`, returns a 4k-char preview + path. Root is a **module-level global** (`_ROOT`), set once via `set_offload_root()`. |
| `engine/builtin/planning.py` | `todo_write`/`todo_read`: one **module-level global list** `_plan`, reset via `reset_plan()`. Not persisted; shared by every `Orchestrator` in the process (including `delegate` sub-agents). |
| `engine/builtin/search.py` | `build_search_tool(api_key)`: Tavily-backed `web_search`, built as a `Tool` object but **not self-registered** — a caller must call `registry.register(...)` explicitly, which `interfaces/cli.py` and `interfaces/pipeline_cli.py` only do when `HARNESS_SEARCH_API_KEY` is set. |
| `engine/builtin/web.py` | `fetch_url` (HTML→text) **and a second, always-self-registering `web_search`** tool backed by scraping DuckDuckGo's lite HTML endpoint via regex — registers unconditionally on import, no key needed. |
| `context_engine/compaction.py` | `Conversation`: owns `messages` + running `summary`; `maybe_compact()` folds the oldest messages (sliding the cut past leading `tool` messages so a result is never orphaned) into a summary via an injected `Summarizer` once `estimate_tokens()` (a `len(json)/4` heuristic) exceeds `max_context_tokens`. |
| `context_engine/memory_tool.py` | `memory` tool: `view`/`create`/`str_replace`/`insert`/`delete`/`rename` confined to a virtual root via `_resolve()` (path-traversal-safe, `ValueError` on escape). Root is a **module-level global** (`_ROOT`), set once via `set_memory_root()`. |
| `context_engine/memory_tracker.py` | `MemoryTracker`: automatic, tool-call-free activity log (`current task`, `files touched`, `tool usage counts`) written to `<memory_dir>/activity.md` on every `on_event("tool_call", ...)`. |
| `context_engine/session_store.py` | `SessionStore`: JSON file per session id (`<dir>/<id>.json`) holding `Conversation.snapshot()`. `save`/`load`/`list_ids`, filename sanitized against traversal. |
| `auth/users.py` | `UserStore`: JSON file `{username: {hash, salt}}`, PBKDF2-HMAC-SHA256 (200k iterations, stdlib only), username regex-validated at registration. No token/session concept — verifies a password and returns nothing else. |
| `observability/log.py` | `EventLogger`: append-only JSONL per session (`ts`, `kind`, `details`). Swallows write errors so logging never breaks a run. |
| `observability/usage.py` | `UsageTracker` + a hardcoded substring-matched `PRICING` table (6 model families); unknown models report `$0` and flag themselves as unpriced. |
| `interfaces/cli.py` | The interactive REPL: login, builds `Session`/`SessionStore`/`MCPManager`, slash-command dispatch (`/new /save /load /sessions /cost /memory /model /whoami /mcp /roles` + skills), auto-saves after every turn, tears down MCP connections in a `finally`. |
| `interfaces/pipeline_cli.py` | `python pipeline.py "<task>"` entry point: builds provider/registry, warns if `permission_mode=ask` (pipeline's approver always denies), runs `PipelineRunner.run()`, prints the result summary. No top-level `try/except` — an uncaught exception from deep in the loop crashes the process. |
| `pipeline/runner.py` | `PipelineRunner`: isolated git worktree + branch per "slice", bounded implement loop (stuck detection via `git status --porcelain`, iteration cap, wall-clock timeout) → self_review → verify → test (with a bounded repair loop) → sync_docs. **Also contains a complete, tested `auto_push`/`auto_pr` path** (pushes the branch and calls `github_pr_create` when `PipelineConfig.auto_push`/`auto_pr` are set) — both default `False`, but the capability is fully implemented and covered by `tests/config_yaml_test.py::test_pipeline_runner_auto_push_and_pr`. |
| `pipeline/config.py` | `PipelineConfig`: iteration/stuck/timeout/repair/auto_push/auto_pr, same env→yaml→default resolution pattern as `Config`. |
| `pipeline/stages.py` | Pure prompt-template functions (`implement_prompt`, `self_review_prompt`, `verify_prompt`, `test_prompt`, `repair_prompt`, `sync_docs_prompt`), each ending in a shared `<promise>COMPLETE/ABORT</promise>` instruction block. Dependency-free; reused directly by `interfaces/cli.py`'s skill commands. |
| `pipeline/state.py` | `SliceState` (JSON snapshot) + `ProgressLog` (append-only text trail each fresh iteration reads instead of carrying a growing conversation — the "Ralph loop" pattern). |
| `pipeline/worktree.py` | Plain subprocess `git worktree add/remove`, `diff --stat`, `status --porcelain`, `commit -A`, `push`. Deterministic infrastructure, not a model tool. |
| `pipeline/external_skills.py` | `load_external_skills(path)`: `.harness/skills.json` → `{name: ExternalSkill}`, `str.replace`-based `{task}`/`{diff_stat}` substitution (not `str.format`, so stray braces in a user prompt don't break it). |
| `multiagent/coordinator.py` | `build_delegate_tool`: one `delegate(role, task)` tool that spins up a fresh `Orchestrator` with a role's system prompt against a `FilteredRegistry` (hides `delegate` itself — structural one-level-deep limit, not a counter). Sub-agent shares the coordinator's `Config` (hence `memory_dir`), `Provider`, and `approver`. |
| `multiagent/roles.py` | `load_roles(path)`: `.harness/roles.json` → `{name: AgentRole(description, system_prompt)}`. |
| `tests/*.py` (14 files) | All against fakes — `FakeProvider` scripted responses, mocked MCP sessions, temp dirs — no network, no API key. All pass (`Bash` run, this session). |

**Dead code / stubs / TODOs found:** none of the disallowed kind. `grep`
across all non-test `.py` files for `TODO|FIXME|XXX|NotImplementedError|stub`
returns exactly one hit: `providers/base.py:45`, the legitimate
`raise NotImplementedError` inside `Provider.complete`'s abstract-method body
— not a stub masquerading as done work. `CONTRIBUTING.md`'s `find_files`
example is documentation only; no such tool exists in `engine/builtin/`.

---

## 2. The agent loop, traced end to end

Entry point → termination, with exact locations:

1. **Entrypoint**: `main.py` → `interfaces/cli.py:main()` (`interfaces/cli.py:391`). Builds `Config`, logs in (`_login`, `interfaces/cli.py:343`), namespaces it per-user (`config.for_user`, `config.py:109`), builds a `Provider` (`providers/factory.py:24`), wires event listeners, optional tools (`delegate`, `web_search`), then a `Session` (`interfaces/cli.py:129`).
2. **Prompt assembly**: `Session._new_conversation()` (`interfaces/cli.py:142`) builds a `Conversation` from `config.system_prompt` (itself built once by `config.build_system_prompt()`, `config.py:52`, which appends `AGENTS.md` verbatim if present — the *only* dynamic system-prompt composition; there is no separate "tool descriptions" or "environment info" section, those live only in `Registry.specs()`, sent as the API's `tools` parameter, not the system prompt).
3. **Model call**: `Orchestrator.run()` (`engine/orchestrator.py:43`) — per iteration: `conversation.maybe_compact()` (`engine/orchestrator.py:49`), then `provider.complete(conversation.to_list(), registry.specs())` (`engine/orchestrator.py:52`).
4. **Tool dispatch**: for each `ToolCall`, `_handle_tool_call` (`engine/orchestrator.py:87`) looks the tool up in the registry, runs `permissions.check` (`engine/permissions.py:21`), and on `ASK` calls the injected `approver` (the CLI's is `interfaces/cli.py:90`, a blocking `input()` prompt).
5. **Result handling**: `registry.run(name, args)` (`engine/registry.py:53`) executes the handler inside a `try/except` that converts any exception to an error string; the result is appended as a `{"role": "tool", ...}` message (`engine/orchestrator.py:76-83`).
6. **Loop termination**: either `response.tool_calls` is empty (`engine/orchestrator.py:67`, model is done) or `config.max_steps` (default 25) is exhausted (`engine/orchestrator.py:85`, returns a `[stopped: ...]` sentinel string, not an exception).

**Gap found in this trace**: step 3's `provider.complete()` call has no
exception handling in `Orchestrator.run()` itself. A transient network error,
an API rate-limit (`anthropic.RateLimitError`, `openai.RateLimitError`), or a
malformed SDK response propagates straight out of `run()`. `interfaces/cli.py`
happens to catch it per-turn (`interfaces/cli.py:466-470`, prints `✗ error:`
and continues the REPL loop — but the just-appended user message stays in
history with no matching assistant reply, an inconsistent state). `interfaces/pipeline_cli.py`
has **no** such catch around `runner.run(task)` — an uncaught provider
exception crashes the whole pipeline process (`interfaces/pipeline_cli.py:73`).

---

## 3. Tools, config, persistence, external deps — full inventory

**Tools** (self-registering unless noted): `read_file`, `write_file`,
`edit_file`, `list_dir` (`engine/builtin/filesystem.py`); `run_command`
(`engine/builtin/shell.py`); `git_status`, `git_diff`, `git_log`
(`engine/builtin/git_tool.py`); `github_pr_create`, `github_pr_view`,
`github_ci_status` (`engine/builtin/github_tool.py`); `fetch_url` +
`web_search` (DuckDuckGo, `engine/builtin/web.py`, always registered);
`todo_write`, `todo_read` (`engine/builtin/planning.py`); `memory`
(`context_engine/memory_tool.py`); `web_search` (Tavily,
`engine/builtin/search.py`, opt-in — **name-collides with the one above**);
`delegate` (opt-in, `multiagent/coordinator.py`); any `mcp__<server>__<tool>`
from a connected MCP server (`engine/mcp_client.py`).

**Config surface**: 20 `Config` fields + 6 `PipelineConfig` fields, every one
resolvable from an env var, with a lower-priority `.harness.yaml`/`.harness.yml`
fallback (`config.py:131`, `pipeline/config.py:22`) and a hardcoded default —
documented in full in `.env.example`.

**Persistence mechanisms**: `SessionStore` (JSON per session,
`context_engine/session_store.py`), `UserStore` (JSON accounts,
`auth/users.py`), memory tool's virtual filesystem (`context_engine/memory_tool.py`),
`MemoryTracker`'s `activity.md` (`context_engine/memory_tracker.py`), offloaded
tool output (`engine/builtin/offload.py`), `EventLogger`'s JSONL
(`observability/log.py`), pipeline's `SliceState`/`ProgressLog` (`pipeline/state.py`).
**All of these are flat files on local disk. There is no database anywhere
in the codebase** (confirmed: no `sqlite3`, `sqlalchemy`, `psycopg`, or `.db`
file references outside this audit).

**External dependencies** (`requirements.txt`): `anthropic`, `openai`,
`python-dotenv`, `mcp`, `pyyaml`. That's the whole runtime dependency set —
no LiteLLM (deliberately dropped, D3), no web framework, no ORM, no auth
library (bcrypt/argon2/PyJWT), no container/sandbox SDK (`docker`, `firecracker`,
etc. — confirmed via `find . -iname "*docker*" -o -iname "*sandbox*"`, zero
hits outside `.git`).

---

## 4. Scored checklist

Legend: ✅ Implemented / 🟡 Partial / ❌ Missing / 🔵 Deliberately deferred.

### A. Model Layer & Switching Protocol

**A1. Provider-agnostic client (any provider).** 🟡 **Partial.**
`providers/base.py`'s `Provider` ABC + `AnthropicProvider`/`OpenAIProvider`
(`providers/anthropic_provider.py`, `providers/openai_provider.py`) cover
Anthropic natively and reach Ollama/OpenRouter/Groq/Together/any
OpenAI-compatible endpoint via `base_url` (`providers/factory.py:15-21`,
`OPENAI_COMPATIBLE`). This is genuinely "most models," but it is **not**
truly provider-agnostic: Google Gemini's native API, Cohere, Bedrock, and
any other non-OpenAI-shaped API each need a hand-written adapter (D3's own
documented trade-off). LiteLLM was tried and dropped for a real, recorded
reason (Windows long-path limit, `DESIGN.md` D3) — the trade-off is honest,
but the product requirement is "any provider via an OpenRouter/LiteLLM-style
abstraction," and this is a 2-adapter hand-rolled abstraction, not that.

**A2. Model switching via config and at runtime.** ✅ **Implemented.**
Config-time: `HARNESS_MODEL` env var (`config.py:157`). Runtime: `/model
<name>` (`interfaces/cli.py:221`, `Session.switch_model`,
`interfaces/cli.py:170-183`) rebuilds `Config`/`Provider` without losing
conversation history, verified by `tests/model_switch_test.py` (4 assertions,
all pass).

**A3. Normalization (tool calls, streaming, roles).** 🟡 **Partial.**
Tool-call formats and message roles are fully normalized through the neutral
OpenAI-style contract (`ARCHITECTURE.md`'s message-format contract,
implemented in `providers/anthropic_provider.py:_translate_messages`/`_translate_tools`).
**Streaming is not implemented anywhere** — `Provider.complete` is a single
blocking call returning one `Response`; DESIGN.md's own non-goals list
"Not streaming token-by-token output yet" and that is still true today.

**A4. Fallback & retry.** ❌ **Missing.** Grepped `providers/` for
`retry|backoff|RateLimit|Timeout`: zero hits beyond `json.JSONDecodeError`
handling of malformed tool arguments. No retry/backoff on transient errors,
no fallback model. A rate-limited call simply raises out of `Orchestrator.run()`
(see §2's loop-trace gap).

**A5. Per-model config (context window, max tokens, cost hooks).** 🟡 **Partial.**
Cost tracking exists and is real (`observability/usage.py`'s `PRICING` table,
substring-matched, unpriced models report `$0` honestly). **Context window
size and max-output-tokens are not per-model** — `config.max_tokens` (single
int, default 4096) and `config.max_context_tokens` (single int, default
100,000) apply identically regardless of which model is active; switching to
a model with a materially smaller or larger real window via `/model` does not
adjust either value.

### B. Context Engine

**B1. System prompt assembly from parts.** 🟡 **Partial.** Two parts, not
several: `DEFAULT_SYSTEM_PROMPT` + optional `AGENTS.md` content
(`config.py:52-73`). Memory is *not* injected into the system prompt (see B5
below); tool descriptions travel separately via `Registry.specs()`, not as
system-prompt text; there is no "environment info" (OS, cwd, date) section at
all.

**B2. Token budgeting.** ✅ **Implemented**, with a caveat already flagged
in `DESIGN.md`: `estimate_tokens` (`context_engine/compaction.py:19`) is a
`len(json.dumps(messages))//4` heuristic, not a real tokenizer — good enough
to trigger compaction, not exact.

**B3. Compaction.** ✅ **Implemented and tested.** `Conversation.maybe_compact()`
(`context_engine/compaction.py:52`) folds the oldest messages into a running
summary via an injected `Summarizer` once the token estimate exceeds budget,
sliding the cut past leading `tool` messages so no result is orphaned
(`_safe_cut_index`, `context_engine/compaction.py:68`). Verified by
`tests/phase2_test.py` (fake summarizer, real cut logic).

**B4. Tool-output offloading.** ✅ **Implemented and tested.**
`engine/builtin/offload.py`'s `maybe_offload` — content-hashed file +
preview, applied at 4 call sites (`read_file`, `run_command`, `fetch_url`,
`memory`'s `view`). Verified by `tests/offload_test.py`.

**B5. Progressive disclosure / skills loaded on demand.** 🟡 **Partial.**
Two different things both partially satisfy this: (a) `/review /verify /test
/docs` + user-defined skills (`.harness/skills.json`) are genuinely on-demand
prompt injections, not loaded at startup (`interfaces/cli.py:244`,
`_handle_skill_command`) — this is real progressive disclosure of
*instructions*. (b) **Tool schemas are not progressively disclosed** —
`registry.specs()` (`engine/registry.py:39`) sends every registered tool's
full schema on *every single model call*, with no lazy-loading or "load this
tool's docs only when relevant" mechanism (the LangChain-anatomy /
deepagents sense of this item, e.g. filesystem-based skill directories
loaded only when referenced). With MCP servers connected this list grows
unboundedly and there's no compaction of the tool-schema portion of context,
only the message-history portion.

### C. Tools, Execution Engine & MCP

**C1. Tool registry, zero-orchestrator-change extension.** ✅ **Implemented**,
genuinely Open/Closed — verified by reading `engine/registry.py` and every
`engine/builtin/*.py` module (all self-register via the same 3-line pattern),
and by `CONTRIBUTING.md`'s recipe matching the real code exactly.

**C2. Filesystem tools (read/write/edit/list/search).** 🟡 **Partial.**
Read/write/list: ✅ (`engine/builtin/filesystem.py`). Edit: ✅, targeted
(`edit_file` requires the old text to be unique — a real patch-style edit,
not overwrite-only, `engine/builtin/filesystem.py:39-58`). **Search/grep: ❌
missing as a dedicated tool** — there is no `grep_files`/`find_files`/`glob`
tool anywhere in `engine/builtin/`; `CONTRIBUTING.md`'s `find_files` example
is documentation-only and does not exist in the codebase (confirmed by
`Grep` across the whole tree). The only way to search files today is the
`dangerous`-risk `run_command` escape hatch.

**C3. Bash/code-execution escape hatch.** ✅ **Implemented.** `run_command`
(`engine/builtin/shell.py`), correctly risk=`dangerous`.

**C4. Web search / fetch.** 🟡 **Partial, with a real bug.**
`fetch_url` ✅. Web search exists **twice**, under the same tool name,
with contradictory registration semantics:
- `engine/builtin/web.py:web_search` — DuckDuckGo HTML scraping via regex,
  self-registers unconditionally on import (`engine/builtin/web.py:116-142`),
  imported unconditionally by both `interfaces/cli.py:39` and
  `interfaces/pipeline_cli.py:23`.
- `engine/builtin/search.py:build_search_tool` — Tavily API, registered only
  when `HARNESS_SEARCH_API_KEY` is set (`interfaces/cli.py:429-430`,
  `interfaces/pipeline_cli.py:68-69`), which **silently overwrites** the
  DuckDuckGo tool in the registry dict (`Registry.register`,
  `engine/registry.py:25-27`, last write wins, no collision notice — unlike
  the skills-collision path, which does print one, `interfaces/cli.py:411-412`).

This directly contradicts the documentation: `DESIGN.md` D24 states "Unlike
the other built-in tools, it's opt-in: with no key set, the tool simply isn't
registered, rather than being present and always failing" — **false as
written**, because `engine/builtin/web.py`'s `web_search` is always present
regardless of the key. `DESIGN.md`'s own D24 rationale ("the first pass ...
tried DuckDuckGo's free, keyless endpoints and found them unreliable in live
testing ... not something to ship right before a demo") describes *exactly*
the tool that is still shipping by default today. This looks like the
DuckDuckGo tool was meant to be removed when Tavily replaced it and wasn't.

**C5. MCP client (stdio + HTTP/SSE, discovery, exposure).** ✅ **Implemented,
well-built.** `engine/mcp_client.py`'s `MCPManager` supports all three
transports (`stdio_client`, `sse_client`, `streamable_http_client`), runs a
dedicated background asyncio loop so tool handlers stay synchronous, connects
declaratively from `.harness/mcp.json` at startup and dynamically via
`/mcp connect|disconnect`, infers risk from MCP tool annotations. Verified by
`tests/mcp_test.py` (duck-typed fake session, no real subprocess/network).

**C6. Robust tool-error handling.** ✅ **Implemented.** `Registry.run`
(`engine/registry.py:53-63`) catches `TypeError` (bad args) and any other
`Exception` separately, always returns a string. Every built-in tool handler
additionally catches its own expected failure modes. No tool can crash the
loop — verified structurally (every handler reviewed) and by
`tests/memory_test.py`'s explicit "error paths OK (all strings, nothing
raised)" assertions.

### D. Filesystem & Workspace

**D1. Per-session, per-user isolated workspace directory.** ❌ **Missing.**
This is the most significant gap found in the audit. `Config.for_user`
(`config.py:109`) namespaces exactly four directories —
`sessions_dir`/`memory_dir`/`logs_dir`/`offload_dir` — by username. **There
is no workspace directory concept at all.** `read_file`/`write_file`/`edit_file`/
`list_dir` (`engine/builtin/filesystem.py`) and `run_command`
(`engine/builtin/shell.py`) operate directly on whatever path string the
model supplies, or on the process's real current working directory, with
**zero per-user or per-session confinement**. Two users of a hypothetical
future server sharing one process would have full read/write access to each
other's files, and to anywhere on disk the OS process can reach — the exact
opposite of the isolation `memory_tool.py` correctly implements for memory
files (`_resolve`, `context_engine/memory_tool.py:36-42`). The pipeline
(`pipeline/worktree.py`) *does* get real isolation, but only because it
`chdir`s into a git worktree per autonomous "slice" — a mechanism specific to
the pipeline, not reachable from the interactive CLI, and not designed
around `user_id`/`session_id` (it's a `slice_id`, tied to one git repo).

**D2. Path safety (no traversal, no absolute-path escape).** ❌ **Missing**
for the primary filesystem tools (see D1 — no confinement means no traversal
protection is even attempted, because there's no boundary to escape). ✅
**Implemented correctly** for the one place it *is* attempted:
`context_engine/memory_tool.py:_resolve` confines to an absolute root and
raises `ValueError` on escape — this is the pattern that should be, but
currently is not, applied to `engine/builtin/filesystem.py`.

**D3. Git integration for rollback/continuity.** 🟡 **Partial.**
Read-only git tools exist and are exposed to the interactive agent
(`git_status`/`git_diff`/`git_log`, `engine/builtin/git_tool.py`). Real
commit-based checkpointing exists and works (`pipeline/worktree.py:commit_all`,
used every pipeline iteration) but is pipeline-only infrastructure, not a
tool the interactive `main.py` session (or a future server session) can call
to checkpoint its own work. Design for it exists; it isn't wired to the
general case.

### E. Memory (session + long-term)

**E1. Session memory: full history persisted, listable, resumable.**
✅ **Implemented and tested.** `SessionStore` (`context_engine/session_store.py`)
+ `/save /load /sessions` (`interfaces/cli.py:293-310`), auto-save after
every turn (`interfaces/cli.py:474`). Verified by `tests/phase2_test.py`'s
session round-trip test.

**E2. Long-term per-user memory, model-writable, survives across sessions.**
✅ **Implemented and tested.** `context_engine/memory_tool.py`'s `memory`
tool operates on `config.memory_dir`, which is namespaced per-user (not
per-session) by `Config.for_user` — so it correctly persists *across*
sessions for the same user, per the product requirement. Verified by
`tests/memory_test.py`.

**E3. Memory injection at session start.** 🟡 **Partial.** The system
prompt *instructs* the model to check memory near the start of a task
(`config.py:44-48`, part of `DEFAULT_SYSTEM_PROMPT`), but nothing
**automatically loads** memory content into context at startup — it is
entirely dependent on the model choosing to call the `memory` tool with a
`view` command on its own initiative. If it doesn't, prior memory is
invisible for that turn. This is weaker than the product requirement
("relevant long-term memory is loaded into the system prompt/context at
startup").

### F. Session Management, Multi-User & Auth

**F1. Every session bound to a user_id; storage keyed by it; no cross-user
access.** 🟡 **Partial.** True for session/memory/logs/offload storage
(`Config.for_user`, path-validated). **Not true for the tool registry or the
module-global state** — see F3.

**F2. Session lifecycle: create/list/resume/delete.** 🟡 **Partial.**
Create ✅ (`/new`), list ✅ (`/sessions`), resume ✅ (`/load`). **Delete: ❌
missing** — `SessionStore` has no `delete`/`remove` method and there is no
`/delete` command; old sessions accumulate forever with no cleanup path.

**F3. Concurrency: two sessions don't corrupt each other's state.**
❌ **Missing at the architecture level** (harmless today only because the
CLI is a single-threaded, single-user-at-a-time REPL). Three module-level
globals are shared by *every* `Orchestrator` in the process regardless of
which user or session it belongs to:
- `engine/builtin/planning.py:_plan` — one plan for the whole process (already
  flagged as a known limitation in `DESIGN.md` D23, but scoped there only to
  "a delegate sub-agent sees the coordinator's plan" — the real blast radius
  is any two concurrent agents in one process, which is exactly the shape a
  client/server interface will have).
- `context_engine/memory_tool.py:_ROOT` — set once via `set_memory_root()`
  at CLI startup (`interfaces/cli.py:402`); a second concurrent user's memory
  calls would resolve against the *first* user's root.
- `engine/builtin/offload.py:_ROOT` — same pattern
  (`interfaces/cli.py:403`), same problem.

Additionally, `engine.registry.registry` (`engine/registry.py:67`) is one
process-wide `Registry` — an MCP server one user connects via `/mcp connect`,
or the `delegate`/`web_search` tools registered at startup, become globally
visible and callable by *every* user's agent in the same process, not scoped
to the session that requested them. **This is the architecture's single
biggest blocker to the stated "must be ready for a client/server interface"
requirement** — every consumer of these globals needs to become a
constructor parameter on `Orchestrator` (or session-scoped instances need to
be built per-request) before concurrent multi-user serving is safe. Today's
CLI never exercises this path (one login, one REPL, one process lifetime),
which is exactly why it hasn't surfaced as a bug yet.

**F4. Auth design (may be deferred, but must be planned).** 🟡 **Partial,
explicitly and honestly self-scoped in DESIGN.md.** Real hashing (PBKDF2-HMAC-SHA256,
200k iterations, random salt, `auth/users.py`) — genuinely not plaintext.
**No token issuance (JWT or otherwise), no session expiry, no token
verification** — `_login()` (`interfaces/cli.py:343`) returns a bare
username string, consumed once at process startup; there is no per-request
identity check because there are no requests, only one long-lived login. This
is fine for a CLI and is explicitly called out as not-a-security-boundary in
`DESIGN.md` D22's own trade-off section — but the *mapping* from "current CLI
user" to "future request-scoped token subject" does not exist yet even as a
sketch; `main()` calling `_login()` once and threading a plain string through
is the entire design.

### G. Sandbox

**G1. Execution isolation design.** ❌ **Missing entirely — no code, no
design doc.** Confirmed via `find . -iname "*docker*" -o -iname "*sandbox*"`:
zero hits outside `.git`. `run_command` runs directly on the host process
with no container, no resource limits (memory/CPU/disk), no network policy,
no command allow/deny-list beyond the three-tier `risk` permission gate
(which governs *whether to run*, not *what the command can reach once
running*). `README.md`'s only nod to this is the `auto` permission mode's own
doc caveat ("use only in a sandbox") — i.e. the harness already assumes a
sandbox will exist around it someday, but nothing constructs one. This
matches the product context ("Sandbox: planned, not yet built") exactly —
correctly an open item, not a broken one.

### H. Long-Horizon & Orchestration

**H1. Planning (todo/plan mechanism).** ✅ **Implemented, with the
concurrency caveat from F3.** `todo_write`/`todo_read`
(`engine/builtin/planning.py`), reset on `/new`/`/load`
(`interfaces/cli.py:165`, `:304`). Verified by `tests/planning_test.py`.

**H2. Self-verification / test-and-loop-on-failure.** 🟡 **Partial.**
✅ for the autonomous pipeline: `verify`/`test` stages plus a real bounded
repair loop (`pipeline/runner.py:_run_outer_stages`, lines 214-233, up to
`max_repair_attempts` re-implementation rounds gated on a `<tests>FAIL</tests>`
marker) — verified by `tests/pipeline_test.py`'s "repair loop on test
failure OK". ❌ for the interactive `main.py` loop: one `agent.run()` per
user turn with no automatic retry-until-verified; the system prompt asks the
model to "check your work after you change something," but nothing in
`Orchestrator` enforces or loops on that.

**H3. Continuation across context windows (Ralph-loop-style).** ✅
**Implemented for the pipeline, correctly evaluated as unnecessary for the
interactive CLI.** `pipeline/state.py`'s `ProgressLog` + a *fresh*
`Conversation` per implement iteration (`pipeline/runner.py:_run_stage`,
lines 131-154) is exactly this pattern — each iteration re-reads prior state
from disk instead of growing one conversation. The interactive CLI instead
relies on compaction (B3), which is the right tool for its different
shape (one long session vs. many bounded autonomous iterations) — this is a
considered design choice (`DESIGN.md` D15), not an oversight.

**H4. Subagent spawning with isolated context.** ✅ **Implemented, with the
F3 caveat.** `multiagent/coordinator.py`'s `delegate` tool: fresh
`Orchestrator`, fresh `Conversation`, role-specific system prompt,
`FilteredRegistry` hiding `delegate` itself (structural one-level limit).
Verified by `tests/multiagent_test.py` including a "sub-agent cannot
recursively delegate" assertion. Context is isolated (own `Conversation`
object) but **plan state is not** (shares the global `_plan`, per F3/D23) and
neither is memory root (shared by design — `DESIGN.md` D17 calls this out
explicitly and intentionally, "two agents sharing memory is simply two
`Orchestrator`s pointed at the same directory").

**H5. Hooks/middleware (pre/post tool-call, pre/post model-call).**
❌ **Missing as a general mechanism.** What exists: `on_event` (`EventHook`,
`engine/orchestrator.py:20`) is a **read-only observation** callback fired
*after* each event (thinking/tool_call/tool_result/denied/compacted/usage) —
listeners (`EventLogger`, `MemoryTracker`) can react to but not alter or
veto anything. `permissions.check` is the one real interception point, and
it is hardcoded into `Orchestrator._handle_tool_call`
(`engine/orchestrator.py:94-107`), not a pluggable chain — adding a new
cross-cutting concern (e.g. a guardrail that rewrites a tool's arguments, or
a pre-model-call context injector) requires editing the orchestrator itself,
which directly violates the project's own stated invariant ("you should
almost never edit `engine/orchestrator.py`," `AGENTS.md`). There is
currently no way to add compaction-like or verification-like behavior
without either baking it into the loop (as compaction and permissions
already are) or wrapping the whole loop from outside (as pipeline/multiagent
do) — there is no per-tool-call or per-model-call interception point an
extension author can hook without touching core code.

### I. Observability & Config

**I1. Structured logging of model + tool calls (inputs, outputs, tokens,
latency) per session.** 🟡 **Partial.** `EventLogger` (`observability/log.py`)
logs every event kind with full details, per-session JSONL, including token
usage (the `"usage"` event, `engine/orchestrator.py:59-63`). **Latency is not
recorded anywhere** — no timing wraps `provider.complete()` or
`registry.run()`; there's no way to answer "how long did that tool call
take" from the logs today.

**I2. Central config (file + env vars).** ✅ **Implemented.** `config.py`
+ `.env` + optional `.harness.yaml`, documented exhaustively in
`.env.example`. Verified by `tests/config_yaml_test.py`.

**I3. Graceful error handling throughout the loop.** 🟡 **Partial.** Tool
layer: ✅, airtight (C6). Provider layer: ❌, see A4/§2 — an SDK exception
is not caught inside `Orchestrator.run()` itself, only by whichever
interface happens to wrap the call (the CLI does; the pipeline CLI does not).

---

## 5. Framework benchmark — what LangGraph + deepagents give for free

| Capability | LangGraph / deepagents give this out of the box | This harness | Verdict |
|---|---|---|---|
| **Checkpointing / persistence** | A `Checkpointer` interface (SQLite/Postgres/Redis backends included) that snapshots full graph state after every step, with thread-scoped and cross-thread (long-term) memory stores built in. | `SessionStore` (JSON file, `context_engine/session_store.py`) saves after each *turn*, not each step; no built-in DB backend, no cross-thread store abstraction (the "long-term memory" here is the hand-rolled `memory` tool, not a framework-level store). | **Needs one.** Not urgent — JSON-per-session works for today's single-process CLI — but the product's storage decision (Phase 3) should treat this as the actual gap: a real `Checkpointer`-equivalent interface, swappable to Postgres, is exactly what "storage undecided, must support multi-user" calls for. |
| **Interrupts (human-in-the-loop)** | `interrupt()` pauses graph execution at any point and resumes later, from a different process even, driven by persisted checkpoint state — this is what a real approve/deny web UI would be built on. | The `ask` permission mode blocks synchronously on `input()` inside the same process/thread (`interfaces/cli.py:90-97`) — there is no way to pause a run, return control to a caller (e.g. an HTTP handler), and resume it later from persisted state. | **Needs one**, specifically *because* the product wants a client/server interface — a synchronous blocking `input()` call fundamentally cannot survive being deployed behind an HTTP request/response cycle. This is the second architecture blocker (after the F3 globals) for the server phase. |
| **State graph** | Arbitrary branching/looping control flow as a graph, with conditional edges. | Deliberately, permanently out of scope (D1: "one agent, one loop," a considered non-goal, not a gap). | **Legitimately doesn't need one** for the harness's stated ambitions (a company coding/automation assistant, not a complex multi-branch workflow engine) — D1's reasoning holds up under this audit; the fixed loop is genuinely simpler to reason about and every extension examined (pipeline, multiagent) composed around it without needing graph branching. |
| **Filesystem tools** | deepagents ships a virtual/sandboxed filesystem backend by default, isolated per agent invocation. | Real filesystem tools exist and are good (targeted edit, offload) but with **zero isolation** (D1/D2 above) — the opposite of deepagents' default. | **Needs one urgently** — this is the sharpest concrete gap in the whole audit relative to a framework default, and it's also a straightforward, well-scoped fix (confine `engine/builtin/filesystem.py`/`shell.py` to a workspace root the same way `memory_tool.py` already confines itself). |
| **Planning tool** | deepagents ships a `write_todos` planning tool matching this exact shape. | `todo_write`/`todo_read` (`engine/builtin/planning.py`) — independently converged on essentially the same design. | **Has an equivalent.** Only real deficiency vs. the framework default is session-scoping (global `_plan`, F3/D23), not the tool's shape or behavior. |
| **Subagents** | deepagents ships a general `task` tool to spawn subagents with isolated context, arbitrary depth (with prompting-based depth discipline). | `delegate` (`multiagent/coordinator.py`) — isolated `Conversation`, structurally capped at one level deep (stronger guarantee than deepagents' prompting-based limit, in fact). | **Has an equivalent**, arguably a stricter one. Shares the F3 caveat (global plan/memory-root) other subagent state does. |
| **Compaction middleware** | A pluggable middleware step that runs before/after each model call; compaction is one built-in instance of that generic mechanism. | Compaction exists (B3, well-built) but as **bespoke logic inside `Conversation`**, not an instance of any general pre/model-call hook — there is no middleware layer to plug a *second* concern (e.g. PII redaction, a guardrail, request shaping) into without writing bespoke code the same way compaction was written. | **Needs the general mechanism**, even though the one instance of it (compaction) that exists today is solid. This is the same gap as H5 above, viewed from the framework-comparison angle. |

**Overall read:** the custom loop itself (D1's bet) has held up well —
tool registry, permission gating, provider abstraction, and compaction are
all genuinely solid, tested, and match or exceed what a framework gives for
free in those specific areas. The gaps that matter are concentrated in
exactly the places the product's next phase (client/server, multi-user) will
stress hardest: **no workspace isolation, no middleware/interrupt mechanism
for anything beyond synchronous CLI blocking, and module-global state that
silently assumes one user per process.** None of these require the loop
itself to change — they're all "new file + wiring" fixes in the spirit of
the project's own Open/Closed principle — but they are real, and they are
exactly where a framework's defaults would have prevented the gap from
existing in the first place.

---

## 6. Summary scorecard

| Area | ✅ | 🟡 | ❌ | 🔵 |
|---|---|---|---|---|
| A. Model Layer | 1 | 3 | 1 | 0 |
| B. Context Engine | 2 | 2 | 0 | 0 |
| C. Tools/Execution/MCP | 3 | 2 | 0 | 0 |
| D. Filesystem & Workspace | 0 | 1 | 2 | 0 |
| E. Memory | 2 | 1 | 0 | 0 |
| F. Sessions/Multi-user/Auth | 0 | 3 | 1 | 0 |
| G. Sandbox | 0 | 0 | 1 | 0 |
| H. Long-Horizon | 3 | 2 | 1 | 0 |
| I. Observability/Config | 1 | 2 | 0 | 0 |
| **Total (23 items)** | **12** | **16** | **6** | **0** |

Nothing was scored 🔵 deliberately-deferred at the item level — every gap
found is either a real fix-now bug (the `web_search` collision), a design
decision the project already made consciously and documented (D1's fixed
loop, correctly not counted as a gap), or a genuine open item the owner
already flagged as planned (sandbox). The next phase's plan should treat D1
(filesystem/workspace), F3 (global state), and H5 (middleware/interrupts) as
the highest-leverage fixes: they block the two things the product roadmap
explicitly wants next (a safe multi-user server, and any future
human-in-the-loop approval flow that isn't a blocking terminal prompt).
