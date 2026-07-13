# Architecture

How the harness is put together, how a request flows through it, and where to
plug new things in. For *why* it's built this way, see [DESIGN.md](DESIGN.md);
for the coding rules, see [PRINCIPLES.md](PRINCIPLES.md).

## The one-sentence model

An LLM is a text-in/text-out brain. The **harness** is the body around it: it
runs a loop that lets the model observe (read results), think (call the model),
and act (run tools) until the task is done. **The loop never changes; new power
is added as new tools, providers, and interfaces at the edges.**

## Layers

```
┌──────────────────────────────────────────────────────────────────┐
│ interfaces/     CLI + pipeline CLI now; Slack / HTTP API later      │  ← talks to humans/systems
├──────────────────────────────────────────────────────────────────┤
│ pipeline/       multi-stage autonomous loop (composes engine/)      │  ← optional outer layer
│ multiagent/     delegate-to-sub-agent tool (composes engine/)       │  ← optional outer layer
├──────────────────────────────────────────────────────────────────┤
│ engine/         orchestrator (the loop) + permissions + registry    │  ← policy / control flow
│                 + MCP client + built-in tools (engine/builtin/)     │     + the agent's hands
├──────────────────────────────────────────────────────────────────┤
│ context_engine/ compaction + memory tool + activity tracker         │  ← what the agent remembers
│                 + session store                                     │
├──────────────────────────────────────────────────────────────────┤
│ providers/      Provider interface + per-model adapters             │  ← the agent's mouth/ears
└──────────────────────────────────────────────────────────────────┘
   config.py       settings resolved once, injected at the edge
   observability/  usage/cost tracking + event logging               ┐ cross-cutting:
                                                                       ┘ touch every layer
```

`pipeline/` and `multiagent/` both sit *above* `engine/`, not inside it: each
calls `Orchestrator.run()` — repeatedly for a stage sequence, or once per
delegated sub-task — instead of changing what the loop does. See D15 and
D17 in [DESIGN.md](DESIGN.md).

Dependencies point **downward and inward, toward abstractions**. `engine/`
depends on the `Provider` interface, the `Registry`, and `context_engine/`
(for `Conversation`), never on a concrete provider or an interface. Wiring
happens only in `interfaces/` via `providers/factory.py`.

`auth/` isn't in the stack above because nothing below `interfaces/` needs
to know accounts exist: `interfaces/cli.py` is the only caller of
`auth/users.py`, and it uses `Config.for_user(username)` to turn "who's
logged in" into ordinary `Config` fields (`sessions_dir`, `memory_dir`, ...)
before anything else is constructed. `engine/` and `context_engine/` just
see a `Config` with different paths in it — same as any other config value
(D22).

## Component reference

| File | Type(s) | Responsibility |
|------|---------|----------------|
| `config.py` | `Config`, `Config.for_user` | Resolve settings from env/`.env` once; namespace per-user dirs (D22) |
| `auth/users.py` | `UserStore`, `hash_password`, `verify_password` | Salted/hashed username+password accounts (D22) |
| `providers/base.py` | `Provider`, `Response`, `ToolCall` | The model abstraction + normalized data |
| `providers/anthropic_provider.py` | `AnthropicProvider` | Neutral ↔ Claude API translation |
| `providers/openai_provider.py` | `OpenAIProvider` | OpenAI + any compatible endpoint |
| `providers/factory.py` | `build_provider` | Model string → concrete provider |
| `engine/registry.py` | `Tool`, `Registry`, `registry` | Hold tool schemas; dispatch by name |
| `engine/builtin/filesystem.py` | read/write/edit/list | Filesystem tools (self-register) |
| `engine/builtin/shell.py` | run_command | Shell tool (self-register) |
| `engine/builtin/planning.py` | `todo_write`, `todo_read`, `reset_plan` | Explicit step-by-step plan as a tool (self-register) (D23) |
| `engine/builtin/search.py` | `build_search_tool` | `web_search` via Tavily; opt-in on an API key, not self-registered (D24) |
| `engine/mcp_client.py` | `MCPManager`, `MCPServerConfig` | Connect to MCP servers; register/deregister their tools (D14) |
| `context_engine/memory_tool.py` | `memory`, `set_memory_root` | The model's own view/create/str_replace/insert/delete/rename tool (D16) |
| `engine/builtin/offload.py` | `maybe_offload`, `set_offload_root` | Oversized tool output -> file + preview, instead of hard truncation (D19) |
| `engine/permissions.py` | `check` | allow / ask / deny decision |
| `context_engine/compaction.py` | `Conversation`, `make_provider_summarizer` | Hold history; compact it when over budget |
| `engine/orchestrator.py` | `Orchestrator` | The agent loop + tool gating |
| `context_engine/session_store.py` | `SessionStore` | Save/load a conversation as JSON |
| `observability/usage.py` | `UsageTracker`, `cost_for` | Accumulate tokens; estimate spend |
| `observability/log.py` | `EventLogger` | Append a JSONL trace of events |
| `context_engine/memory_tracker.py` | `MemoryTracker` | Automatic "what am I working on" summary, independent of `context_engine/memory_tool.py` (D16) |
| `interfaces/cli.py` | `main`, `Session` | Terminal I/O, approvals, session commands, MCP wiring, `/model` switching (D21) |
| `pipeline/runner.py` | `PipelineRunner` | Outer multi-stage loop: implement → self-review → verify → test → sync-docs (D15) |
| `pipeline/worktree.py` | worktree/commit helpers | Isolated git worktree per slice; the stuck-detection signal |
| `pipeline/state.py` | `SliceState`, `ProgressLog` | Persist slice status + an append-only progress trail |
| `pipeline/stages.py` | stage prompt builders | One prompt template per stage; reused directly by the CLI's skill commands |
| `pipeline/external_skills.py` | `ExternalSkill`, `load_external_skills` | User-defined skills from `.harness/skills.json`, merged with the built-ins (D18) |
| `interfaces/pipeline_cli.py` | `main` | `python pipeline.py "<task>"` entry point |
| `multiagent/coordinator.py` | `build_delegate_tool`, `FilteredRegistry` | The `delegate` tool + the live registry view that hides it from sub-agents (D17) |
| `multiagent/roles.py` | `AgentRole`, `load_roles` | Sub-agent roles loaded from `.harness/roles.json` |

## The request lifecycle

What happens on one `agent.run("...")` call (`engine/orchestrator.py`):

```
user text ─▶ append {role:user} to history
             │
   ┌─────────▼──────────── loop (up to config.max_steps) ───────────┐
   │ conversation.maybe_compact()  (fold old history if over budget) │
   │ provider.complete(conversation.to_list(), registry.specs())     │
   │        └─ provider translates history → native, calls model,    │
   │           translates reply → neutral Response (+ token usage)    │
   │ append Response.assistant_message; record usage in UsageTracker  │
   │                                                                  │
   │ Response.tool_calls empty?  ── yes ─▶ return Response.text  (done)│
   │        │ no                                                       │
   │        ▼  for each tool_call:                                    │
   │   permissions.check(tool, args, mode)                            │
   │        ├─ allow ─▶ registry.run(name, args)                      │
   │        ├─ ask   ─▶ approver(call, tool) ? run : "denied"         │
   │        └─ deny  ─▶ "blocked by policy"                           │
   │   append {role:tool, tool_call_id, content:result} to history    │
   └──────────────────────────────────────────────────────────────────┘
```

Two callbacks let the interface participate without the core knowing about it:
- **`approver(call, tool) -> bool`** — how a human answers an "ask" decision.
- **`on_event(kind, *details)`** — progress reporting (`thinking`, `tool_call`,
  `tool_result`, `denied`).

The CLI supplies both; a future Slack bot supplies its own. The core is unaware
which interface it's serving.

## The message-format contract (important)

There is exactly **one internal ("neutral") message format**, and it is
OpenAI-style. Everything in `engine/` and `interfaces/` uses only this. Providers
are the *only* place allowed to know a native format.

Neutral messages are dicts with these shapes:

```python
{"role": "system",    "content": "<text>"}
{"role": "user",      "content": "<text>"}
{"role": "assistant", "content": "<text>",           # may be ""
 "tool_calls": [                                      # optional
   {"id": "<id>", "type": "function",
    "function": {"name": "<tool>", "arguments": "<json-string>"}}]}
{"role": "tool",      "tool_call_id": "<id>", "content": "<result string>"}
```

- `OpenAIProvider` passes these straight through (this *is* OpenAI's format).
- `AnthropicProvider` translates them: pulls `system` out as a separate argument,
  turns `tool_calls` into `tool_use` blocks, and folds consecutive `tool` results
  into a single Anthropic user turn (`_translate_messages`).

A `Provider.complete` always returns a `Response` with:
- `text`: the model's natural-language output (or `None`),
- `tool_calls`: normalized `ToolCall`s (empty ⇒ the model is finished),
- `assistant_message`: the neutral dict to append back to history.

If you add a provider, its job is entirely: **neutral in, neutral out.**

## Tool schema contract

`Registry.specs()` emits OpenAI function-tool schemas:

```python
{"type": "function",
 "function": {"name": ..., "description": ..., "parameters": <JSON Schema>}}
```

Each `Tool` also carries a `risk` (`safe` | `write` | `dangerous`) that drives
the permission layer. Handlers receive validated kwargs and **return a string**.

## MCP tools (dynamic, not self-registered)

Built-in tools self-register on import (D8). MCP server tools don't exist
until you connect, so `MCPManager.connect(config)` registers them at runtime
instead, namespaced `mcp__<server>__<tool>` (same convention this very
session's own MCP tools use) so they can't collide with a built-in tool or
another server's tool of the same name. Risk is derived from the server's own
MCP tool annotations when present, else assumed `write` (D14) — from there
they're indistinguishable from any other `Tool` to the orchestrator and
permission layer. `disconnect(name)` deregisters them; `list_connected()`
reports what's live. The CLI wires this at startup from `.harness/mcp.json`
(see `mcp.json.example`) and via `/mcp`, `/mcp connect`, `/mcp disconnect`.

## Memory (two independent pieces)

"Memory" is deliberately not one component:

- `context_engine/memory_tool.py` — a plain tool (`view`/`create`/`str_replace`/`insert`/
  `delete`/`rename` over a confined directory) the model calls when it
  decides something is worth remembering across turns or sessions. Same
  shape as every other tool — nothing provider-specific (D16).
- `context_engine/memory_tracker.py`'s `MemoryTracker` — listens on the same
  `on_event` stream as `EventLogger` and automatically maintains
  `<memory_dir>/activity.md`: the current task, files touched, tool usage
  counts. Works whether or not the model ever calls the memory tool.

Neither imports the other. `interfaces/cli.py` is the only place that knows
both exist: it points `context_engine/memory_tool.py` at `config.memory_dir` via
`set_memory_root()`, and fans `on_event` out to both `EventLogger` and
`MemoryTracker` via `_make_event_handler(*listeners)` — any object with a
`log(kind, *details)` method can be added or removed there as a one-line
change, with no signature change anywhere else. `/memory` prints the current
`MemoryTracker` summary.

## Skills (named, on-demand instructions)

The interactive CLI's `/review`, `/verify`, `/test`, `/docs` commands are
`pipeline/stages.py`'s prompt builders invoked individually, mid-conversation,
instead of only as a fixed pipeline sequence — `interfaces/cli.py`'s
`_handle_skill_command` builds the prompt (task text: an explicit argument,
else the current `MemoryTracker.task`; diff: `pipeline/worktree.diff_stat(".")`,
or a graceful "(not a git repository)" fallback) and feeds it into the
*existing* `session.agent.run(...)` — same conversation, same context, not a
fresh isolated run like the pipeline uses. `pipeline/stages.py` itself stays
a dependency-free leaf module used by both callers; neither `pipeline/runner.py`
nor `interfaces/cli.py` knows about the other's use of it.

**Adding a skill without touching Python:** `.harness/skills.json`
(`pipeline/external_skills.py`'s `load_external_skills`) defines more skills
as `{"name": {"description", "prompt"}}`, where `prompt` is a template with
`{task}`/`{diff_stat}` placeholders. `main()` merges these into the same
dict that holds the four built-ins before anything ever dispatches on it —
`_handle_command`/`_handle_skill_command` never know or care which source a
given skill came from (D18).

## Multi-agent (delegation as a tool)

If `.harness/roles.json` defines any roles, `interfaces/cli.py` registers one
extra tool, `delegate(role, task)` (`multiagent/coordinator.py`), onto the
shared `registry`. Calling it runs a fresh `Orchestrator` with a role-specific
system prompt and returns its final answer as the tool result — from the
calling agent's perspective, delegating to a sub-agent looks exactly like
calling any other tool. Sub-agents share the coordinator's `Config` (model,
permission_mode, `memory_dir`) and approver, and see every tool the
coordinator currently has *except* `delegate` itself (`FilteredRegistry`) —
one level of delegation only, structurally, not by a depth counter. See D17
in [DESIGN.md](DESIGN.md) for the full rationale, including why this doesn't
reverse D1.

## The pipeline (outer loop, optional)

`pipeline/` is a second entry point (`python pipeline.py "<task>"`,
`interfaces/pipeline_cli.py`) for autonomous multi-stage work, sitting above
`engine/` rather than inside it. One `PipelineRunner.run(task)` call:

```
create an isolated git worktree + branch
  │
  ▼  implement loop (bounded by max_iterations / slice_timeout_s)
  │   each iteration: fresh Orchestrator.run() seeded with the task,
  │   progress.log, and the current `git diff --stat`
  │   no uncommitted change this iteration? -> stuck_count++
  │   any change?  -> commit it, stuck_count = 0
  │   answer contains <promise>COMPLETE</promise> -> stop, proceed below
  │   answer contains <promise>ABORT</promise>    -> stop, report why
  │   stuck_count >= stuck_after                  -> stop ("stuck")
  │   iteration > max_iterations                  -> stop ("max_iterations")
  ▼
self_review -> verify -> test  (each: one fresh Orchestrator.run(), commit if changed)
  │   test answer contains <tests>FAIL</tests> -> repair (bounded by
  │   max_repair_attempts), re-run test, repeat
  ▼
sync_docs -> stop (no push/PR in v1 — hand back the committed branch)
```

Every stage is an ordinary, bounded call to the *unmodified*
`engine.orchestrator.Orchestrator` — the pipeline adds no new capability to the
loop itself, only a calling pattern around it (D15). Because no human is
present to answer an "ask" permission decision during an autonomous run, the
pipeline's approver always denies rather than always allows; run with
`HARNESS_PERMISSION_MODE=allowlist` or `auto` for it to make progress.

## Runtime model switching (`/model`)

`Session.switch_model(model)` (`interfaces/cli.py`) rebuilds the provider from
a new model string without losing the conversation: it builds a fresh `Config`
(`dataclasses.replace`, only `model` differs) and a fresh `Provider` via
`build_provider`, points the *existing* `Conversation`'s summarizer at the new
provider, and rebuilds the `Orchestrator` around the same conversation object.
If `build_provider` rejects the new model string, nothing is mutated — the
session keeps its old provider/config/agent untouched (D21). No new
abstraction: this is the same `build_provider(config)` call `main()` already
makes once at startup, just callable again mid-session.

## Multi-user login (`auth/`)

`interfaces/cli.py`'s `main()` calls `_login(config.users_config_path)`
before anything else is built. `_login` reads `auth/users.py`'s `UserStore`
(a JSON file, same external-config shape as `.harness/mcp.json` /
`roles.json` / `skills.json`): an unrecognized username walks through account
creation (choose + confirm a password), a recognized one is checked against
its stored salted PBKDF2 hash. Either way it returns a username, which
`main()` immediately turns into `config = config.for_user(username)` —
`sessions_dir`, `memory_dir`, `logs_dir`, and `offload_dir` all get the
username appended, so two logged-in users never read or write each other's
data even though they share the same process's `registry`, MCP connections,
and org-wide `.harness/*.json` config (D22). `HARNESS_USER`/`HARNESS_PASSWORD`
skip the interactive prompt for scripted/demo runs.

## Planning (`todo_write` / `todo_read`)

`engine/builtin/planning.py` holds one ordered checklist in a module-level
list -- `todo_write(steps)` replaces it wholesale, `todo_read()` renders it.
No new orchestrator concept: it's a tool like any other, just one whose
"state" is the plan itself rather than a side effect on disk. `Session.reset()`
and a successful `/load` both call `reset_plan()` so a new or resumed
conversation doesn't inherit a stale plan (D23).

## Web search (`web_search`, opt-in)

`engine/builtin/search.py`'s `build_search_tool(api_key)` wraps the Tavily
API over plain `urllib` (no new dependency, same as `fetch_url`). It's the
one `engine/builtin/` tool that does **not** self-register on import --
`interfaces/cli.py` and `interfaces/pipeline_cli.py` each call
`registry.register(build_search_tool(config.search_api_key))` only when
`HARNESS_SEARCH_API_KEY` is set, so an unconfigured harness simply doesn't
have the tool rather than having one that always errors (D24).

## Extension points (where new work goes)

| To add… | Do this | Files touched |
|---------|---------|---------------|
| A tool | Define a `Tool`, `registry.register(...)`, import the module | new `engine/builtin/x.py` |
| A provider | Subclass `Provider`, add a branch in the factory | new `providers/x.py`, `factory.py` |
| An interface | New module supplying `approver` + `on_event`, wire via `build_provider` | new `interfaces/x.py` |
| A permission mode | Extend `permissions.check` | `permissions.py` |

Step-by-step recipes are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Limits & safeguards (current)

- `config.max_steps` caps tool-using iterations per request (runaway guard).
- `config.max_tokens` bounds model output per turn.
- `config.max_context_tokens` triggers history compaction before the window
  overflows; `keep_recent_messages` stays verbatim.
- Tool outputs over ~20k chars are offloaded to a file (`engine/builtin/offload.py`,
  D19) instead of truncated away — a preview stays inline, the rest is
  recoverable via `read_file`.
- The permission layer gates every action; `dangerous` tools never run silently
  outside `auto` mode.
- Sessions auto-save after each turn (`context_engine/session_store.py`); every
  event is traced to a JSONL log (`observability/log.py`).

## Roadmap position

- **Phase 1 (done):** loop, model-independence, permissions, filesystem/shell
  tools, CLI, smoke test.
- **Phase 2 (done):** context management (history compaction), session
  persistence, observability + cost tracking, `fetch_url` web tool.
- **Phase 3 (in progress):** MCP client (`engine/mcp_client.py`, dynamic
  tools), autonomous multi-stage pipeline (`pipeline/`, stops before push/PR),
  agent memory (`context_engine/memory_tool.py` + `context_engine/memory_tracker.py`,
  D16), skill commands (`/review`/`/verify`/`/test`/`/docs`), multi-agent
  delegation (`multiagent/`, D17), runtime model switching (`/model`, D21),
  multi-user login + per-user session isolation (`auth/`, D22), an explicit
  planning tool (`todo_write`/`todo_read`, D23), opt-in web search (`web_search`
  via Tavily, D24). Still open: HTTP API, Slack interface, sandboxed/isolated
  execution, a browser tool, pipeline push/PR automation, cross-model review,
  parallel slices, labeling sub-agent activity in the CLI output, and account
  management beyond signup/verify (no password reset, no roles/permissions
  per account yet).

These phases slot into the existing folders without reshaping the loop.
