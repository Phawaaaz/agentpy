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
┌──────────────────────────────────────────────────────────────┐
│ interfaces/     CLI now; Slack / HTTP API later                │  ← talks to humans/systems
├──────────────────────────────────────────────────────────────┤
│ core/           orchestrator (the loop) + permissions          │  ← policy / control flow
├──────────────────────────────────────────────────────────────┤
│ tools/          registry + dispatcher + the tools              │  ← the agent's hands
├──────────────────────────────────────────────────────────────┤
│ providers/      Provider interface + per-model adapters        │  ← the agent's mouth/ears
└──────────────────────────────────────────────────────────────┘
   config.py       settings resolved once, injected at the edge
   store/          session persistence (save/resume conversations)   ┐ cross-cutting:
   observability/  usage/cost tracking + event logging              ┘ touch every layer
```

Dependencies point **downward and inward, toward abstractions**. `core/` depends
on the `Provider` interface and the `Registry`, never on a concrete provider or
an interface. Wiring happens only in `interfaces/` via `providers/factory.py`.

## Component reference

| File | Type(s) | Responsibility |
|------|---------|----------------|
| `config.py` | `Config` | Resolve settings from env/`.env` once |
| `providers/base.py` | `Provider`, `Response`, `ToolCall` | The model abstraction + normalized data |
| `providers/anthropic_provider.py` | `AnthropicProvider` | Neutral ↔ Claude API translation |
| `providers/openai_provider.py` | `OpenAIProvider` | OpenAI + any compatible endpoint |
| `providers/factory.py` | `build_provider` | Model string → concrete provider |
| `tools/registry.py` | `Tool`, `Registry`, `registry` | Hold tool schemas; dispatch by name |
| `tools/filesystem.py` | read/write/edit/list | Filesystem tools (self-register) |
| `tools/shell.py` | run_command | Shell tool (self-register) |
| `core/permissions.py` | `check` | allow / ask / deny decision |
| `core/context.py` | `Conversation`, `make_provider_summarizer` | Hold history; compact it when over budget |
| `core/orchestrator.py` | `Orchestrator` | The agent loop + tool gating |
| `store/session_store.py` | `SessionStore` | Save/load a conversation as JSON |
| `observability/usage.py` | `UsageTracker`, `cost_for` | Accumulate tokens; estimate spend |
| `observability/log.py` | `EventLogger` | Append a JSONL trace of events |
| `interfaces/cli.py` | `main`, `Session` | Terminal I/O, approvals, session commands, wiring |

## The request lifecycle

What happens on one `agent.run("...")` call (`core/orchestrator.py`):

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
OpenAI-style. Everything in `core/` and `interfaces/` uses only this. Providers
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

## Extension points (where new work goes)

| To add… | Do this | Files touched |
|---------|---------|---------------|
| A tool | Define a `Tool`, `registry.register(...)`, import the module | new `tools/x.py` |
| A provider | Subclass `Provider`, add a branch in the factory | new `providers/x.py`, `factory.py` |
| An interface | New module supplying `approver` + `on_event`, wire via `build_provider` | new `interfaces/x.py` |
| A permission mode | Extend `permissions.check` | `permissions.py` |

Step-by-step recipes are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Limits & safeguards (current)

- `config.max_steps` caps tool-using iterations per request (runaway guard).
- `config.max_tokens` bounds model output per turn.
- `config.max_context_tokens` triggers history compaction before the window
  overflows; `keep_recent_messages` stays verbatim.
- Tool outputs are truncated (~20k chars) to protect the context window.
- The permission layer gates every action; `dangerous` tools never run silently
  outside `auto` mode.
- Sessions auto-save after each turn (`store/`); every event is traced to a JSONL
  log (`observability/log.py`).

## Roadmap position

- **Phase 1 (done):** loop, model-independence, permissions, filesystem/shell
  tools, CLI, smoke test.
- **Phase 2 (done):** context management (history compaction), session
  persistence, observability + cost tracking, `fetch_url` web tool.
- **Phase 3:** integrations (Jira, DB, Drive), HTTP API, per-user config, Slack
  interface, web *search*.

These phases slot into the existing folders without reshaping the loop.
