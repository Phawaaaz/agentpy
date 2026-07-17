"""Central configuration for the harness.

Everything the rest of the system needs to know about *how* to run lives here,
loaded once from environment variables (and a .env file if present).
"""

import os
import re
from dataclasses import dataclass, replace

# Usernames become directory path components (Config.for_user); confine them
# to a safe charset so "../other_user" or "/tmp/evil" can't escape the
# intended per-user directory (same whitelist-the-untrusted-component pattern
# as context_engine/session_store.py's session_id and observability/log.py's
# run_id).
_VALID_USERNAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

try:
    from dotenv import load_dotenv

    load_dotenv()  # pulls values from a local .env file into the environment
except ImportError:
    pass  # dotenv is optional; env vars still work without it


DEFAULT_SYSTEM_PROMPT = """You are an autonomous software-engineering and data agent, deployed by an organization to do real work on its behalf.

The files, code, and data in your workspace are real and belong to the organization running you. They were placed there deliberately for you to work on. You are authorized to read, create, modify, delete, and run them with the tools you have -- that is your job. Your tools genuinely act on this real workspace; they are not a simulation.

Act, don't editorialize. When asked to do something you have a tool for, do it -- do not respond with disclaimers about being "an AI", about "not having access to files", about "not being able to handle the organization's data", or about needing a human to do it for you. You have file, shell, and data tools; use them. If the organization's own data is in your workspace, it is in scope: handle it carefully and correctly, but do not refuse to work with it or lecture the user about privacy or security frameworks. Only refuse a request that is actually harmful or clearly outside the task, and if you do, say so in one plain sentence.

Your workspace is sandboxed for the host's protection: you cannot reach outside it to the machine hosting you, and you must not try. That boundary keeps the host safe -- it does not limit your authority to do the work *inside* the workspace, which is complete. If a path is refused for escaping the workspace, that is expected; stay inside it.

Report what a tool actually returned, not a description of it. When a tool gives you data -- an API response, a file's contents, command output -- show the actual content or the specific values you received. Do not answer with a generic summary of what such a response "usually" or "typically" contains; that is guessing, and it is wrong. If the user asks for the data or the full response, paste it. If a request comes back as something other than what was expected -- for example an HTML page or an error when you wanted JSON -- say that plainly ("that URL returned an HTML page, not JSON") instead of inventing a plausible-sounding description of what the endpoint would have returned. Never fabricate field values you did not actually see.

You complete tasks by calling the tools available to you rather than guessing.
Work in small, verifiable steps: inspect before you change, and check your work
after you change something (for example, run tests or re-read a file). When the
task is finished, stop calling tools and give a short, clear summary of what you
did. If you cannot complete the task, say so plainly and explain why.

Never assume file contents, command output, or system state -- check them with
a tool. Never claim a change works, a test passed, or something was verified
unless a tool actually showed you that; distinguish what you verified from
what you're assuming. Make the smallest change that satisfies the request:
preserve unrelated behavior, match the existing code's patterns and style, and
reuse existing utilities before adding new ones. If you notice an unrelated
problem, mention it rather than fixing it. If an approach fails twice, stop
repeating it -- explain what you tried, why it failed, and what would unblock
it, instead of trying the same thing again.

If a `memory` tool is available, check it near the start of a task for context
from earlier sessions (prior decisions, known gotchas, work already in
progress) before you start exploring from scratch, and write down anything
you learn that would help a future session -- not routine narration, only
what would actually save re-discovery.
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
    # Optional second model to retry a failed call on (same credentials, so
    # use a sibling model or a key-less local one). Unset = no fallback.
    fallback_model: str | None = None
    permission_mode: str = "ask"  # ask | allowlist | auto
    max_steps: int = 25
    # None = use the active model's known limit (providers/model_info.py),
    # falling back to the historical 4096 default for unknown models.
    max_tokens: int | None = None
    temperature: float = 0.0
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    # Context management: fold old history into a summary past this token
    # budget. None = derive from the active model's known context window
    # (providers/model_info.py), else the historical 100k default.
    max_context_tokens: int | None = None
    keep_recent_messages: int = 20
    # Persistence / observability locations.
    sessions_dir: str = ".harness/sessions"
    logs_dir: str = ".harness/logs"
    # MCP servers to connect to at startup (Claude-Desktop-style config file).
    mcp_config_path: str = ".harness/mcp.json"
    # Where the memory tool and the automatic activity tracker write notes.
    memory_dir: str = ".harness/memory"
    # Sub-agent roles the coordinator can delegate to (empty file = no delegate tool).
    roles_config_path: str = ".harness/roles.json"
    # User-defined skills (on-demand prompt templates), merged with the built-in ones.
    skills_config_path: str = ".harness/skills.json"
    # Where oversized tool output gets written instead of silently truncated.
    offload_dir: str = ".harness/offload"
    # Relational store for users, sessions, and usage accounting (D29).
    # SQLite file by default; any SQLAlchemy URL works (e.g.
    # postgresql+psycopg://... for Postgres) with no code change.
    db_url: str = "sqlite:///.harness/harness.db"
    # Legacy JSON account file -- read only by scripts/migrate_json_to_db.py.
    users_config_path: str = ".harness/users.json"
    # JWT scaffolding (D30): where the auto-generated signing secret lives
    # when HARNESS_JWT_SECRET isn't set, and the token lifetime.
    jwt_secret_path: str = ".harness/jwt_secret"
    jwt_ttl_s: int = 7 * 24 * 3600
    # Tavily API key for the web_search tool; unset = DuckDuckGo fallback (D25).
    search_api_key: str | None = None
    # Workspace confinement (D27): when True, filesystem/shell tools are
    # confined to workspaces/{user}/{session}/ and cannot reach outside it.
    # Default False = the historical unconfined single-user CLI behavior.
    confine_workspace: bool = False
    # Root under which per-user, per-session workspaces are created.
    workspace_dir: str = "workspaces"
    # Sandbox (D33): "off" = run_command runs on the host (default); "docker"
    # = run each session's commands inside a resource-limited, network-denied
    # container mounting only that session's workspace. "docker" implies
    # workspace confinement (the container has nothing else to mount).
    sandbox: str = "off"
    sandbox_image: str = "python:3.11-slim"
    sandbox_memory: str = "2g"
    sandbox_cpus: str = "2"
    sandbox_pids: int = 256
    sandbox_network: str = "none"  # "none" = default-deny egress; "bridge" allows

    def for_user(self, username: str) -> "Config":
        """A copy of this Config with per-user data directories namespaced by
        username, so concurrent users never see each other's sessions,
        memory, logs, or offloaded output. Org-wide config (model, MCP
        servers, roles, skills) is untouched -- those aren't a user's data.

        Rejects a username that isn't a safe directory-name component --
        without this, "../alice" or "/tmp/evil" would let one user's
        directories collide with another's or escape .harness/ entirely."""
        if not _VALID_USERNAME.match(username):
            raise ValueError(
                f"invalid username {username!r}: must be 1-64 characters of "
                "letters, digits, underscore, or hyphen"
            )
        return replace(
            self,
            sessions_dir=os.path.join(self.sessions_dir, username),
            memory_dir=os.path.join(self.memory_dir, username),
            logs_dir=os.path.join(self.logs_dir, username),
            offload_dir=os.path.join(self.offload_dir, username),
            workspace_dir=os.path.join(self.workspace_dir, username),
        )

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

        def to_bool(value) -> bool:
            # bool("false") is True, so string values need real parsing.
            if isinstance(value, str):
                return value.strip().lower() in ("true", "yes", "1")
            return bool(value)

        def get_val(env_name: str, yaml_key: str, default, type_conv=None):
            env_val = os.getenv(env_name)
            # A blank env var (e.g. a `HARNESS_MAX_TOKENS=` line in a copied
            # .env) means "unset", not "the empty string" -- otherwise
            # type_conv=int would choke on int('') and crash startup.
            if env_val is not None and env_val.strip() != "":
                return type_conv(env_val) if type_conv else env_val
            yaml_val = yaml_data.get(yaml_key)
            if yaml_val is not None:
                return type_conv(yaml_val) if type_conv else yaml_val
            return default

        return cls(
            model=get_val("HARNESS_MODEL", "model", cls.model),
            system_prompt=get_val("HARNESS_SYSTEM_PROMPT", "system_prompt", cls.system_prompt),
            api_key=get_val("HARNESS_API_KEY", "api_key", cls.api_key) or None,
            base_url=get_val("HARNESS_BASE_URL", "base_url", cls.base_url) or None,
            fallback_model=get_val("HARNESS_FALLBACK_MODEL", "fallback_model", cls.fallback_model) or None,
            permission_mode=get_val("HARNESS_PERMISSION_MODE", "permission_mode", cls.permission_mode),
            max_steps=get_val("HARNESS_MAX_STEPS", "max_steps", cls.max_steps, int),
            max_tokens=get_val("HARNESS_MAX_TOKENS", "max_tokens", cls.max_tokens, int),
            max_context_tokens=get_val("HARNESS_MAX_CONTEXT_TOKENS", "max_context_tokens", cls.max_context_tokens, int),
            keep_recent_messages=get_val("HARNESS_KEEP_RECENT_MESSAGES", "keep_recent_messages", cls.keep_recent_messages, int),
            sessions_dir=get_val("HARNESS_SESSIONS_DIR", "sessions_dir", cls.sessions_dir),
            logs_dir=get_val("HARNESS_LOGS_DIR", "logs_dir", cls.logs_dir),
            mcp_config_path=get_val("HARNESS_MCP_CONFIG", "mcp_config_path", cls.mcp_config_path),
            memory_dir=get_val("HARNESS_MEMORY_DIR", "memory_dir", cls.memory_dir),
            roles_config_path=get_val("HARNESS_ROLES_CONFIG", "roles_config_path", cls.roles_config_path),
            skills_config_path=get_val("HARNESS_SKILLS_CONFIG", "skills_config_path", cls.skills_config_path),
            offload_dir=get_val("HARNESS_OFFLOAD_DIR", "offload_dir", cls.offload_dir),
            db_url=get_val("HARNESS_DB_URL", "db_url", cls.db_url),
            users_config_path=get_val("HARNESS_USERS_FILE", "users_config_path", cls.users_config_path),
            jwt_secret_path=get_val("HARNESS_JWT_SECRET_PATH", "jwt_secret_path", cls.jwt_secret_path),
            jwt_ttl_s=get_val("HARNESS_JWT_TTL", "jwt_ttl_s", cls.jwt_ttl_s, int),
            search_api_key=get_val("HARNESS_SEARCH_API_KEY", "search_api_key", cls.search_api_key) or None,
            confine_workspace=get_val("HARNESS_CONFINE_WORKSPACE", "confine_workspace", cls.confine_workspace, to_bool),
            workspace_dir=get_val("HARNESS_WORKSPACE_DIR", "workspace_dir", cls.workspace_dir),
            sandbox=get_val("HARNESS_SANDBOX", "sandbox", cls.sandbox),
            sandbox_image=get_val("HARNESS_SANDBOX_IMAGE", "sandbox_image", cls.sandbox_image),
            sandbox_memory=get_val("HARNESS_SANDBOX_MEMORY", "sandbox_memory", cls.sandbox_memory),
            sandbox_cpus=get_val("HARNESS_SANDBOX_CPUS", "sandbox_cpus", cls.sandbox_cpus),
            sandbox_pids=get_val("HARNESS_SANDBOX_PIDS", "sandbox_pids", cls.sandbox_pids, int),
            sandbox_network=get_val("HARNESS_SANDBOX_NETWORK", "sandbox_network", cls.sandbox_network),
        )
