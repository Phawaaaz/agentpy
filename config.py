"""Central configuration for the harness.

Everything the rest of the system needs to know about *how* to run lives here,
loaded once from environment variables (and a .env file if present).
"""

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()  # pulls values from a local .env file into the environment
except ImportError:
    pass  # dotenv is optional; env vars still work without it


DEFAULT_SYSTEM_PROMPT = """You are a helpful agent running inside a tool-using harness.

You complete tasks by calling the tools available to you rather than guessing.
Work in small, verifiable steps: inspect before you change, and check your work
after you change something (for example, run tests or re-read a file). When the
task is finished, stop calling tools and give a short, clear summary of what you
did. If you cannot complete the task, say so plainly and explain why.
"""


@dataclass
class Config:
    """Resolved settings for one run of the harness."""

    model: str = "anthropic/claude-opus-4-8"
    api_key: str | None = None
    base_url: str | None = None  # for OpenAI-compatible endpoints (Ollama, etc.)
    permission_mode: str = "ask"  # ask | allowlist | auto
    max_steps: int = 25
    max_tokens: int = 4096
    temperature: float = 0.0
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    # Context management: fold old history into a summary past this token budget.
    max_context_tokens: int = 100_000
    keep_recent_messages: int = 20
    # Persistence / observability locations.
    sessions_dir: str = ".harness/sessions"
    logs_dir: str = ".harness/logs"
    # MCP servers to connect to at startup (Claude-Desktop-style config file).
    mcp_config_path: str = ".harness/mcp.json"
    # Where the memory tool and the automatic activity tracker write notes.
    memory_dir: str = ".harness/memory"

    @classmethod
    def load(cls) -> "Config":
        """Build a Config from environment variables, falling back to defaults."""
        return cls(
            model=os.getenv("HARNESS_MODEL", cls.model),
            api_key=os.getenv("HARNESS_API_KEY") or None,
            base_url=os.getenv("HARNESS_BASE_URL") or None,
            permission_mode=os.getenv("HARNESS_PERMISSION_MODE", cls.permission_mode),
            max_steps=int(os.getenv("HARNESS_MAX_STEPS", str(cls.max_steps))),
            max_tokens=int(os.getenv("HARNESS_MAX_TOKENS", str(cls.max_tokens))),
            max_context_tokens=int(
                os.getenv("HARNESS_MAX_CONTEXT_TOKENS", str(cls.max_context_tokens))
            ),
            keep_recent_messages=int(
                os.getenv("HARNESS_KEEP_RECENT_MESSAGES", str(cls.keep_recent_messages))
            ),
            sessions_dir=os.getenv("HARNESS_SESSIONS_DIR", cls.sessions_dir),
            logs_dir=os.getenv("HARNESS_LOGS_DIR", cls.logs_dir),
            mcp_config_path=os.getenv("HARNESS_MCP_CONFIG", cls.mcp_config_path),
            memory_dir=os.getenv("HARNESS_MEMORY_DIR", cls.memory_dir),
        )
