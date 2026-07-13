# Engineering Principles

These are the rules this codebase follows. They are not aspirational — every one
is already reflected in the current code, and every change should keep it that
way. When a principle and convenience conflict, the principle wins or you write
down why it didn't (see [DESIGN.md](DESIGN.md)).

The guiding idea of the whole project: **the loop is fixed; capabilities grow at
the edges.** Adding a tool, a model provider, or an interface must never require
editing the orchestrator. If it does, the design has drifted and needs fixing.

---

## SOLID, applied to this repo

SOLID is only useful when tied to real code. Here is each principle and exactly
where it lives.

### S — Single Responsibility

Each module has one reason to change.

| Module | Its one job | What it does *not* do |
|--------|-------------|-----------------------|
| `engine/orchestrator.py` | Run the observe→think→act loop | Doesn't format output, call SDKs, or run tools itself |
| `engine/permissions.py` | Decide allow / ask / deny | Doesn't prompt the human or execute anything |
| `engine/registry.py` | Store and dispatch tools | Doesn't know what any specific tool does |
| `providers/*_provider.py` | Translate to/from one model API | Doesn't know about the loop or tools' meaning |
| `interfaces/cli.py` | Talk to the human | Doesn't contain agent logic |

**Rule:** if you can't describe a module's job in one sentence without "and",
split it.

### O — Open/Closed

Open for extension, closed for modification.

- Add a **tool** → create a `Tool` and `registry.register(...)` it. The
  orchestrator is untouched. (`engine/builtin/filesystem.py`, `engine/builtin/shell.py`)
- Add a **provider** → subclass `Provider` and add one branch to
  `providers/factory.py`. The loop is untouched.
- Add a **permission mode** → extend `permissions.check`. Callers are untouched.

**Rule:** a new capability should be a new file plus a registration, not an edit
to existing behavior.

### L — Liskov Substitution

Any `Provider` is usable wherever a `Provider` is expected.

- `AnthropicProvider`, `OpenAIProvider`, and the test's `FakeProvider` all honor
  the same contract: `complete(messages, tools) -> Response`.
- `tests/smoke_test.py` proves it — it runs the *real* orchestrator against a
  fake provider and everything works, because the orchestrator only relies on the
  contract, never on a concrete type.

**Rule:** a subtype must not strengthen preconditions or weaken postconditions.
A provider must always return a valid `Response` (text and/or tool calls) — never
raise for a normal model reply, never return a half-filled object.

### I — Interface Segregation

Interfaces are minimal, so implementers aren't forced to satisfy things they
don't use.

- `Provider` has exactly one method. A new backend implements one thing.
- The interface↔core boundary uses two tiny callback types —
  `Approver` and `EventHook` (`engine/orchestrator.py`) — not a fat "UI" object.
  The CLI supplies two small functions; a Slack bot later supplies two different
  ones. Neither implements anything it doesn't need.

**Rule:** prefer several small, purpose-built interfaces (or callables) over one
big one.

### D — Dependency Inversion

High-level policy depends on abstractions, not details.

- `Orchestrator` depends on `Provider` (abstraction) and `Registry`, plus the
  `Approver`/`EventHook` callbacks — all injected through its constructor. It
  imports **no** concrete provider and **no** interface.
- Concrete wiring happens at the outermost layer only: `interfaces/cli.py` calls
  `build_provider(config)` and passes everything in.

**Rule:** construct dependencies at the edge (interfaces/factory) and inject them
inward. Inner layers never `import` outer ones. The dependency arrow always
points toward abstractions.

---

## Beyond SOLID — the day-to-day rules

### 1. Tools never crash the loop
A tool returns a `str` for **every** outcome, including errors (see
`Registry.run` and each handler catching its own exceptions). A failed tool
becomes an observation the model can react to, not a stack trace that kills the
run. **Never** let a tool raise into the orchestrator.

### 2. Fail loud at the edges, fail safe in the middle
- **Config/factory** (`config.py`, `providers/factory.py`): raise immediately on
  bad setup. A misconfigured provider should stop the program with a clear
  message, not silently misbehave.
- **Tools** (during a run): degrade gracefully, return an error string.

The difference: setup errors are the developer's to fix now; runtime tool errors
are the agent's to reason about.

### 3. Dependencies are injected, never reached for
Pass collaborators through constructors/parameters. The only deliberate global is
the shared `registry` singleton — and it's justified in [DESIGN.md](DESIGN.md)
because tool modules self-register on import. Do not add new globals.

### 4. One message format to rule them all
The orchestrator speaks one **neutral** message format (OpenAI-style: roles
`system`/`user`/`assistant`/`tool`). Providers translate neutral↔native at their
own boundary. Nothing outside a provider may contain provider-specific message
shapes. This contract is specified in [ARCHITECTURE.md](ARCHITECTURE.md).

### 5. Type everything public
Public functions and dataclasses carry type hints (see `providers/base.py`,
`engine/registry.py`). Types are documentation that can't go stale.

### 6. Docstrings say *why*, comments explain the non-obvious
Every module has a top docstring stating its purpose. Inline comments explain
intent and gotchas (e.g. the Anthropic tool-result coalescing), not what the code
literally says.

### 7. Small, pure-where-possible functions
Prefer functions that take inputs and return outputs over ones that mutate hidden
state. `permissions.check` is pure. Keep handlers focused; if one grows past a
screen, split it.

### 8. DRY, but don't over-abstract
Two providers share the `Provider` contract, not a forced base implementation.
Don't invent an abstraction until there are at least two real cases for it. The
`OPENAI_COMPATIBLE` map in the factory exists because there were several real
compatible providers — not speculatively.

### 9. Keep the folders honest
The directory layout *is* the architecture. A file's location declares its layer.
Don't put provider logic in `engine/`, or agent logic in `interfaces/`.

### 10. Testability is a design constraint, not an afterthought
Because the orchestrator depends on abstractions, it's testable without network,
keys, or real models (`tests/smoke_test.py`). Any new core logic must be
reachable by a test that uses fakes. If something can only be tested by calling a
real API, it's in the wrong layer.

---

## Pull-request checklist

Before considering a change done:

- [ ] Did I add capability at the edge (new file + registration) rather than
      editing the loop?
- [ ] Does every new tool return a string on all paths, including errors?
- [ ] Are new dependencies injected, not globally reached for?
- [ ] Do inner layers still avoid importing outer layers?
- [ ] Are public functions typed and modules docstringed?
- [ ] Can the new logic be tested with fakes? Did I add/extend a test?
- [ ] If I broke a principle on purpose, did I record why in DESIGN.md?
