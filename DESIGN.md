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
**Decision:** one loop in `core/orchestrator.py`: call model → run any requested
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
pushing translation into providers keeps `core/` provider-agnostic. OpenAI's
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
**Decision:** `tools/registry.py` exposes one module-level `registry`; tool
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
**Decision:** move history out of the orchestrator into `core/context.py`. The
`Conversation` owns messages and compaction; the orchestrator just calls
`add`/`to_list`/`maybe_compact`.
**Why:** Single Responsibility — "manage the window" is a different job from "run
the loop." It also isolates the token heuristic and cut logic for testing.
**Trade-off:** one more object to wire. Worth it; the loop got simpler.

### D11 — Compaction by injected summarizer, not hard-coded (Phase 2)
**Decision:** when history exceeds `max_context_tokens`, fold the oldest messages
into a running summary produced by an *injected* `Summarizer`. The cut slides
past leading `tool` messages so a tool result is never orphaned from its call.
**Why:** Dependency Inversion keeps `context.py` free of any provider import and
testable with a fake summarizer (see `tests/phase2_test.py`). The summary lives
in the system prompt, which sidesteps role/pairing issues entirely.
**Alternatives:** naive truncation (loses information) or no compaction (window
overflows). Summarization keeps the thread coherent.
**Trade-off:** compaction costs an extra model call. Acceptable and infrequent.

### D12 — Persistence as a swappable `SessionStore` (Phase 2)
**Decision:** `store/session_store.py` serializes a `Conversation.snapshot()` to
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

---

## Known limitations & future work

- **Token counts are estimated for compaction triggers:** the ~4-chars/token
  heuristic (`estimate_tokens`) is approximate; the actual per-turn usage from
  the API is exact and used for cost. Good enough to decide *when* to compact.
- **Prices drift:** `PRICING` in `observability/usage.py` is manually maintained.
- **Single-user persistence:** JSON session files; see D12.
- **Parallel tool calls run sequentially:** the loop executes a turn's tool calls
  in order. Fine for correctness; a future optimization could parallelize
  independent, read-only calls.
- **Coarse risk model:** see D5.
- **No web *search* yet:** only `fetch_url` (needs a known URL). Search needs an
  API key or scraping — deferred to Phase 3.

Every item above has a home in the existing structure — none requires reshaping
the loop.
