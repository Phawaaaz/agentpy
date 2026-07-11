# AGENTS.md

Instructions for **any** AI coding assistant working in this repository. This is
the vendor-neutral entry point ([agents.md](https://agents.md) convention). Read
this first, then follow the links below before making changes.

## What this project is

A small, **model-independent agentic harness**: an LLM wrapped in a loop that
lets it use tools (read/write files, run commands) to complete real tasks. It is
built to start minimal and grow into a company-wide coding + automation
assistant.

## The one invariant — do not break it

**The agent loop is fixed; capability grows at the edges.**

You should almost never edit `engine/orchestrator.py`. New power is added as:
- a new **tool** (`engine/builtin/`),
- a new model **provider** (`providers/`),
- a new **interface** (`interfaces/`).

If a change forces you to edit the loop, stop — the design has drifted. Re-read
[DESIGN.md](DESIGN.md) and reconsider.

## Read these before changing code

| Read this | For |
|-----------|-----|
| [PRINCIPLES.md](PRINCIPLES.md) | The rules this code must follow (SOLID + practices) and the PR checklist. **Non-negotiable.** |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Layers, the request lifecycle, and the message-format contract. |
| [DESIGN.md](DESIGN.md) | Why each decision was made; where deviations are recorded. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Copy-paste recipes for adding a tool / provider / interface. |

## Project layout

```
interfaces/     thin entry points (CLI, pipeline CLI now; Slack / API later)
engine/         orchestrator (the loop) + permissions + registry + MCP client + built-in tools (engine/builtin/)
context_engine/ everything persisted/remembered: compaction, memory tool, activity tracker, session store (D20)
providers/      Provider interface + per-model adapters (Anthropic, OpenAI-compatible)
pipeline/       optional outer loop: multi-stage autonomous runs, composes engine/ (D15)
multiagent/     optional outer layer: delegate-to-sub-agent tool, composes engine/ (D17)
observability/  token usage + cost estimate + JSONL event logging (D16)
config.py       settings resolved once from env/.env, injected at the edge
tests/          smoke/phase2/mcp/pipeline/memory/cli_skills/external_skills/multiagent/offload/model_switch_test.py — all fakes, no key
```

## Hard rules (enforced, not suggestions)

1. **Tools return a string on every path, including errors. Never raise into the
   loop.** A tool failure is an observation the model reacts to, not a crash.
2. **One neutral message format** (OpenAI-style) everywhere except inside a
   provider. Providers translate neutral ↔ native at their own boundary.
3. **Inner layers never import outer layers.** `engine/` must not import
   `interfaces/`; nothing imports a concrete provider except `providers/factory.py`.
4. **Inject dependencies through constructors/parameters.** The only allowed
   global is the shared `registry` singleton (see DESIGN.md D8). Add no new globals.
5. **Fail loud at the edges** (config/factory may raise on bad setup); **fail safe
   in the middle** (tools degrade to error strings).
6. **Type public functions; docstring every module** with its one job.
7. Adding a capability = a new file + a registration, **not** an edit to existing
   behavior (Open/Closed).

## How to run and verify

```bash
# setup
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

# verify the system WITHOUT any API key (must stay green):
python tests/smoke_test.py        # prints: SMOKE TEST PASSED
python tests/phase2_test.py       # prints: PHASE 2 TESTS PASSED
python tests/mcp_test.py          # prints: MCP TESTS PASSED
python tests/pipeline_test.py     # prints: PIPELINE TESTS PASSED
python tests/memory_test.py       # prints: MEMORY TESTS PASSED
python tests/cli_skills_test.py   # prints: CLI SKILLS TESTS PASSED
python tests/external_skills_test.py  # prints: EXTERNAL SKILLS TESTS PASSED
python tests/multiagent_test.py   # prints: MULTIAGENT TESTS PASSED
python tests/offload_test.py      # prints: OFFLOAD TESTS PASSED
python tests/model_switch_test.py # prints: MODEL SWITCH TESTS PASSED

# run for real (after: cp .env.example .env; set HARNESS_MODEL + HARNESS_API_KEY):
python main.py                    # interactive CLI
python pipeline.py "<task>"       # autonomous multi-stage pipeline (see pipeline/)
```

**Always run all ten test files after a change** and keep them passing.
New core logic must be testable with fakes — if it can only be tested against a
live API, it's in the wrong layer.

## Quick task recipes

- **Add a tool:** define a `Tool`, `registry.register(...)`, import the module,
  pick a `risk` (`safe`/`write`/`dangerous`). Details in CONTRIBUTING.md.
- **Add a model backend:** if it's OpenAI-compatible, just set `HARNESS_BASE_URL`
  — no code. Otherwise subclass `Provider` and add one branch to the factory.
- **Add an interface:** supply an `approver` and an `on_event` callback, wire with
  `build_provider(config)`. Put no agent logic in the interface.
- **Connect an MCP server:** add it to `.harness/mcp.json` (copy
  `mcp.json.example`), or `/mcp connect <name>` at runtime. No code needed —
  its tools register dynamically (D14).
- **Add a pipeline stage:** add a prompt builder to `pipeline/stages.py` and
  call it from `pipeline/runner.py`'s stage sequence. The base loop
  (`engine/orchestrator.py`) is untouched either way.
- **Add a skill:** no code needed — add an entry to `.harness/skills.json`
  (copy `skills.json.example`). Only touch `pipeline/stages.py` +
  `interfaces/cli.py`'s `_SKILLS` dict for a *built-in* skill shipped with
  the harness itself (D18).
- **Add a sub-agent role:** add an entry to `.harness/roles.json` (copy
  `roles.json.example`). No code needed (D17); the `delegate` tool picks up
  new roles the next time the CLI starts.
- **Switch models at runtime:** `/model <name>` in the CLI rebuilds the
  provider from a new model string and keeps conversation history (D21). No
  code needed to use it; `HARNESS_MODEL` still sets the starting model.

## Environment / platform notes

- Target machine is Windows without admin rights and without long-path support.
  Avoid dependencies that install deeply nested file paths (this is why LiteLLM
  was dropped in favor of native SDKs — see DESIGN.md D3).
- Secrets live in `.env` (gitignored), never in code. Use `Config` to read them.

## When you finish a change

Walk the PR checklist at the end of [PRINCIPLES.md](PRINCIPLES.md). If you broke a
principle on purpose, record why in [DESIGN.md](DESIGN.md) as a new decision.
