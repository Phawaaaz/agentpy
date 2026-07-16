# Sandbox Design (G1 — design approved before any implementation)

Status: **design only.** Nothing in this document is built yet; it exists so
the sandbox milestone starts from an agreed shape instead of improvisation
(AUDIT.md G1, PLAN.md Milestone 8). Implementation is a separate, future
milestone with its own tests and rollout.

## What problem this solves — and what it doesn't

Today `run_command` executes directly on the host (`engine/builtin/shell.py`:
`subprocess.run(shell=True)`). The permission layer (D5) decides *whether* a
command runs; nothing constrains *what it can reach once running*. Workspace
confinement (D27) pins the cwd and the filesystem tools' paths, but a
running command can still read any host path the process can, call any
binary on PATH, and reach the network. `auto` permission mode's own
documentation says "use only in a sandbox" — this is that sandbox.

Out of scope here: protecting users from each other's *data* (already done —
D22/D27/D29 isolation), and model-level guardrails (D32 hooks are the home
for those).

## Isolation unit: one Docker container per session

- **Boundary choice:** a container per `(user, session)`, created lazily on
  the session's first `run_command` and reaped on session end plus an idle
  TTL. Matches the workspace-per-session shape from D27 exactly: the
  container bind-mounts **only** `workspaces/{user}/{session}/` (as the
  container's working directory) and nothing else from the host.
- **Why Docker and not a VM/microVM (Firecracker, Kata):** the threat model
  is "an LLM-driven command shouldn't touch the host or other users," not
  "defend against a determined kernel-exploit adversary." Containers give
  that with zero exotic infrastructure, run on the owner's existing
  machines, and the interface below hides the runtime — swapping in gVisor
  (`--runtime=runsc`) or a microVM later is a config change, not a redesign.
- **Image:** one configurable base image (`HARNESS_SANDBOX_IMAGE`, default a
  slim Python+git+coreutils image published with the harness). Per-project
  images are a later concern; the config seam exists from day one.

## Resource limits (all standard Docker flags, all configurable)

| Limit | Default | Flag |
|---|---|---|
| Memory | 2 GiB | `--memory=2g` |
| CPU | 2 cores | `--cpus=2` |
| PIDs (fork bombs) | 256 | `--pids-limit=256` |
| Disk (workspace) | quota on the workspace volume | volume driver / `--storage-opt` |
| Wall clock per command | existing `timeout` param (60s default) | enforced by the exec call, as today |
| Privileges | none | `--cap-drop=ALL --security-opt=no-new-privileges --read-only` (rootfs read-only; workspace mount and `/tmp` writable) |

## Network policy: default-deny, explicit allowlist

- Container runs with `--network=none` by default. Commands that need the
  network (`pip install`, `git clone`) go through an **egress proxy**
  container on a shared internal network: the proxy allows a configured
  domain allowlist (`HARNESS_SANDBOX_ALLOW_HOSTS`, e.g. `pypi.org`,
  `github.com`) and denies everything else.
- `fetch_url`/`web_search`/MCP connections are **not** routed through the
  sandbox — they already run in the harness process under the permission
  layer, and moving them adds nothing (they reach exactly the URL/API they
  were asked to reach).

## Command allow/deny-listing: explicitly NOT the primary control

Regex-matching shell commands is notoriously bypassable (`bash -c`, `$()`,
encodings) — a denylist would be security theater. The container boundary is
the control. The only string-level check kept is a small, *advisory*
denylist of obviously-catastrophic patterns (`rm -rf /`, `mkfs`, fork-bomb
idioms) that produces a friendlier early error than the container's own
failure would; it is documented as UX, not security.

## How it lands in the code (unchanged architecture)

- `engine/builtin/shell.py`'s `run_command` gains a backend seam:
  `HARNESS_SANDBOX=off|docker` (default `off` — exactly today's behavior,
  same opt-in rollout convention as D27). With `docker`, the handler calls a
  new `engine/sandbox.py` (`SandboxManager.exec(session_key, command,
  timeout) -> (exit_code, output)`) instead of `subprocess.run`. Same
  string-in/string-out tool contract; the orchestrator, registry, and
  permission layer are untouched.
- The permission layer stays the **first** gate exactly as-is (a `dangerous`
  tool still prompts/denies per mode); the container is a **second,
  independent** layer. Defense in depth, not replacement.
- `SandboxManager` owns container lifecycle (create-on-first-use, exec,
  idle reap, remove-on-session-end) — the same stateful-manager pattern as
  `MCPManager` (D14), including being the documented exception to
  "tools are plain functions."
- Filesystem tools (`read_file` etc.) stay in the harness process operating
  on the same workspace directory the container mounts — both sides see the
  same files, so no result-copying protocol is needed.

## Failure modes

- Docker not installed / daemon down with `HARNESS_SANDBOX=docker`: fail
  **loud at startup** (PRINCIPLES rule 2 — it's a setup error), not at the
  first tool call mid-task.
- Container OOM/limit kill: surfaces as an ordinary error-string tool result
  with the limit named, so the model can react (e.g. split the work).
- Image pull failures: retried once, then loud startup failure.

## Verification plan (for the implementation milestone, not now)

- Unit: `SandboxManager` against a fake `docker` CLI (same fake-subprocess
  pattern as `tests/pipeline_test.py`).
- Integration (gated on Docker being present, skipped otherwise): a real
  container run proving (a) a command cannot read a host file outside the
  workspace, (b) `--network=none` blocks egress, (c) the memory limit kills
  a hog and the error string names the limit, (d) `HARNESS_SANDBOX=off`
  reproduces today's behavior byte-for-byte.

## Deliberately deferred

- Per-project custom images; GPU passthrough; rootless-Docker/gVisor/microVM
  hardening tiers (the seam exists); sandboxing `fetch_url`/MCP (see above);
  Windows container support (the owner's Windows target runs commands
  host-side with `HARNESS_SANDBOX=off` until WSL2-backed Docker is
  validated).
