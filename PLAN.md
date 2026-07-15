# PLAN — Closing the gaps found in AUDIT.md

This plan is derived entirely from `AUDIT.md`'s scored checklist and
framework benchmark. Read that first; this file doesn't re-derive evidence,
it acts on it. **Stop point: this plan requires explicit approval before any
implementation (Phase 4) begins.**

---

## 1. Build vs. migrate recommendation

**Recommendation: (c) Hybrid — keep the custom loop, fix the gaps at the
edges, adopt no new framework.**

The audit's framework benchmark (`AUDIT.md` §5) found the loop itself
(orchestrator, registry, permissions, provider abstraction, compaction) is
solid, tested, and in a few places (delegation's structural one-level cap)
*stricter* than what LangGraph/deepagents give by default. None of the six
❌-scored gaps require branching control flow, a state graph, or a
checkpointer's full generality to fix — they're missing files and wiring,
not missing architecture. Migrating to LangGraph/deepagents would mean
re-solving problems this codebase has already solved well (provider
neutrality, permission gating, tested compaction) in exchange for framework
defaults on exactly three things: workspace isolation, interrupts, and
middleware — all three of which are buildable here directly, in-pattern,
without a rewrite.

**Effort comparison:**

| Path | Effort | Gained | Lost | Lock-in risk |
|---|---|---|---|---|
| **(a) Keep + fill gaps** (recommended) | ~9 milestones below, each independently shippable, 1-3 days each | Closes every ❌/🟡 item without touching what already works; team already understands every line | Nothing — no framework primitives to gain elsewhere (state graph, which we don't need) | None — stays on stdlib + 2 SDKs |
| **(b) Full migration to LangGraph/deepagents** | Total rewrite of `engine/`, `context_engine/`, `pipeline/`, `multiagent/` — every one of the 43 modules read in the audit either disappears or gets rewritten against new APIs; realistically 4-8x the effort of (a), and the two custom entry points (`interfaces/cli.py`, `interfaces/pipeline_cli.py`) need rewiring too | Real checkpointer/store abstraction, real `interrupt()`, real middleware chain, deepagents' sandboxed filesystem tools out of the box | The tested provider-neutral abstraction (D2-D4), the coarse-but-working permission model (D5), all 14 passing test files (would need a full rewrite against new fakes), the "a new engineer reads the loop in one sitting" property this codebase currently has | High — commits to LangGraph's checkpoint schema, its async execution model, and its release cadence; the very Windows long-path problem that killed LiteLLM (D3) is a real prior signal about dependency weight on the target environment and should be re-checked before pulling in LangGraph's own dependency tree |
| **(c) Hybrid — adopt specific libraries** | Evaluated per-library below | — | — | — |

**Hybrid components considered and rejected:**
- **LiteLLM for the model layer**: rejected, not because the original D3
  reasoning is wrong, but because it's *unnecessary* now — `openrouter/<model>`
  already routes through the existing `OpenAIProvider` + `base_url`
  (`providers/factory.py:18`), and OpenRouter alone proxies ~200 models under
  one OpenAI-compatible endpoint. The real remaining gap (A4 retry/backoff,
  A5 per-model window/pricing) is ~150 lines added to the existing
  `Provider`/`Config` classes (Milestone 1), not a new dependency. Re-litigate
  only if a genuinely non-OpenAI-shaped provider (e.g. raw Gemini, Bedrock)
  becomes a hard requirement — that's still "one new adapter file," per D3.
- **A checkpointer library (e.g. LangGraph's standalone checkpoint package)
  for persistence**: rejected in favor of a direct SQLAlchemy + SQLite/Postgres
  implementation (Milestone 4) — the schema is small (sessions, users,
  memory-file index) and a purpose-built schema is easier to reason about
  and migrate than adapting a graph-shaped checkpoint schema to a
  linear-conversation shape.
- **A real interrupt/middleware primitive, custom-built** (not adopted from a
  framework): the audit's H5/interrupt gap is real, but the fix is a thin,
  in-house addition to `Orchestrator` (Milestone 7) — a list of injectable
  pre/post hooks, not a new dependency.

No hybrid adoption is recommended at this time. Revisit if a milestone below
turns out to need materially more custom code than estimated.

---

## 2. Storage recommendation

**Recommendation: SQLite via SQLAlchemy now, schema designed for a
straight swap to Postgres later; workspaces and long-term memory stay as
files on disk.**

**Reasoning:**
- `AUDIT.md` §5 and §4.F3 both point at the same root cause: JSON-file
  persistence (`SessionStore`, `UserStore`) has no concurrent-write
  protection (`DESIGN.md`'s own "still single-writer" limitation) and no way
  to query across sessions/users (e.g. "list this user's sessions ordered by
  last activity" currently means listing a directory and stat-ing every
  file). A relational store fixes both with a small, well-understood schema:
  `users(id, username, password_hash, salt, created_at)`,
  `sessions(id, user_id, created_at, updated_at, title)`,
  `messages(id, session_id, role, content, tool_call_id, seq)` (replacing the
  single JSON blob-per-session with queryable rows — enables "resume from
  message N" without deserializing the whole history), `memory_index(user_id,
  path, updated_at)` (an index over the memory tool's files, not a
  replacement for them — see below).
- **SQLite now**: zero ops, one file, works identically in the CLI's current
  single-process model and in early server testing; SQLAlchemy's Core/ORM
  layer makes the Postgres migration a connection-string change plus
  verifying a couple of SQLite-specific type quirks (e.g. `JSON` column
  behavior), not a rewrite.
- **Postgres later**: the natural move once there's an actual concurrent
  multi-user server process — SQLite's single-writer lock becomes a real
  bottleneck exactly at that point, not before.
- **Workspaces stay as files-on-disk**, under
  `workspaces/{user_id}/{session_id}/` (Milestone 2) — large text/binary
  content doesn't belong in database rows, and every filesystem tool
  (`read_file`, `write_file`, `edit_file`, `run_command`) already assumes a
  real filesystem path; there's no reason to change that, only to confine it.
- **Long-term memory stays as files** (the `memory` tool's `view/create/
  str_replace/insert/delete/rename` interface is inherently file-shaped, and
  keeping it that way preserves D16's "same shape as a text-editor tool"
  design and lets memory content stay human-readable/diffable/git-friendly).
  What moves to SQLite is only a lightweight *index* over those files
  (path, size, last-updated) so listing/searching memory doesn't require
  walking the filesystem — optional, low-priority (folded into Milestone 4
  but can be dropped without blocking anything else).

**Alternative considered: stay on JSON files for one more phase, defer the
database entirely.** Cheaper short-term (zero new dependency — SQLAlchemy
would be requirements.txt's first ORM), and defensible if the client/server
phase is still far out. Rejected as the primary recommendation because it
doesn't fix the concurrent-write hazard `DESIGN.md` already flags as a known
limitation, and because Milestone 3 (killing the global-state problem, F3)
is naturally done *at the same time* as introducing real per-request session
objects backed by a real store — doing the storage migration separately
later means touching the same call sites twice.

---

## 3. Prioritized milestones

Ordered by dependency and impact, per the audit's own conclusion (§6): the
loop is solid, so priority goes to what blocks the multi-user/server phase
first (workspace isolation, global-state removal), then storage, then the
remaining checklist items, then sandbox design last since it's explicitly
still "planned, not yet built" in the product context and has no code
dependents.

### Milestone 1 — Model layer hardening
**Closes:** A3 (partial→done for non-streaming scope), A4, A5, C4 (the
`web_search` bug).
**Files:** `providers/base.py`, `providers/anthropic_provider.py`,
`providers/openai_provider.py`, new `providers/model_info.py` (context
window / max-tokens table, same shape as `observability/usage.py`'s
`PRICING`), `engine/builtin/web.py`, `engine/builtin/search.py`,
`interfaces/cli.py`, `interfaces/pipeline_cli.py`.
**Design decisions:**
- Retry: wrap `Provider.complete` with a small retry decorator (exponential
  backoff, 3 attempts) catching each SDK's own rate-limit/connection
  exception types — added once per provider, not in the orchestrator (keeps
  D2's "orchestrator never imports a concrete provider" invariant intact).
- Per-model config: a `model_info.py` lookup (substring-matched, same
  pattern as `PRICING`) providing `context_window` and `default_max_tokens`;
  `build_provider` consults it to fill in `Config.max_tokens` when the user
  hasn't overridden it, instead of always using the hardcoded `4096` default.
- `web_search` fix: remove the always-on DuckDuckGo `web_search` from
  `engine/builtin/web.py` (keep `fetch_url` there); the Tavily tool in
  `engine/builtin/search.py` becomes the only `web_search`, opt-in as the
  docs already (incorrectly) claim it is today. *(See open question 1 below
  — this is the one place this plan makes a judgment call worth confirming.)*
**Verification:** extend `tests/model_switch_test.py` with a retry-on-fake-rate-limit
case; new `tests/model_info_test.py`; manual: run the CLI against a real key,
confirm `/model` still reports correctly, confirm only one `web_search` tool
appears in the startup tool list with no key set, and appears when
`HARNESS_SEARCH_API_KEY` is set.

### Milestone 2 — Workspace isolation
**Closes:** D1, D2.
**Files:** `config.py` (add `workspace_dir`, extend `Config.for_user` to
accept a `session_id` too), `engine/builtin/filesystem.py`,
`engine/builtin/shell.py`, new `engine/workspace.py` (the confinement
helper, same shape as `context_engine/memory_tool.py`'s `_resolve`).
**Design decisions:**
- Directory shape: `workspaces/{user_id}/{session_id}/`, matching the
  product requirement's own example path exactly.
- Confinement: `engine/workspace.py:resolve(path, root)` — identical
  traversal-safety logic to `memory_tool._resolve`, extracted so both can
  share it rather than duplicating (closes the DRY gap between the two
  independently-written confinement implementations that would otherwise
  exist).
- `run_command`: `cwd` pinned to the workspace root, not the process's real
  cwd; commands cannot `cd ..` out of it in a way that matters for
  subsequent tool calls (each `run_command` call is already a fresh
  subprocess, so this is a `cwd=` kwarg change, not new sandboxing — real
  isolation from the *host* is still G's job, not this milestone's).
- Interactive CLI default: the workspace root defaults to the process's
  actual launch directory *only when running as the single-user CLI*
  (`Session.__init__` passes `os.getcwd()` as the root today implicitly by
  not confining at all) — see open question 4 for how hard this confinement
  should be enforced immediately.
**Verification:** new `tests/workspace_test.py` mirroring
`tests/memory_test.py`'s escape-attempt assertions (`../../etc/passwd`,
absolute paths, symlink escape); manual: run `main.py` in a scratch
directory, confirm `read_file`/`write_file`/`run_command` all stay confined
and a `../`-prefixed path is rejected with a clear error string (not a
crash — must still satisfy PRINCIPLES rule 1).

### Milestone 3 — Remove global state (concurrency safety)
**Closes:** F3 (and de-risks H4's shared-plan caveat).
**Files:** `engine/orchestrator.py` (add `workspace_root`/`memory_root`/
`offload_root`/`plan` as constructor-injected, session-scoped objects
instead of module globals), `engine/builtin/planning.py`,
`context_engine/memory_tool.py`, `engine/builtin/offload.py`,
`interfaces/cli.py`, `multiagent/coordinator.py`.
**Design decisions:**
- This is the one milestone that touches tool *handler signatures*, which
  the current codebase deliberately avoided ("would break the plain
  `(**kwargs) -> str` handler contract," per `memory_tool.py`'s and
  `offload.py`'s own docstrings) — the fix has to thread session context
  through *without* breaking that contract, since every built-in tool
  handler assumes `(**kwargs) -> str` with no injected session object.
  Recommended approach: closures built per-`Orchestrator` construction, the
  same pattern `engine/builtin/search.py:build_search_tool` already uses for
  a per-config API key — `Session`/`Orchestrator` builds a
  `Registry` whose `memory`/`todo_write`/offloading tools are closures bound
  to *that* session's root, instead of reaching for a shared module global.
  This means each session effectively gets its own small set of
  session-scoped tool instances layered over the shared built-in/MCP tools,
  rather than one process-wide `registry` — a bigger structural change than
  any other milestone here, flagged accordingly.
- `todo_write`/`todo_read`: same treatment — plan state becomes part of
  `Orchestrator`'s own state (or `Conversation`'s, since it already owns
  per-session mutable state) instead of a module list.
**Verification:** new `tests/concurrency_test.py` — construct two
`Orchestrator`s in the same process with different roots/plans in the same
test function, run scripted turns on both interleaved, assert neither's
memory/offload/plan state leaks into the other; this is the test that
*cannot pass today* and is the milestone's actual acceptance criterion.
Manual: none needed beyond the test (this is a pure-architecture fix with no
user-visible CLI behavior change when there's only one session, which is
exactly the property that makes it safe to ship without disrupting today's
CLI users).

### Milestone 4 — Storage migration (sessions, users, memory index)
**Closes:** F2 (adds session delete), improves F1's storage-layer half,
lays the groundwork the client/server phase needs.
**Files:** new `storage/` package (`storage/models.py` — SQLAlchemy models,
`storage/session_store.py`, `storage/user_store.py` — same public interface
as today's `SessionStore`/`UserStore` so callers don't change), `requirements.txt`
(add `sqlalchemy`), `context_engine/session_store.py` and `auth/users.py`
become thin adapters or are replaced outright (open question — see below).
**Design decisions:** see §2 above for the schema. Migration path: a
one-time script reads existing `.harness/sessions/*.json` and
`.harness/users.json` and inserts them into the new SQLite DB, so no one
loses saved sessions when this ships.
**Verification:** port `tests/phase2_test.py`'s session round-trip assertion
and `tests/auth_test.py`'s hashing/verify assertions onto the new store
(same test *behavior*, new backend — proves the interface swap is
transparent); manual: run the migration script against a real `.harness/`
directory produced by the current code, confirm `/sessions` and `/load`
still work post-migration.

### Milestone 5 — Auth scaffolding for the server phase
**Closes:** F4 (planned-but-designed, per the product context's explicit
allowance).
**Files:** new `auth/tokens.py` (JWT issuance/verification, stdlib-adjacent
via a small dependency — see open question 3), `auth/users.py` (add
`user_id` as a real primary key now, from Milestone 4's schema, rather than
username-as-identity), `interfaces/cli.py` (maps today's login to "issue a
token for a local default user," per the product context's own instruction
— "specify how the current CLI maps to this... so nothing needs rewriting
later").
**Design decisions:** the CLI does **not** start requiring a bearer token —
it keeps its interactive username/password prompt. What changes is that
`_login()` returns a `(user_id, token)` pair instead of a bare username, and
every downstream consumer (`Config.for_user`, `SessionStore`, workspace
paths) switches from keying on `username` to keying on `user_id` — the
actual database primary key, not the display name — so a future server's
token-verification middleware can hand the exact same `user_id` to the exact
same downstream code with zero changes there. No password reset, no roles/
permissions beyond today's single-tier account model — explicitly deferred
(§4).
**Verification:** `tests/auth_test.py` extended with token issue/verify
round-trip tests (fake clock for expiry); manual: confirm CLI login still
works unchanged from the user's perspective.

### Milestone 6 — Memory auto-injection + session lifecycle polish
**Closes:** E3, remaining half of F2.
**Files:** `context_engine/memory_tool.py` (add a `summary()`-style read
used at session construction, mirroring how `context_engine/memory_tracker.py`
already renders its own summary), `interfaces/cli.py` (`Session.__init__`
prepends a memory summary to the system prompt when memory files exist,
capped at a small char budget so it doesn't itself blow the context
budget), `context_engine/session_store.py` / `storage/session_store.py`
(add `delete(session_id)`), `interfaces/cli.py` (`/delete <id>` command).
**Design decisions:** injection is capped and summarized, not a raw dump of
every memory file — same "don't blow the budget" instinct that already
governs compaction (B2/B3); a reasonable default is "the top-level memory
file's contents, or its first N chars, plus a note that more exists and how
to `view` it."
**Verification:** extend `tests/memory_test.py` with a "memory content
appears in the assembled system prompt at session start" assertion; manual:
seed `.harness/memory/<user>/notes.md`, start a new session, confirm its
content is visible to the model without it calling the tool first.

### Milestone 7 — Middleware/hook layer
**Closes:** H5, and gives H2 (self-verification in the interactive loop, not
just the pipeline) a real place to live if the owner wants it later.
**Files:** `engine/orchestrator.py` (the only milestone besides #3 that
touches this file — justified explicitly here, matching `AGENTS.md`'s "if a
change forces you to edit the loop, stop and reconsider" rule: this *is*
the considered exception, adding the extension point itself, not a one-off
feature crammed into the loop), new `engine/hooks.py`.
**Design decisions:** a small ordered list of `pre_model_call(conversation)
-> Conversation`, `post_model_call(response) -> Response`,
`pre_tool_call(tool_call, tool) -> ToolCall | denial`, `post_tool_call(result)
-> result` hooks, each optional, each a plain function (matching the
existing `Approver`/`EventHook` callable-not-class style, PRINCIPLES rule
"Interface Segregation... several small, purpose-built interfaces"). This is
additive only — `Orchestrator`'s default behavior with an empty hook list is
byte-for-byte identical to today's loop, so it cannot regress anything.
Compaction is *not* retrofitted onto this mechanism in this milestone
(unnecessary churn on working code) — the point is that the *next*
cross-cutting concern (a guardrail, a redaction step, a real `interrupt()`-style
pause) has somewhere to go without editing the loop again.
**Verification:** new `tests/hooks_test.py` — a fake pre-tool-call hook that
denies a specific tool name, proving the interception point actually
intercepts; run the full existing test suite (all 14+ files from Milestones
1-6) to confirm zero regressions from adding empty-by-default hook plumbing.

### Milestone 8 — Sandbox design (design only, no implementation)
**Closes:** G1 — "design required" per the product context, not
"build it now."
**Files:** none (a design doc, `SANDBOX_DESIGN.md`, added to the repo).
**Design decisions to record:** Docker-container-per-session as the
isolation unit (matching the workspace-per-session shape from Milestone 2 —
the container mounts exactly that session's workspace directory and nothing
else); resource limits via standard Docker flags (`--memory`, `--cpus`,
`--pids-limit`); network policy default-deny with an explicit allowlist for
`fetch_url`/`web_search`/MCP-server domains; `run_command` becomes "exec
inside the session's container" instead of a bare host `subprocess.run`,
with the existing `risk`-tiered permission system staying as the *first*
gate (unchanged) and the container boundary as a *second*, independent one
— defense in depth, not a replacement. Command allow/deny-listing is
explicitly **not** relied upon as the primary control (regexes matching
shell commands are notoriously bypassable) — the container boundary is.
**Verification:** none (no code) — reviewed and approved as a design, same
gate as this plan itself.

### Milestone 9 — Observability latency + minor cleanups
**Closes:** I1's latency gap, D3's git-checkpoint-for-interactive-CLI gap,
C2's missing grep/search tool.
**Files:** `engine/orchestrator.py` (time `provider.complete`/`registry.run`
calls, add duration to the `"usage"`/`"tool_result"` event payloads),
`observability/log.py` (record it), new `engine/builtin/search_files.py`
(the `find_files`/grep tool `CONTRIBUTING.md` already documents as an
example but that was never actually built), `engine/builtin/git_tool.py`
(add a `git_commit` tool, `write`-risk, for interactive-session
checkpointing).
**Verification:** extend `observability`-adjacent tests to assert a
`duration_ms` field appears in logged events; unit test for the new grep
tool (happy path + no-matches path, matching `CONTRIBUTING.md`'s own
"quick unit test... happy path + error path" standard).

---

## 4. Explicit deferrals

Consciously not doing these, and why:

- **A real HTTP/API server.** This plan makes the architecture *ready* for
  one (workspace-per-session, no global state, user_id-keyed storage, a
  token-shaped auth boundary) without building it — building an actual
  request/response server, its routing, and its own approver/on_event
  wiring is a separate, later plan once these prerequisites land, per the
  product context's own phrasing ("planned... architecture must be ready").
- **A real sandbox implementation (Docker-in-the-loop).** Milestone 8 is
  design-only; implementing it means picking a container runtime dependency,
  handling image builds, and solving "how does a container get the model's
  file edits back out efficiently" — real scope, deliberately left for its
  own plan once the design is approved.
- **Reintroducing LiteLLM or adopting LangGraph/deepagents.** Addressed in
  §1 — the evidence doesn't support either; revisit only if a concrete new
  requirement (a non-OpenAI-shaped provider, or genuine branching control
  flow) appears that the current architecture can't cleanly absorb.
- **Streaming token-by-token output.** Real gap (A3) but not blocking any
  stated product requirement (multi-provider, multi-user, memory, storage,
  sandbox) — `DESIGN.md`'s own non-goals list already deferred this once;
  this plan doesn't re-open it.
- **Password reset, roles/permissions per account, SSO.** F4 explicitly
  scopes auth to "enough to map onto a future real system," not "a real
  identity provider" — matches `DESIGN.md` D22's own already-recorded scope.
- **Parallel pipeline slices, cross-model review, pipeline auto-push/PR
  becoming the default.** Already-recorded deferrals in `DESIGN.md`
  (D15/known-limitations); this plan doesn't touch `pipeline/` at all except
  where Milestone 1's provider retry logic naturally benefits it too.
- **Fixing the `_plan` tool's non-persistence.** Milestone 3 makes plan
  state session-scoped (fixing the concurrency hazard) but does **not** make
  it durable across a restart — that's what the `memory` tool is already for
  (D23's own reasoning stands); conflating the two would undo D16's
  intentional split.

---

## 5. Questions needing your decision

1. **The `web_search` fix (Milestone 1):** remove the always-on DuckDuckGo
   scraper entirely and make Tavily the only `web_search` (matching what the
   docs already claim happens today), or keep DuckDuckGo as a distinctly-named
   fallback tool (e.g. `web_search_free`) so search works with zero API-key
   setup? The current docs promise the former; the current code does the
   latter by accident. I'd default to matching the docs (remove it) unless
   you want zero-config search kept as a real feature.
2. **Storage migration scope (Milestone 4):** confirm SQLite-now/
   Postgres-later, per §2 — or would you rather defer the database
   entirely for now and only do Milestones 1-3 + 5-9 this round (i.e., fix
   everything except storage), leaving JSON-file persistence in place a
   while longer?
3. **JWT library choice (Milestone 5):** stdlib has no JWT support — pulling
   one in (e.g. `PyJWT`) is this plan's only proposed *new* dependency
   category (auth) beyond `sqlalchemy`. OK to add it, or do you want a
   simpler, non-JWT token scheme (e.g. opaque random tokens stored in the
   new `sessions`/`users` DB, looked up rather than decoded) to stay
   dependency-light, matching the project's existing bias (D3, D22's own
   "stdlib only" choice for password hashing)?
4. **Workspace confinement enforcement (Milestone 2):** should the
   interactive CLI (`main.py`) start *enforcing* workspace confinement
   immediately (a behavior change — today's users can `read_file` anywhere
   on disk; after this, they're confined to a workspace directory), or
   should confinement be opt-in at first (a config flag, default off for the
   existing single-user CLI, default on for anything server-facing later) so
   current users aren't surprised by a new restriction?

---

**Waiting for your approval (with any modifications) before starting Phase 4.**
