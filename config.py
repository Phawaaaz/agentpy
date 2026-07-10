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


def build_system_prompt(
    base: str = DEFAULT_SYSTEM_PROMPT,
    agents_path: str = "AGENTS.md",
    inject_project_context: bool = True,
) -> str:
    """Append AGENTS.md (or similar) to the system prompt when present."""
    if not inject_project_context or not agents_path:
        return base
    if not os.path.isfile(agents_path):
        return base
    try:
        with open(agents_path, encoding="utf-8") as f:
            agents = f.read().strip()
    except OSError:
        return base
    if not agents:
        return base
    return (
        f"{base}\n\n---\n\n"
        f"# Project context (from {agents_path})\n\n"
        f"{agents}"
    )


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

    @classmethod
    def load(cls) -> "Config":
        """Build a Config from environment variables, falling back to defaults."""
        yaml_data = {}
        for filename in (".harness.yaml", ".harness.yml"):
            if os.path.exists(filename):
                try:
                    import yaml
                    with open(filename, "r", encoding="utf-8") as f:
                        parsed = yaml.safe_load(f) or {}
                        if isinstance(parsed, dict):
                            yaml_data = parsed.get("harness", {}) or {}
                        break
                except Exception:
                    pass

        def get_val(env_name: str, yaml_key: str, default, type_conv=None):
            env_val = os.getenv(env_name)
            if env_val is not None:
                return type_conv(env_val) if type_conv else env_val
            yaml_val = yaml_data.get(yaml_key)
            if yaml_val is not None:
                return type_conv(yaml_val) if type_conv else yaml_val
            return default

        return cls(
            model=get_val("HARNESS_MODEL", "model", cls.model),
            api_key=get_val("HARNESS_API_KEY", "api_key", cls.api_key),
            base_url=get_val("HARNESS_BASE_URL", "base_url", cls.base_url),
            permission_mode=get_val("HARNESS_PERMISSION_MODE", "permission_mode", cls.permission_mode),
            max_steps=get_val("HARNESS_MAX_STEPS", "max_steps", cls.max_steps, int),
            max_tokens=get_val("HARNESS_MAX_TOKENS", "max_tokens", cls.max_tokens, int),
            max_context_tokens=get_val("HARNESS_MAX_CONTEXT_TOKENS", "max_context_tokens", cls.max_context_tokens, int),
            keep_recent_messages=get_val("HARNESS_KEEP_RECENT_MESSAGES", "keep_recent_messages", cls.keep_recent_messages, int),
            sessions_dir=get_val("HARNESS_SESSIONS_DIR", "sessions_dir", cls.sessions_dir),
            logs_dir=get_val("HARNESS_LOGS_DIR", "logs_dir", cls.logs_dir),
            mcp_config_path=get_val("HARNESS_MCP_CONFIG", "mcp_config_path", cls.mcp_config_path),
        )
