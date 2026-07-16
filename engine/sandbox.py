"""Containerized command execution (D33 -- implements SANDBOX_DESIGN.md).

`run_command` normally runs on the host. With HARNESS_SANDBOX=docker it runs
inside a per-session Docker container that mounts *only* that session's
workspace directory (D27), with resource limits, dropped capabilities, and
default-deny networking -- the second, independent layer under the
permission gate (D5) that `auto` mode's docs have always assumed.

Design faithfully followed with one recorded deviation: the container is
persistent per session (created on first command via `docker run -d ...
sleep infinity`, reused by `docker exec` for later commands, torn down on
session end) rather than fresh-per-command -- so installed packages and
background state survive within a session, matching a real shell.

Shells out to the `docker` CLI (no new Python dependency -- same choice as
engine/mcp_client.py and pipeline/worktree.py). The `runner` is injectable
so unit tests assert the exact docker arguments without a daemon; a gated
integration test exercises a real container.
"""

import hashlib
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable

# (args_after_"docker", timeout_s) -> (returncode, combined_output)
Runner = Callable[[list[str], float], tuple[int, str]]

_CONTAINER_PREFIX = "harness-sbx-"
_WORKDIR = "/workspace"
# `timeout` kill exit codes: 124 (GNU), 143 (busybox SIGTERM), 137 (SIGKILL).
_TIMEOUT_CODES = {124, 143, 137}


def _subprocess_runner(args: list[str], timeout: float) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return 124, f"Error: docker command timed out after {timeout}s"
    except FileNotFoundError:
        return 127, "Error: docker is not installed or not on PATH"
    out = (result.stdout or "") + (result.stderr or "")
    return result.returncode, out


@dataclass
class SandboxConfig:
    image: str = "python:3.11-slim"
    memory: str = "2g"
    cpus: str = "2"
    pids: int = 256
    network: str = "none"  # none = default-deny egress; "bridge" to allow


class SandboxError(Exception):
    """A setup failure (daemon down, image unpullable) -- raised at
    configure time so it fails loud at startup, not mid-task."""


class SandboxManager:
    """Owns one container per session workspace. Same stateful-manager
    pattern as MCPManager (a documented exception to 'tools are plain
    functions')."""

    def __init__(self, config: SandboxConfig, runner: Runner | None = None) -> None:
        self.config = config
        self._run = runner or _subprocess_runner
        self._started: set[str] = set()  # workspace paths with a live container

    def preflight(self) -> None:
        """Fail loud if the daemon is unreachable (PRINCIPLES rule 2)."""
        code, out = self._run(["info", "--format", "{{.ServerVersion}}"], 30)
        if code != 0:
            raise SandboxError(f"Docker daemon not reachable: {out.strip()}")

    def _container_name(self, workspace: str) -> str:
        digest = hashlib.sha256(os.path.abspath(workspace).encode()).hexdigest()[:16]
        return f"{_CONTAINER_PREFIX}{digest}"

    def _ensure(self, workspace: str) -> str:
        name = self._container_name(workspace)
        if workspace in self._started:
            return name
        os.makedirs(workspace, exist_ok=True)
        # Remove any orphan of the same name from a prior crashed process.
        self._run(["rm", "-f", name], 30)
        code, out = self._run(
            [
                "run", "-d", "--name", name,
                "--memory", self.config.memory,
                "--cpus", self.config.cpus,
                "--pids-limit", str(self.config.pids),
                "--network", self.config.network,
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--read-only",                       # rootfs read-only...
                "--tmpfs", "/tmp:rw,exec",           # ...except /tmp
                "-v", f"{os.path.abspath(workspace)}:{_WORKDIR}:rw",  # ...and the workspace
                "-w", _WORKDIR,
                self.config.image,
                "sleep", "infinity",
            ],
            120,
        )
        if code != 0:
            raise SandboxError(f"failed to start sandbox container: {out.strip()}")
        self._started.add(workspace)
        return name

    def exec(self, workspace: str, command: str, timeout: int = 60) -> str:
        """Run `command` in the session's container; return the same
        'exit code: N\\n<output>' string run_command produces on the host.
        A setup failure returns an error string (never raises into the
        loop) so a broken sandbox degrades like any other tool error."""
        try:
            name = self._ensure(workspace)
        except SandboxError as exc:
            return f"Error: {exc}"
        # Enforce the timeout *inside* the container with `timeout(1)`, so a
        # runaway command is actually killed rather than merely abandoned by
        # a client-side `docker exec` timeout (which leaves the process
        # running against the container's limits). A small grace margin lets
        # the client-side deadline act only as a backstop.
        wrapped = f"timeout -k 2 {int(timeout)} sh -c {shlex.quote(command)}"
        code, out = self._run(["exec", name, "sh", "-c", wrapped], timeout + 5)
        body = out.strip() or "(no output)"
        # `timeout`'s exit code for a kill differs by implementation: GNU
        # coreutils returns 124, busybox (alpine) returns 143 (SIGTERM) or
        # 137 (SIGKILL from -k). Treat all three as a timeout kill.
        if code in _TIMEOUT_CODES:
            body = f"Error: command timed out after {timeout}s (killed)\n{body}".rstrip()
        return f"exit code: {code}\n{body}"

    def close(self, workspace: str) -> None:
        if workspace in self._started:
            self._run(["rm", "-f", self._container_name(workspace)], 30)
            self._started.discard(workspace)

    def close_all(self) -> None:
        for workspace in list(self._started):
            self.close(workspace)


# --- process-wide wiring (configured once at startup by the interface) ------
# The config is process-wide; only the container *key* (the workspace path)
# is per-session, so a single manager keyed internally by workspace is right
# -- same shape as MCPManager. Off by default: no manager, host execution.

_manager: SandboxManager | None = None


def configure(config: SandboxConfig, runner: Runner | None = None) -> None:
    """Turn the sandbox on. Verifies the daemon now so a missing/broken
    Docker fails at startup, not on the first command."""
    global _manager
    manager = SandboxManager(config, runner)
    manager.preflight()
    _manager = manager


def active() -> bool:
    return _manager is not None


def exec_in_sandbox(workspace: str, command: str, timeout: int = 60) -> str:
    if _manager is None:
        raise RuntimeError("sandbox not configured")  # never happens: guarded by active()
    return _manager.exec(workspace, command, timeout)


def shutdown() -> None:
    if _manager is not None:
        _manager.close_all()
