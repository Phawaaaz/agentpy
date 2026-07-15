# Design Decisions

The *why* behind the architecture. Each entry is a decision, its rationale, the
alternatives considered, and the trade-off accepted. This is where we record
deviations from our own [PRINCIPLES.md](PRINCIPLES.md) when we make them.

## Goals

- **Model-independent.** Plug in any model's key and it works.
- **Coding + general automation.** Useful to engineers and to the whole company.
- **Safe by default, configurable.** From ask-first to full autonomy, by setting.
- **Minimal core, grows at the edges.** Start small; extend without rewrites.
- **Understandable.** A new engineer can read the loop and get it in one sitting.

## Non-goals (for now)

- Not a multi-agent framework — one agent, one loop.
- Not streaming token-by-token output yet (Phase 2+).
- Not a hosted service yet — CLI first, interfaces later.

---

## Decisions

### D1 — A single fixed agent loop
**Decision:** one loop in `engine/orchestrator.py`: call model → run any requested
tools → feed results back → repeat until no tool calls.
**Why:** every agent behavior (coding, research, automation) reduces to this
loop. Keeping it fixed and small means the interesting variability lives in
tools, not control flow.
**Alternatives:** graph/state-machine engines (LangGraph-style). Rejected for
Phase 1 as over-engineering for a single-agent harness.
**Trade-off:** complex branching workflows will eventually want more structure;
we'll add it as an optional layer, not by complicating the base loop.

### D2 — Model-independence via a `Provider` abstraction
**Decision:** the orchestrator talks only to the `Provider` interface
(`providers/base.py`); concrete adapters implement it.
**Why:** this is the core requirement. It also gives Liskov substitutability,
which makes the loop testable with a fake (see `tests/smoke_test.py`).
**Trade-off:** each new native provider needs a translation layer. Accepted —
translation is isolated and small.

### D3 — Native SDKs (`anthropic`, `openai`) instead of LiteLLM
**Decision:** use the vendor SDKs behind our `Provider` interface, with the
OpenAI adapter accepting a `base_url` for any compatible endpoint.
**Why we changed course:** the original plan was LiteLLM (one lib, ~100
providers). It **failed to install on the target machine**: Windows' 260-char
path limit truncates a deeply nested file in LiteLLM's proxy module, and enabling
long-path support requires admin rights the user doesn't have.
**Why this is fine:** the OpenAI adapter + `base_url` reaches Ollama, OpenRouter,
Groq, Together, LM Studio, vLLM, and any OpenAI-compatible gateway; the Anthropic
adapter covers Claude natively. That's practically "any model," with lighter
dependencies and code that's easier to understand.
**Trade-off:** providers with a *non*-OpenAI, *non*-Anthropic native API (e.g.
raw Google Gemini) need their own adapter. Acceptable — it's one file, and the
abstraction was built for exactly this. This is a deliberate, recorded deviation
from the initial plan (per PRINCIPLES rule 0: write down why).

### D4 — One neutral message format (OpenAI-style)
**Decision:** the whole system stores history in OpenAI-style messages; providers
translate to/from their native shape at their boundary.
**Why:** the loop must not branch on provider. Picking one internal format and
pushing translation into providers keeps `engine/` provider-agnostic. OpenAI's
format was chosen because it's the most widely mirrored, so most adapters are
pass-throughs.
**Trade-off:** the Anthropic adapter carries real translation logic (system
extraction, `tool_use` blocks, coalescing tool results into user turns). Isolated
to one file; documented in ARCHITECTURE.md.

### D5 — Configurable permission modes with per-tool risk
**Decision:** every action passes `permissions.check(tool, args, mode)`, which
returns allow/ask/deny based on the tool's `risk` and the configured mode
(`ask` / `allowlist` / `auto`).
**Why:** the user explicitly wanted to choose between "ask first" and "full
autonomy." A company tool needs both — interactive for people, autonomous for
scheduled jobs. Risk lives *on the tool* so the policy stays declarative and the
check stays a small pure function.
**Trade-off:** risk is coarse (three levels). Fine for now; can grow to
per-argument policies (e.g. allow reads, block writes to `/etc`) without changing
the call site.

### D6 — Tools return strings and never raise
**Decision:** tool handlers catch their own errors and return an error string;
`Registry.run` is a final safety net.
**Why:** a tool failure is information the model should see and react to (retry,
try another path), not a crash. This keeps the loop robust and makes the agent
self-correcting.
**Trade-off:** genuine programming bugs in a tool can hide as strings. Mitigated
by keeping tools small and testable.

### D7 — Interface decoupled via two callbacks
**Decision:** the orchestrator takes an `approver` and an `on_event` callback
rather than referencing any UI.
**Why:** Interface Segregation + Dependency Inversion — the core doesn't know if
it's serving a CLI, Slack, or an API. Swapping interfaces is swapping two small
functions.
**Trade-off:** callbacks are less discoverable than a formal interface object. At
two functions, the simplicity wins; if it grows, promote to a small Protocol.

### D8 — A single shared `registry` singleton
**Decision:** `engine/registry.py` exposes one module-level `registry`; tool
modules register onto it on import.
**Why:** it makes adding a tool a one-liner with zero wiring, which is the whole
point of Open/Closed here. This is the *one* global we allow.
**Trade-off:** import-time side effects and shared global state. Contained by
convention: only tool modules touch it, and tests import the same modules. If we
ever need isolated tool sets per agent, we pass a `Registry` instance explicitly
(the orchestrator already accepts one) and drop the singleton.

### D9 — Config from env/`.env`, resolved once
**Decision:** `Config.load()` reads environment (and `.env`) into a frozen-ish
dataclass injected at startup.
**Why:** keeps secrets out of code, centralizes settings, and makes the rest of
the system take config as a parameter (testable, no hidden reads).
**Trade-off:** no live reconfiguration mid-run. Not needed yet.

### D10 — Context is a `Conversation`, separate from the loop (Phase 2)
**Decision:** move history out of the orchestrator into `context_engine/compaction.py`. The
`Conversation` owns messages and compaction; the orchestrator just calls
`add`/`to_list`/`maybe_compact`.
**Why:** Single Responsibility — "manage the window" is a different job from "run
the loop." It also isolates the token heuristic and cut logic for testing.
**Trade-off:** one more object to wire. Worth it; the loop got simpler.

### D11 — Compaction by injected summarizer, not hard-coded (Phase 2)
**Decision:** when history exceeds `max_context_tokens`, fold the oldest messages
into a running summary produced by an *injected* `Summarizer`. The cut slides
past leading `tool` messages so a tool result is never orphaned from its call.
**Why:** Dependency Inversion keeps `context_engine/compaction.py` free of any
provider import and testable with a fake summarizer (see `tests/phase2_test.py`).
The summary lives
in the system prompt, which sidesteps role/pairing issues entirely.
**Alternatives:** naive truncation (loses information) or no compaction (window
overflows). Summarization keeps the thread coherent.
**Trade-off:** compaction costs an extra model call. Acceptable and infrequent.

### D12 — Persistence as a swappable `SessionStore` (Phase 2)
**Decision:** `context_engine/session_store.py` serializes a `Conversation.snapshot()` to
JSON on disk and restores it. The CLI auto-saves after each turn.
**Why:** resumable sessions are essential for a real tool, and one narrow
interface (`save`/`load`/`list_ids`) means we can later back it with a database
without touching callers.
**Trade-off:** JSON files don't scale to many concurrent users. Fine for CLI;
Phase 3 can swap the backend behind the same interface.

### D13 — Usage/cost tracking at the provider boundary (Phase 2)
**Decision:** providers report token `Usage` on every `Response`; a
`UsageTracker` accumulates it and estimates cost from a small `PRICING` table.
**Why:** cost visibility is non-negotiable for a company-wide tool. Capturing
usage where the model call happens is the one place it's always available.
**Trade-off:** prices are hardcoded estimates that drift and don't cover every
model (unknown models report zero and say so). Accepted — it's guidance, not
billing, and the table is trivial to update.

### D14 — MCP tools are managed by a stateful `MCPManager`, not self-registered
**Decision:** `engine/mcp_client.py`'s `MCPManager` owns live connections to
external MCP servers (subprocess or network) and registers/deregisters their
tools onto the shared `registry` as servers connect/disconnect, instead of
the usual "tool module imports itself onto the registry" pattern (D8).
**Why:** every other tool is a plain function known at import time. An MCP
server's tools are dynamic (only known after connecting), and the connection
itself needs a lifecycle (connect, list, call, disconnect) a bare function
can't hold. Since the harness is otherwise synchronous and the `mcp` SDK is
async, `MCPManager` also owns one background asyncio event loop (a thread)
that every operation is dispatched onto and waited for synchronously — this
keeps every tool handler a plain `(**kwargs) -> str` function, so nothing
downstream (permissions, orchestrator, registry) needs to know a tool might
be remote.
**Risk mapping:** MCP servers are third-party and untrusted by default. Risk
is derived from the tool's own MCP annotations when present
(`readOnlyHint` → `safe`, `destructiveHint` → `dangerous`), else assumed
`write` — never silently trusted as `safe`. This still flows through the
existing `permissions.check` unchanged.
**Trade-off:** a second way for tools to enter the registry (import-time
self-registration vs. runtime `MCPManager.connect`). Accepted because the
two cases are genuinely different (static vs. live), and both still produce
plain `Tool` objects the rest of the system treats identically.

### D15 — The autonomous pipeline is a new package that composes the loop, not a change to it
**Decision:** `pipeline/` implements a higher-level, multi-stage loop
(implement → self-review → verify → test → sync-docs) by calling
`engine.orchestrator.Orchestrator.run()` repeatedly — once per stage/iteration,
each a fresh, bounded, ordinary run of the *unmodified* orchestrator.
**Why:** per D1, the base loop stays fixed and small. The pipeline's
additional structure (stuck detection, iteration caps, a wall-clock timeout,
repair caps, git-worktree isolation) is real complexity that a single
company-wide agent doesn't always want — it belongs in an optional layer
above the loop, not folded into it. Giving each stage a *fresh* `Conversation`
seeded from an append-only `progress.log` and the current `git diff --stat`
(rather than one long conversation carried across stages) means no stage's
context can grow unbounded, and `context_engine/compaction.py`'s compaction stays
irrelevant to the pipeline entirely.
**Stuck/timeout mechanism:** after each implement iteration, `git status
--porcelain` in the isolated worktree is the progress signal — no change for
`stuck_after` (default 3) consecutive iterations stops the loop, same idea as
`max_steps` but for *iterations that produce nothing* rather than iterations
that exceed a count. A wall-clock `slice_timeout_s` budget is checked
alongside `max_iterations`, since a single slow tool call can blow a
step-count budget without blowing a time budget or vice versa.
**Scope (v1):** stops before pushing or opening a PR — it hands back a
committed branch in an isolated worktree for a human to review and push.
Auto-push/PR, cross-model review, and parallel slices are deliberately
deferred (see ARCHITECTURE.md roadmap) rather than built speculatively.
**Trade-off:** running a slice unattended means no human can answer an "ask"
permission decision, so the pipeline's approver always denies rather than
always allows (fail safe, not fail open) — this makes `permission_mode:
allowlist` or `auto` a practical requirement for the pipeline to get
anywhere, which is called out at pipeline startup.

### D16 — Memory is two independent pieces, not one feature
**Decision:** "memory" is deliberately split into two components that don't
know about each other:
- `context_engine/memory_tool.py` — a plain neutral `Tool` (view/create/str_replace/
  insert/delete/rename over a confined directory) the *model* calls
  deliberately, exactly like `engine/builtin/filesystem.py`.
- `context_engine/memory_tracker.py`'s `MemoryTracker` — an automatic,
  harness-side listener that derives a standing "current task / files
  touched / tool usage" summary from the same `on_event` stream
  `observability/log.py`'s `EventLogger` already listens to, with **no
  import of `context_engine/memory_tool.py`** and no dependency in the other direction.
**Why not Anthropic's native `memory_20250818` tool type:** that type has a
fixed schema Claude expects natively — using it would mean the Anthropic
provider special-cases one tool while every other provider (OpenAI-compatible
endpoints) gets a plain function tool, breaking D3/D4's "one neutral schema,
providers translate, nothing upstream knows which model is running." A
hand-defined tool with the same view/create/str_replace/insert/delete/rename
convention gets the proven interface without the lock-in.
**Why two pieces, not one "Memory" class:** they solve different problems —
the tool is for content the *model* decides is worth keeping; the tracker is
bookkeeping the *harness* keeps regardless of whether the model ever calls
the tool. Fusing them would mean neither could be removed without touching
the other. As built: delete `context_engine/memory_tool.py` and `MemoryTracker` still
works (just without the model's own notes alongside it); delete
`MemoryTracker` and the memory tool still works (the model can still take
notes, there's just no automatic activity summary). Both default to writing
into the same `config.memory_dir` purely by convention — `interfaces/cli.py`
sets `context_engine.memory_tool`'s root from `config.memory_dir` explicitly at startup
(`set_memory_root`), rather than either module reaching for `Config` itself.
**Shared listener contract:** `EventLogger.log` was changed from
`(kind, **fields)` to `(kind, *details)` — the same shape as `MemoryTracker.log`
and as the orchestrator's own `on_event(kind, *details)` callback — so
`interfaces/cli.py`'s event fan-out (`_make_event_handler(*listeners)`) can
treat any number of listeners interchangeably. Adding or removing a listener
is a one-line change in `main()`, never a signature change.
**Trade-off:** two files instead of one; a human has to know both exist to
get the full picture. Accepted — independent removability is worth more than
the small discovery cost, and both are cheap to find from `context_engine/`'s
existing per-concern layout.

### D17 — Multi-agent is delegation-as-a-tool, not a second control flow
**Decision:** `multiagent/coordinator.py`'s `build_delegate_tool` produces one
tool, `delegate(role, task)`, that runs a fresh `Orchestrator` against a
role-specific system prompt and returns its final answer as the tool result.
Registered onto the shared `registry`, it's indistinguishable from any other
tool to the coordinator's own loop — delegating to a sub-agent *is* a tool
call, not a new kind of control flow.
**Why this reverses D1's "not a multi-agent framework":** it doesn't, quite —
D1's point was that the *base loop* stays one-agent-one-loop, and every
extension composes it from outside rather than complicating it. Delegation
follows that rule exactly: `engine/orchestrator.py` is untouched, and "an agent
that can spawn other agents" falls out of "a tool that happens to run another
`Orchestrator`," the same pattern `pipeline/runner.py` already established
for stages. What's genuinely new is the *policy* decision to allow it at all
— recorded here rather than silently reversing D1.
**Roles are external config**, loaded from `.harness/roles.json` (`load_roles`
in `multiagent/roles.py`), same pattern as `engine/mcp_client.py`'s server
list — adding a sub-agent role is a data change, not a Python change. No
roles configured (the default) means no `delegate` tool is registered at
all — multi-agent is opt-in, same as MCP.
**Shared memory is not new plumbing.** A sub-agent's `Config` is the
coordinator's own config with only `system_prompt` swapped (`dataclasses.
replace`) — `memory_dir`, `permission_mode`, and `model` all carry over
unchanged, and `context_engine/memory_tool.py`'s root is process-global (set once at
startup). Two agents sharing memory is simply two `Orchestrator`s pointed at
the same directory; nothing had to be built for it.
**One level of delegation, structurally, not by convention.** A sub-agent
gets `FilteredRegistry(registry, hidden={"delegate"})` — a *live* view of the
coordinator's registry (so MCP tools connected mid-session are still visible
to sub-agents) with `delegate` itself hidden. A sub-agent literally cannot
see the tool that would let it delegate further; there is no depth counter
to get wrong, because recursion isn't reachable in the first place. This
mirrors the same constraint Anthropic's own Managed Agents multiagent
sessions apply ("one level of delegation only, depth > 1 is ignored").
**`approver` is a required parameter on `build_delegate_tool`, not defaulted.**
`Orchestrator`'s own default approver is "allow everything" — silently
inheriting that default for a tool that spawns a full sub-agent (itself able
to call `write`/`dangerous` tools) would be the wrong failure mode. Callers
must pass one explicitly, same approver the coordinator itself uses.
**Trade-off:** the coordinator decides delegation dynamically (it's just
another tool the model can choose to call), rather than a fixed roster/stage
sequence — flexible, but means there's no built-in guarantee a sub-agent
ever gets called for a given task. Acceptable: that's true of every other
tool too, and prompting (the role descriptions in the tool's own
description) is the intended lever, same as any other tool-use trigger rate.

### D18 — External skills are the same JSON-config pattern, a third time
**Decision:** `pipeline/external_skills.py`'s `load_external_skills` loads
user-defined skills from `.harness/skills.json` (`{"skills": {"name":
{"description", "prompt"}}}`); `interfaces/cli.py`'s `main()` merges them
into the same `skills` dict that holds the four built-ins
(`_SKILLS`, sourced from `pipeline/stages.py`) and passes that one dict
through to `_handle_command`/`_handle_skill_command` — neither function
knows or cares whether a given skill came from Python or from JSON, both
are just `(task, diff_stat) -> str` callables.
**Why this is the third occurrence of the same shape:** `engine/mcp_client.py`
(external servers), `multiagent/roles.py` (external roles), and now this —
all three are "a directory of names -> small config objects, loaded from a
JSON file at startup, absent file means the feature is simply not there."
Worth naming explicitly: if a fourth thing needs external configurability,
reach for this exact shape again rather than reinventing it.
**Placeholder substitution uses `str.replace`, not `str.format`.** A user's
prompt template can legitimately contain other `{`/`}` characters (a JSON
example, a code snippet) that `str.format` would try to parse as fields and
fail on (`KeyError`/`IndexError`) — `.replace("{task}", task).replace(
"{diff_stat}", diff_stat)` only ever touches the two placeholders it knows
about and leaves everything else alone.
**Name collisions are allowed but surfaced.** An external skill named
`verify` silently shadowing the built-in `verify` would be a confusing way
to lose the pipeline's real verify prompt; `main()` prints a one-line notice
when this happens rather than either refusing to start or staying silent.

### D19 — Offload oversized tool output instead of truncating it away
**Decision:** `engine/builtin/offload.py`'s `maybe_offload(text, max_inline, label)` is
the one place every tool's "this output might be huge" logic goes through.
Under the limit, text passes through unchanged. Over it, the full text is
written to a content-hashed file under `config.offload_dir`
(`.harness/offload/`, default) and the tool returns a preview plus the file
path instead. `engine/builtin/filesystem.py` (`read_file`), `engine/builtin/shell.py`
(`run_command`), `engine/builtin/web.py` (`fetch_url`), and `context_engine/memory_tool.py`
(`_view`) all call it instead of each hand-rolling `text[:N] + "...
[truncated]"`.
**Why:** the old behavior didn't just shorten output, it **destroyed** the
rest of it — if a command produced 100K characters and the limit was 20K,
the last 80K were gone, unrecoverable, no matter how much the model
subsequently needed them. Offloading keeps the full output on disk and
recoverable via the same `read_file` tool the model already has.
**Deterministic, not incidental:** the on-disk filename is a hash of the
content (`{label}-{sha256[:16]}.txt`), not a timestamp or a counter — the
same oversized output offloaded twice reuses the same file rather than
writing a duplicate, and running the test suite twice produces identical
filenames rather than an ever-growing directory.
**Why one shared function instead of four separate truncation blocks:** all
four call sites had the *exact* same shape (`if len(x) > N: x = x[:N] +
"..."`) with nothing tool-specific — that's the textbook "reuse existing
utilities, avoid duplicate code" case, not a place for four subtly
different heuristics to drift apart over time.
**Trade-off:** every offloaded output now costs a small disk write and a
`read_file` round-trip if the model actually needs more of it (vs. having
the whole thing already truncated in context). Accepted — for oversized
output the model rarely needs to read every character of, the disk write is
cheap and the alternative was silent, permanent data loss.

### D20 — Split into `context_engine/` and `engine/` (folder reorganization)
**Decision:** regroup the codebase by what each piece is *for*, not just move
files around ad hoc. `context_engine/` holds everything the agent persists or
remembers — `compaction.py` (was `core/context.py`), `memory_tool.py` (was
`tools/memory.py`), `memory_tracker.py` (was `observability/memory_tracker.py`),
`session_store.py` (was `store/session_store.py`). `engine/` holds the
execution machinery — `orchestrator.py` and `permissions.py` (was `core/`),
`registry.py` and `mcp_client.py` (was `tools/`), and the built-in tools
themselves under `engine/builtin/` (`filesystem.py`, `shell.py`, `web.py`,
`offload.py`, was `tools/`). The old `core/`, `tools/`, and `store/` packages
no longer exist; `observability/` keeps only what's actually cross-cutting
telemetry (`usage.py`, `log.py`).
**Why:** the previous layout grouped by "what kind of Python object is this"
(a package for the loop, a package for tools, a package for persistence) more
than by "what job is this doing for the agent." `context_engine/` makes the
memory system's two independently-removable pieces (D16) sit next to the
conversation history and session store they're conceptually part of, instead
of being split across `tools/`, `observability/`, and `store/`. `engine/`
makes "the loop and everything it acts through" one importable unit,
`engine.builtin` making explicit that the shipped tools are a replaceable
default set, not privileged over an MCP tool or a future company-specific one.
**Trade-off:** this is a **purely organizational change — zero behavior
difference.** No test assertion changed, no runtime logic changed; every edit
is an import path. It touches nearly every file in the repository (`git mv`
plus import fixes across `multiagent/`, `pipeline/`, both `interfaces/`
entry points, and all nine test files), which is real regression risk for no
functional payoff — done at explicit user request rather than because the
old layout was broken. Verified by running the full test suite and both CLI
entry points after every step of the move, not just at the end.
**Alternatives considered:** leaving the folders as-is and only reorganizing
in documentation/diagrams. Rejected because the user specifically asked for
the actual folders to move, not just a relabeling in prose.

### D21 — `/model` switches at runtime by rebuilding, not by branching the loop
**Decision:** `interfaces/cli.py`'s `Session.switch_model(model)` builds a new
`Config` (`dataclasses.replace(self.config, model=model)`) and a new
`Provider` (`build_provider(new_config)`), then re-points the *existing*
`Conversation`'s summarizer at the new provider and rebuilds the
`Orchestrator` around that same conversation object. The `/model` command
(`_handle_model_command`) is a thin wrapper: no args shows the current model,
one arg switches.
**Why rebuild instead of mutate:** `Provider`, `Config`, and `Orchestrator`
are all meant to be treated as immutable-for-the-session values elsewhere in
the codebase (D2, D9) — teaching them to swap their own model out from under
running code would mean every consumer suddenly has to worry about the
model changing mid-call. Building fresh instances and swapping the
*reference* on `Session` keeps that invariant intact; nothing outside
`Session` needs to know switching is possible.
**Why the conversation object survives untouched:** the whole point of a
`/model` command during a live demo or a long task is to change *how* the
work continues, not to throw away *what's* been done. `Conversation` doesn't
hold a reference to the provider except through its injected `summarizer`
callable (D11), so swapping that one field is enough — messages and the
running summary carry over exactly as they were.
**Failure handling:** `build_provider` is called against the *candidate*
config before anything on `Session` is mutated, so an unknown model prefix
(or any other setup failure) leaves the session exactly as it was — matching
PRINCIPLES rule 5 ("fail loud at the edges"): the command reports the error
and the session keeps working on the model it already had.
**Trade-off:** switching models mid-conversation can produce a stranger reply
than starting fresh — the new model didn't generate any of the history it's
now continuing, tool-calling conventions can differ across models, and a
provider swap doesn't re-validate that the new model actually supports every
tool already in play. Accepted: it's an explicit, visible action the user
takes (unlike e.g. auto-compaction), and being able to demonstrate "same
task, different model" live is the actual point of the feature.
**Out of scope for this decision:** per-role or per-sub-agent model
overrides (`delegate` sub-agents keep using whatever `Provider` the
coordinator was built with at startup, not whatever `/model` last switched
to) — no current use case forces that yet.

### D22 — Multi-user login via a JSON account file + Config namespacing
**Decision:** `auth/users.py`'s `UserStore` holds username -> salted/hashed
password records in a JSON file (`.harness/users.json` by default, same
external-config shape as D14/D17/D18's MCP/roles/skills files). Passwords are
hashed with PBKDF2-HMAC-SHA256 (200,000 iterations, a random 16-byte salt per
user, stdlib `hashlib`/`secrets` only) — never stored or logged in plaintext.
`interfaces/cli.py`'s `main()` calls `_login()` before building anything
else; a returning username is verified, a new one is registered on the spot
(choose + confirm a password). The returned username feeds straight into
`Config.for_user(username)`, a new method on `Config` that returns a copy
with `sessions_dir`, `memory_dir`, `logs_dir`, and `offload_dir` all suffixed
with the username — every other field (model, permission mode, MCP/roles/
skills config paths) stays shared and org-wide.
**Why a new top-level package instead of folding into `context_engine/` or
`interfaces/`:** authentication is a distinct concern from both — it's not
something the agent remembers (`context_engine/`) and it's not interface
plumbing (`interfaces/`), it's "who is allowed to be here and whose data is
whose." Keeping it a separate, small package means it can be swapped for a
real identity provider later (SSO, OAuth, a company directory) by replacing
`auth/users.py` behind the same two calls (`_login`, `Config.for_user`)
without interfaces/cli.py's other 300+ lines caring.
**Why namespace directories instead of a shared store keyed by user:** every
consumer of `config.sessions_dir` / `memory_dir` / `logs_dir` / `offload_dir`
(`SessionStore`, `MemoryTracker`, `EventLogger`, `maybe_offload`) already
just takes "a directory" and knows nothing about users — namespacing the
path once, at login, means zero of those classes need a `username` parameter
threaded through them. This is the same trick `Config.for_user` name implies:
push the per-user decision to the one place (`main()`, right after login)
instead of every downstream consumer.
**Why PBKDF2 over bcrypt/argon2:** those need an extra dependency; PBKDF2 via
stdlib `hashlib` needs none, matching this repo's existing bias toward native
SDKs over extra packages (D3). 200,000 iterations is OWASP's current
minimum-recommended floor for PBKDF2-SHA256.
**Username validation closes a path-traversal hole:** the first version of
`Config.for_user` built directories with a raw `os.path.join(self.sessions_dir,
username)` — an unsanitized username, since `UserStore.register` only checked
"non-empty." A username of `"../alice"` collides `os.path.join` back onto
another user's real directory (`.harness/sessions/../sessions/alice` resolves
to the same path the OS already uses for user `alice`), and a leading `/`
discards the base directory entirely (`os.path.join` semantics), redirecting
a user's own data anywhere on disk the process can write. Caught in a
security review before merging (not in production). Fixed by confining
usernames to `^[A-Za-z0-9_-]{1,64}$` in *two* places — `UserStore.register`
(reject at account creation, the actual point untrusted input enters) and
`Config.for_user` itself (defense in depth: the method's own docstring
promises directory isolation, so it enforces the precondition rather than
trusting every caller to have validated first) — the same
"whitelist-the-untrusted-path-component" pattern already used by
`context_engine/session_store.py`'s `session_id` and `observability/log.py`'s
`run_id`; this feature should have followed it from the start.
**Trade-off:** this is real authentication (salted, hashed, never
plaintext) but not a real *security boundary* — anyone with filesystem
access to `.harness/users.json` or the running process can read any user's
files directly; there's no encryption at rest, no session tokens, no rate
limiting beyond the interactive prompt's 3-attempt cap, and the
`HARNESS_USER`/`HARNESS_PASSWORD` env-var shortcut (added for scripted/demo
use, same "env var first" convention as the rest of `Config.load()`) means a
`.env` file with those set bypasses the prompt entirely. Acceptable for what
this is today — a local CLI tool giving each person their own session/memory
namespace, not a multi-tenant server — and the account/namespacing layer is
exactly what a real auth system would sit behind later without changing how
`context_engine/`, `engine/`, or `pipeline/` work.
**Alternatives considered:** OS-user-based isolation (`os.getlogin()`) —
rejected because it doesn't give the harness its own login step or work
identically across machines; a database-backed user store — rejected as
premature for the account volumes a CLI tool has (same reasoning as D12).

### D23 — Explicit planning as a tool (`todo_write`/`todo_read`), not a loop change
**Decision:** `engine/builtin/planning.py` registers two tools,
`todo_write(steps)` (replaces the whole ordered checklist -- each item a
`{step, status}` pair, `status` one of `pending`/`in_progress`/`completed`)
and `todo_read()` (renders it). State is a plain in-memory module list, reset
via `reset_plan()` at the start of a new conversation (`Session.reset()`,
and after `/load`, since the loaded conversation's plan -- if any -- wasn't
persisted with it). This closes the "Planning and Decomposition" component
from the LangChain harness-anatomy article that prompted D19's offloading
work and this evening's gap review.
**Why a tool instead of new orchestrator logic:** the system prompt already
tells the model to "work in small, verifiable steps"; what was missing
wasn't the *instruction* to plan, it was a place to put the plan somewhere
visible and re-checkable turn over turn, the same way `memory` gives the
model a place to put durable notes instead of only holding them in its own
context. A tool keeps this at the edge (D1/AGENTS.md's one invariant) --
`engine/orchestrator.py` doesn't know or care that `todo_write` exists,
exactly like every other built-in tool.
**Why replace-the-whole-plan instead of item-level mutation (`mark_step_done(i)`):**
one call, one consistent state -- no risk of the model's mental model of
step indices drifting from the stored list after a few turns of partial
updates. The cost (resending the full checklist each time) is negligible;
plans are short.
**Trade-off -- not persisted, and shared across delegated sub-agents:**
the plan lives only for the process's current conversation (gone on
restart -- deliberate, `memory` already covers "needs to survive a
restart"). More notably, because `todo_write`/`todo_read` self-register
onto the same shared `registry` as everything else, a `delegate`-spawned
sub-agent (D17) sees and can overwrite the *same* plan the coordinator is
using -- `FilteredRegistry` only hides `delegate` itself, not this. Accepted
for now as a known limitation (a real fix means threading a plan scope
through `Orchestrator`, which is a bigger change than tonight's gap-closing
pass justifies) rather than silently pretending it's isolated.

### D24 — Web search is an opt-in tool gated on a real API key, not a scraper
**Decision:** `engine/builtin/search.py`'s `build_search_tool(api_key)`
returns a `web_search` tool backed by the Tavily API (`api.tavily.com`,
built for LLM tool-calling, free tier) over plain `urllib` -- no new
dependency, same as `fetch_url`. Unlike every other `engine/builtin/` tool,
it does **not** self-register on import: `interfaces/cli.py` and
`interfaces/pipeline_cli.py` only call `registry.register(build_search_tool(...))`
when `config.search_api_key` (`HARNESS_SEARCH_API_KEY`) is set. No key = no
tool, not a tool that's always present and always fails.
**Why this reverses the earlier decision not to build web search:** the
first pass (D19's PR) tried DuckDuckGo's free, keyless endpoints and found
them unreliable in live testing (bot-detection challenge pages, empty
Instant-Answer results for ordinary queries) -- not something to ship right
before a demo. That verdict was about *scraping without a key*, not about
search in general; a real search API with a real key is a different,
reliable foundation, so the fix was never "write a better scraper," it was
"use a service built for this."
**Why opt-in registration instead of self-register + fail-string-on-call:**
a tool the model can see in its tool list but that always returns
`"Error: not configured"` teaches the model to distrust the tool list, and
wastes a turn every time it's tried. This mirrors `delegate`'s shape (D17:
no roles configured = no tool at all) rather than MCP's shape (D14: tools
appear only once a server is actually connected) -- same underlying
principle (don't advertise capability that isn't actually there), applied
at config-load time instead of connect time since there's no "connect" step
for an API-key-only tool.
**Risk classification:** `dangerous`, same as `fetch_url` -- it reaches an
external network service the user didn't explicitly name (unlike `fetch_url`,
where the model at least names the exact URL). Prompts in `ask` mode, denied
in `allowlist`, free in `auto` -- same rule, no special case added.
**Trade-off:** requires the user to obtain and configure a third-party API
key (a new external dependency the project didn't have before); the tool is
silently absent without one rather than nudging the user toward getting a
key. Accepted -- forcing configuration to get a real feature beats a
default-on feature that's unreliable or requires committing to one specific
provider's SDK.

### D25 — One `web_search` tool, two backends (supersedes D24's registration rule)
**Decision:** `engine/builtin/search.py`'s `build_search_tool(api_key)` is
the only place a `web_search` tool comes from, and it is now **always**
registered by both interfaces — with a Tavily key it calls the Tavily API
(D24's reliable path), without one it falls back to the DuckDuckGo
lite-HTML scraper, which moved to a plain exported function
(`engine/builtin/web.py:duckduckgo_search`) and no longer self-registers.
**Why:** the audit (AUDIT.md C4) found D24 was never fully landed: the old
DuckDuckGo `web_search` still self-registered unconditionally on import,
and when a Tavily key was set, `build_search_tool`'s registration
silently *overwrote* it in the registry (last-write-wins, no notice) —
the code contradicted D24's own "no key = no tool" claim. Rather than
finishing D24 as written (delete the scraper) the owner chose to keep
zero-config search as a real feature: one tool name, key-gated backend
selection inside the handler, so the model sees exactly one `web_search`
whose quality improves when a key is configured.
**What this supersedes:** D24's "no key = no tool" registration rule.
D24's other verdicts stand — scraping is unreliable (that's exactly why
it's the fallback, not the primary), and a real search API with a real
key is the preferred path.
**Trade-off:** a zero-config install now exposes a search tool that can
hit DuckDuckGo's bot detection and return junk — the model may waste a
turn on a bad result where D24-as-written would have had no tool at all.
Accepted: search-that-sometimes-degrades beat search-that-needs-setup for
the owner's use, and offloading/`risk: dangerous` gating apply the same
either way.

### D26 — Transient-failure retries live inside each provider adapter
**Decision:** `providers/retry.py`'s `call_with_retries` wraps the one SDK
call in each adapter (`AnthropicProvider.complete`,
`OpenAIProvider.complete`) with exponential backoff (3 attempts, 1s/2s)
on that SDK's own rate-limit/connection/timeout/5xx exception types.
`providers/fallback.py`'s `FallbackProvider` optionally wraps two whole
providers — primary and `HARNESS_FALLBACK_MODEL` — behind the same
one-method `Provider` interface. `providers/model_info.py` gives the
factory and the `Conversation` construction sites per-model context
windows and output limits (substring-matched like `PRICING`), used only
when the user hasn't set `HARNESS_MAX_TOKENS`/`HARNESS_MAX_CONTEXT_TOKENS`
explicitly; unknown models keep the historical 4096/100k defaults.
**Why in the adapters and not the orchestrator:** the retryable exception
types are SDK-specific (`anthropic.RateLimitError` vs
`openai.RateLimitError`) — catching them in `engine/orchestrator.py` would
mean the loop importing concrete provider SDKs, breaking D2. Each adapter
already owns its SDK boundary; retrying there keeps the loop
provider-blind, and `FallbackProvider` is invisible to it for the same
reason (Liskov: it's just another `Provider`).
**Trade-off:** both SDKs also retry internally (their clients' defaults),
so a truly down API waits through two retry layers before failing —
seconds, not minutes, and bounded. Accepted for the simplicity of not
reconfiguring SDK internals. The fallback model reuses the primary's
credentials, so cross-provider fallback (Anthropic primary → OpenAI
fallback) needs the same key to be valid for both — in practice the
useful pairs are same-provider (opus → haiku) or anything → local
key-less (`ollama/...`); recorded rather than solved.

### D27 — Workspace confinement is opt-in, shared with the memory tool's protection
**Decision:** `engine/workspace.py` owns one confinement implementation:
`confine(root, path)` (realpath-based, so symlinks can't smuggle access;
rejects `../` traversal and outside absolute paths) plus a module-level
root set per session by the interface. `HARNESS_CONFINE_WORKSPACE=true`
(default **false**) makes `read_file`/`write_file`/`edit_file`/`list_dir`
resolve every path inside `workspaces/{user}/{session}/` and pins
`run_command`'s cwd there; escapes come back as error strings, never
exceptions (PRINCIPLES rule 1). `context_engine/memory_tool.py`'s
`_resolve` now delegates to the same `confine()` (virtual-root semantics
preserved) instead of keeping its own copy.
**Why opt-in instead of enforced:** the owner's explicit rollout decision
(PLAN.md §5.4) — today's single-user CLI users read/write anywhere on disk
on purpose, and a silent new restriction would break them; a future server
interface sets the flag unconditionally per session, which is the actual
target of this feature (AUDIT.md D1/D2: zero isolation was the sharpest
gap vs. a framework default).
**Why a module-level root:** same deliberate, startup-set global pattern
as `set_memory_root`/`set_offload_root` — and like them it is scheduled to
become session-scoped state in the global-state removal milestone
(PLAN.md Milestone 3); building it session-scoped *now* would have meant
doing half of that milestone early, out of order.
**Trade-off:** confinement covers the built-in filesystem/shell tools, not
MCP-server tools (a filesystem MCP server has its own allow-list) and not
the host itself (`run_command` inside the workspace can still call
anything on PATH — real host isolation is the sandbox's job, G1, designed
in a later milestone). Recorded so nobody mistakes this for a sandbox.

### D28 — Session-scoped state via ContextVars, not per-session tool closures
**Decision:** the four pieces of module-level mutable state the audit
flagged as multi-user blockers (AUDIT.md F3) — `context_engine/memory_tool.py`'s
root, `engine/builtin/offload.py`'s root, `engine/workspace.py`'s root
(added in D27), and `engine/builtin/planning.py`'s plan — are now
`contextvars.ContextVar`s instead of plain globals. The `set_*` APIs are
unchanged; they now write to the calling execution context. A thread (the
shape a future server gives each session) automatically gets its own
values; `multiagent/coordinator.py` runs each delegated sub-agent via
`contextvars.copy_context().run(...)` with a fresh plan, which closes
D23's recorded "sub-agents share the coordinator's plan" limitation
structurally while leaving memory shared by design (D17).
**Why ContextVars instead of the per-session tool-closure registry the
plan sketched (PLAN.md Milestone 3):** closures would have required every
session to assemble its own `Registry` and every root-consuming tool to
be rebuilt per session — a large structural change touching registration
everywhere. ContextVars achieve the same isolation with zero changes to
tool handler signatures, zero changes to registration, identical
single-threaded CLI behavior, and stdlib-only semantics that are also
correct under asyncio (relevant for a future server). Deviation from the
written plan, recorded here per PRINCIPLES rule 0.
**What this does not fix:** the shared `registry` singleton's *visibility*
(an MCP server one session connects is callable by every session in the
process, D8/D14) — that is a tool-scoping question, not a state-corruption
one, and is deferred to the server-phase work recorded in AUDIT.md F1.
**Trade-off:** ContextVar state is invisible to code that doesn't know to
look for it — a reader grepping for globals won't find the mutation the
way an assignment to `_ROOT` used to show. Mitigated by keeping the same
`set_*` entry points and documenting the pattern here and in each module's
docstring.

### D29 — Users and sessions move to a relational store behind one URL
**Decision:** a new `storage/` package (SQLAlchemy) owns user accounts and
session persistence: `users` (with integer `user_id` primary keys and a
two-tier `admin`/`user` role — the first account ever created bootstraps
as admin), `sessions` keyed by `(session_id, user_id)`, and a `usage_log`
table (schema now, writer lands with admin monitoring in the next
milestone). One connection string, `HARNESS_DB_URL`, picks the backend —
default `sqlite:///.harness/harness.db` (zero ops), any
`postgresql+psycopg://...` URL for Postgres with no code change.
`DbUserStore`/`DbSessionStore` keep the old stores' public interfaces
(`exists/register/verify/list_usernames`, `save/load/list_ids`) plus what
JSON couldn't do: `delete()` (wired to a new `/delete <id>` command),
real cross-user isolation at the query level (a store is *bound* to a
user_id at construction — no query can reach another user's rows), and
concurrent-write safety.
**What was replaced vs. kept:** `context_engine/session_store.py` is
deleted (replaced outright, per the plan decision — no caller depended on
its module path but the CLI). `auth/users.py` keeps the PBKDF2
`hash_password`/`verify_password` (the DB store uses them unchanged, so
migrated hashes still verify) and keeps the JSON `UserStore` class marked
LEGACY, as the read-side of `scripts/migrate_json_to_db.py` — the
idempotent one-time migration that copies `.harness/users.json` accounts
(hashes verbatim) and `.harness/sessions/<user>/*.json` snapshots into the
database.
**Why one snapshot blob per session instead of per-message rows:** resume
always loads the whole history anyway; the blob is byte-identical to what
the file store wrote, which makes migration an exact copy; and exploding
to rows is a later, additive change if per-message queries ever matter.
**Trade-off:** SQLAlchemy is the project's first ORM dependency (a
recorded deviation from the stdlib-only bias of D3/D22 — accepted because
hand-writing two dialects of SQL for a schema this small is worse), and
SQLite remains single-writer; the Postgres URL swap is the documented
answer once a concurrent server process exists.

### D30 — Auth scaffolding (JWT) + admin usage monitoring
**Decision:** two additions that together make the harness
server-auth-ready and give the owner the requested admin oversight:
- `auth/tokens.py` (PyJWT): `issue_token`/`verify_token` with `sub` =
  integer user_id, `role`, `iat`, `exp` (config TTL, default 7 days),
  signed by `HARNESS_JWT_SECRET` or an auto-generated secret persisted
  0600 at `.harness/jwt_secret`. The CLI issues **and immediately
  verifies** a token on every login — it doesn't need the token yet (one
  process, one login), but exercising the exact round trip a server's
  per-request middleware will run means the path can't rot, and the
  claims carry precisely what downstream code is keyed by.
- `observability/usage_store.py`: `PersistentUsageTracker` extends the
  in-memory `UsageTracker` (which still powers `/cost` unchanged) to
  insert one `usage_log` row per model call — user, session id and task
  text *current at call time* (injected as callables, since both change
  over a run), model, tokens, estimated cost. Inserts are best-effort
  (accounting must never break a run, same rule as EventLogger). The
  admin-only `/usage` (per-user totals) and `/usage <username>`
  (per-session drill-down with last task) print the aggregation queries a
  server's admin endpoints would serve verbatim; `/users` and
  `/users role <name> <admin|user>` manage the two-tier role model, with
  the last admin protected from demotion.
**Why JWT over opaque DB tokens:** the owner's explicit choice (PLAN.md
§5.3) — self-contained tokens a stateless server middleware can verify
without a DB read; PyJWT is the accepted new dependency.
**Trade-off:** the token is scaffolding, not yet a boundary — nothing in
the CLI *requires* it after login, and role checks gate CLI commands, not
storage APIs (a Python caller with the engine can query anything;
process-level trust is unchanged from D22). The boundary becomes real in
the server phase, which is exactly what this shape was built for.

### D31 — Long-term memory is injected at session start, not just advertised
**Decision:** `context_engine/memory_tool.py`'s `memory_overview(root,
max_chars=2000)` builds a capped digest of the user's memory directory —
each top-level file's contents, subdirectories listed by name only — and
`Session._new_conversation` appends it to the system prompt under
"## Your memory (notes from earlier sessions)" whenever it's non-empty.
Empty memory adds zero prompt overhead.
**Why:** the audit (E3) found the system prompt *instructed* the model to
check memory, but nothing guaranteed prior notes were ever seen — it
depended entirely on the model choosing to call the tool first. Injection
closes that: what earlier sessions recorded is simply in front of the
model from turn one, and the tool remains the way to read more (the
digest truncates with an explicit pointer) or write updates.
**Trade-off:** the digest spends prompt tokens on every session even when
the memory is irrelevant to the task, and a loaded (`/load`) session keeps
whatever prompt it was saved with rather than re-injecting current memory.
Both accepted: the cap bounds the cost, and re-injecting into a restored
conversation would rewrite history the summary/compaction may reference.

### D32 — A hooks layer makes cross-cutting behavior pluggable
**Decision:** `engine/hooks.py`'s `Hooks` dataclass carries four ordered
lists of plain callables the orchestrator runs at fixed points:
`pre_model_call(messages)`, `post_model_call(response)`,
`pre_tool_call(tool_call, tool)` (return a string to veto — it becomes
the tool result the model sees, same "denial is an observation" contract
as D5/D6 — or the possibly-rewritten call to proceed), and
`post_tool_call(tool_call, result)`. Injected through `Orchestrator`'s
constructor like every other collaborator; an empty `Hooks()` (the
default everywhere today) leaves the loop byte-for-byte identical.
**Why this is the sanctioned edit to the loop:** AGENTS.md's rule is
"you should almost never edit `engine/orchestrator.py`" — and the audit
(H5) showed that rule was being *violated in spirit* by every
cross-cutting concern already in the loop: compaction and permissions are
baked in because there was nowhere else to put them. This change adds the
extension point itself, so the *next* guardrail, redaction pass, or
context injector is a new file + a `Hooks(...)` argument, not another
edit. Compaction and permissions are deliberately NOT retrofitted onto
the mechanism — they work, they're tested, and churning them for purity
buys nothing (PRINCIPLES rule 8).
**Trade-off:** hooks are power tools — a badly written pre_model hook can
corrupt history, and hook errors are not swallowed (unlike tool errors)
because a broken guardrail failing open would be worse than a crash.
Accepted and documented rather than defended against.

---

## Known limitations & future work

- **Token counts are estimated for compaction triggers:** the ~4-chars/token
  heuristic (`estimate_tokens`) is approximate; the actual per-turn usage from
  the API is exact and used for cost. Good enough to decide *when* to compact.
- **Prices drift:** `PRICING` in `observability/usage.py` is manually maintained.
- ~~**Per-user, but still single-writer, JSON persistence**~~ — fixed by
  D29: users and sessions live in the relational store (SQLite locally,
  Postgres by URL swap), with per-user isolation enforced at the query
  level and real concurrent-write safety. Memory, logs, offload, and
  workspaces intentionally remain files on disk.
- **Auth is real hashing, not a security boundary:** see D22's trade-off --
  no encryption at rest, no session tokens/expiry, no password reset, and
  filesystem access to `.harness/users.json` or the running process reads
  any user's data directly regardless of login.
- **Parallel tool calls run sequentially:** the loop executes a turn's tool calls
  in order. Fine for correctness; a future optimization could parallelize
  independent, read-only calls.
- **Coarse risk model:** see D5.
- **Web search without a key is best-effort:** `web_search` (D25) always
  exists, but the key-less DuckDuckGo fallback can hit bot detection or
  return empty results; set `HARNESS_SEARCH_API_KEY` for the reliable
  Tavily path.
- ~~**The planning tool's state isn't sub-agent-scoped**~~ — fixed by D28:
  sub-agents run in a copied context with a fresh plan; the coordinator's
  checklist can no longer be overwritten by a delegated sub-agent.
- **Pipeline runs one slice at a time:** no parallel worktrees yet. Real
  parallelism needs separate OS processes (not just threads, since `_cwd`
  chdir is process-global) — a bigger step, left for a follow-up.
- **Pipeline stops before push/PR:** by design for v1 (see D15). Adding
  push + `gh pr create` (or a REST-based `tools/github.py`) is additive, not
  a redesign, whenever that trust boundary is worth crossing.
- **No cross-model review stage:** would need a second configured `Provider`;
  deferred until there's a real second-provider use case (PRINCIPLES rule 8).
- **Sub-agent activity isn't visually distinguished in the CLI:** a delegated
  sub-agent's `tool_call`/`thinking` events flow through the same `on_event`
  as the coordinator's own, unlabeled — you can tell something is happening,
  not which agent is doing it. Adding a "who" tag to the event shape would
  fix this without touching `engine/orchestrator.py` (D17); not done yet.

Every item above has a home in the existing structure — none requires reshaping
the loop.
