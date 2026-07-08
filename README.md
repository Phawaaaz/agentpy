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

## Architecture

```
interfaces/     thin entry points (CLI, pipeline CLI now; Slack / API later)
core/           orchestrator (the loop) + permissions + context (compaction)
tools/          registry + the tools (filesystem, shell, fetch_url, MCP client, ...)
providers/      model abstraction (anthropic + openai SDKs => any model)
pipeline/       optional outer loop: multi-stage autonomous runs
store/          session persistence (save/resume conversations)
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

## Run

```bash
python main.py
```

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
| `/sessions` | List saved sessions |
| `/cost` | Show token usage + estimated cost |
| `/help` | List commands |

Sessions auto-save after each turn to `.harness/sessions/`; events are traced to
`.harness/logs/`. Long conversations are automatically compacted (older messages
summarized) so they don't overflow the model's context window.

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
```

All four run against fakes — no key, no network — and should print `... PASSED`.
