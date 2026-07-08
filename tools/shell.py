"""Shell tool: run a command and return its output.

Marked "dangerous" so the permission layer gates it in every mode except `auto`.
"""

import subprocess

from .registry import Tool, registry

_MAX_OUTPUT = 20_000


def run_command(command: str, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
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
    if len(body) > _MAX_OUTPUT:
        body = body[:_MAX_OUTPUT] + "\n... [truncated]"
    return body


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
