"""Shell tool: run a command and return its output.

Marked "dangerous" so the permission layer gates it in every mode except `auto`.
"""

import subprocess

from .. import sandbox
from ..registry import Tool, registry
from ..workspace import workspace_root
from .offload import maybe_offload

_MAX_OUTPUT = 20_000


def run_command(command: str, timeout: int = 60) -> str:
    root = workspace_root()

    # Sandbox backend seam (D33): with HARNESS_SANDBOX=docker and a
    # confined workspace, the command runs inside that session's container
    # instead of on the host. Off by default = host execution, unchanged.
    if sandbox.active() and root is not None:
        body = sandbox.exec_in_sandbox(root, command, timeout)
        return maybe_offload(body, _MAX_OUTPUT, "run_command")

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            # Confined sessions run in their workspace dir; unconfined ones
            # keep the historical behavior (the process's own cwd) via None.
            cwd=root,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as exc:
        return f"Error running command: {exc}"

    output = result.stdout or ""
    if result.stderr:
        output += "\n[stderr]\n" + result.stderr
    output = output.strip() or "(no output)"
    body = f"exit code: {result.returncode}\n{output}"
    return maybe_offload(body, _MAX_OUTPUT, "run_command")


registry.register(
    Tool(
        name="run_command",
        description=(
            "Run a shell command in the current working directory and return its "
            "exit code and combined output. Use for building, testing, and inspecting."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run."},
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait. Defaults to 60.",
                },
            },
            "required": ["command"],
        },
        handler=run_command,
        risk="dangerous",
    )
)
