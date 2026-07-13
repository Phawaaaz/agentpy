# Contributing / Extension Guide

How to change this codebase without breaking its design. Read
[PRINCIPLES.md](PRINCIPLES.md) once; it's the standard every change is held to.
This file is the practical "how do I add X" companion.

The golden rule: **you should almost never edit `engine/orchestrator.py`.** New
capability goes at the edges — a new tool, provider, or interface.

## Setup

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # set HARNESS_MODEL and HARNESS_API_KEY
python tests/smoke_test.py  # should print SMOKE TEST PASSED (no key needed)
```

---

## How to add a TOOL (the common case)

A tool is a function plus a schema. Example — add a `find_files` tool:

1. Create or open a module in `engine/builtin/` and write the handler. **It must
   return a string on every path, including errors** (PRINCIPLES rule 1):

   ```python
   # engine/builtin/search.py
   import glob as _glob
   from ..registry import Tool, registry

   def find_files(pattern: str) -> str:
       try:
           matches = _glob.glob(pattern, recursive=True)
       except Exception as exc:
           return f"Error: {exc}"
       return "\n".join(matches) if matches else "(no matches)"

   registry.register(
       Tool(
           name="find_files",
           description="Find files matching a glob pattern (supports **).",
           parameters={
               "type": "object",
               "properties": {
                   "pattern": {"type": "string", "description": "Glob pattern."}
               },
               "required": ["pattern"],
           },
           handler=find_files,
           risk="safe",   # safe | write | dangerous  — drives permissions
       )
   )
   ```

2. Make sure the module is imported so registration runs. Tool modules are
   imported in `interfaces/cli.py` (and `tests/smoke_test.py`):

   ```python
   import engine.builtin.search  # noqa: F401
   ```

3. Choose the right `risk`:
   - `safe` — read-only, no side effects (runs without prompting in `ask` mode).
   - `write` — changes files/state (prompts in `ask`, allowed in `allowlist`).
   - `dangerous` — shell/network/irreversible (prompts in `ask`, **denied** in
     `allowlist`, only free in `auto`).

4. Write a description the *model* will rely on: say what it does, when to use it,
   and what it returns. The description is the model's only knowledge of the tool.

That's it — no orchestrator change. Adding a company integration (Jira, DB,
Drive) is the same pattern; keep credentials in config/env, not in the tool.

---

## How to add a PROVIDER (a new model backend)

Only needed for a backend that is neither Anthropic-native nor OpenAI-compatible
(most new ones *are* OpenAI-compatible — just use a `base_url`, no code).

1. Subclass `Provider` and implement `complete` — **neutral in, neutral out**
   (see the message-format contract in [ARCHITECTURE.md](ARCHITECTURE.md)):

   ```python
   # providers/gemini_provider.py
   from .base import Provider, Response, ToolCall

   class GeminiProvider(Provider):
       def __init__(self, model, api_key=None, temperature=0.0):
           ...
       def complete(self, messages, tools) -> Response:
           # 1. translate neutral `messages` + `tools` to the native API
           # 2. call the model
           # 3. translate the reply back into Response(text, tool_calls,
           #    assistant_message)  where assistant_message is a NEUTRAL dict
           ...
   ```

2. Register it in the factory (`providers/factory.py`) — one branch:

   ```python
   if prefix == "gemini":
       return GeminiProvider(model=model_name, api_key=config.api_key, ...)
   ```

3. Uphold the contract (PRINCIPLES: Liskov): always return a valid `Response`;
   never raise for a normal reply; `tool_calls` empty means "done".

4. Verify against the fake-free path: point `HARNESS_MODEL=gemini/...` and run a
   real task, plus confirm the factory routes it (`build_provider`).

---

## How to add an INTERFACE (CLI → Slack, HTTP, …)

An interface is thin: capture input, report events, handle approvals, wire deps.

1. Build the two callbacks the core needs:
   - `approver(tool_call, tool) -> bool` — how *this* channel asks a human.
   - `on_event(kind, *details)` — how *this* channel shows progress.

2. Wire and run, reusing the factory:

   ```python
   # interfaces/slack.py (sketch)
   from config import Config
   from engine.orchestrator import Orchestrator
   from providers.factory import build_provider
   from engine.registry import registry
   import engine.builtin.filesystem, engine.builtin.shell  # register tools

   def handle_message(text, say):
       config = Config.load()
       agent = Orchestrator(
           build_provider(config), registry, config,
           approver=my_slack_approver, on_event=my_slack_event,
       )
       say(agent.run(text))
   ```

3. Put **no agent logic** in the interface (PRINCIPLES: Single Responsibility).
   If you're tempted to make a decision about tools or the loop here, it belongs
   in `engine/`.

---

## How to connect an MCP server

No code needed — add it to `.harness/mcp.json` (copy `mcp.json.example`):

```json
{"mcpServers": {"my-server": {"command": "npx", "args": ["-y", "some-mcp-server"]}}}
```

It connects automatically on CLI startup; use `/mcp connect my-server` to
connect one without restarting. Its tools appear as `mcp__my-server__<tool>`
with risk inferred from the server's own tool annotations (see DESIGN.md D14).
A remote server uses `"url"` (+ `"transport": "http"` or `"sse"`) instead of
`"command"`/`"args"`.

## How to add a PIPELINE STAGE

The autonomous pipeline (`pipeline/`, run via `python pipeline.py "<task>"`)
runs implement → self-review → verify → test → sync-docs as a fixed sequence
of bounded `Orchestrator.run()` calls. To add a stage:

1. Add a prompt builder to `pipeline/stages.py` (same shape as
   `verify_prompt`/`sync_docs_prompt`): takes the task + current diff, returns
   a prompt string ending with `stages.COMPLETION_INSTRUCTIONS`.
2. Call it from `pipeline/runner.py`'s `_run_outer_stages` (or the implement
   loop, if the new stage should iterate), via the existing `run_and_commit`
   helper so it's committed and logged to `progress.log` the same way every
   other stage is.

`engine/orchestrator.py` is never touched for this — the pipeline only composes
it (DESIGN.md D15).

## How to add a SKILL (an on-demand slash command)

Two ways, depending on whether it's yours-and-yours or the harness's own:

**No code — external skill (the common case):** add an entry to
`.harness/skills.json` (copy `skills.json.example`):

```json
{"skills": {"my-skill": {
  "description": "what it does",
  "prompt": "Do the thing.\n\nTASK:\n{task}\n\nCHANGES (--stat):\n{diff_stat}"
}}}
```

`{task}` and `{diff_stat}` are substituted in (plain string replacement, not
`str.format` — other `{braces}` in your prompt are left alone). `/my-skill`
shows up the next time the CLI starts, merged with the built-ins (D18); the
same name as a built-in overrides it, with a startup notice.

**Built into the harness itself:**

1. Add a `def my_skill_prompt(task: str, diff_stat: str) -> str:` to
   `pipeline/stages.py` (copy `verify_prompt`'s shape).
2. Add `"myskill": stages.my_skill_prompt` to `interfaces/cli.py`'s `_SKILLS`
   dict and a line to `HELP`. That's it — `/myskill` now runs it against the
   current conversation via `session.agent.run(...)`.

## How to add a sub-agent ROLE

No code — add an entry to `.harness/roles.json` (copy `roles.json.example`):

```json
{"roles": {"my-role": {"description": "when to delegate here", "system_prompt": "..."}}}
```

It appears in `/roles` and the `delegate` tool's `role` enum the next time
the CLI starts. See DESIGN.md D17 for how delegation works (a tool call, not
a new control flow) and why sub-agents can't recursively delegate.

---

## Coding standards (short version)

- Type-hint public functions and dataclasses.
- Module-level docstring on every file stating its one job.
- Inner layers never import outer layers (`core` must not import `interfaces`).
- Inject dependencies through constructors/parameters; add no new globals.
- Tools return strings and never raise; setup code (config/factory) may raise
  loudly.
- Keep functions small; split anything past ~a screen.

Full rationale and the PR checklist are in [PRINCIPLES.md](PRINCIPLES.md).

## Testing

- `python tests/smoke_test.py` exercises the whole loop with a `FakeProvider` —
  no key, no network. Keep it green.
- New core logic must be testable with fakes. If it can only be tested against a
  live API, it's in the wrong layer.
- When adding a tool, a quick unit test of the handler (happy path + error path)
  is enough; the loop already has coverage.
- `tests/mcp_test.py` fakes the MCP session itself (duck-typed, no real
  subprocess/network) — extend it the same way for new MCP-related logic.
- `tests/pipeline_test.py` scripts a `FakeProvider` per stage call against a
  real, local, disposable git repo (fast, no network) — extend it the same
  way for new pipeline stages or safety rails.
