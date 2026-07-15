"""Command-line interface — the first way to reach the agent.

Deliberately thin: it captures input, shows what the agent is doing, handles
approval prompts, persists sessions, reports cost, and prints the final answer.
All agent logic lives in engine/. Slash commands (/new, /save, ...) manage the
session; anything else is a task for the agent.
"""

import getpass
import os
from dataclasses import replace
from datetime import datetime

from auth.users import UserStore
from config import Config
from context_engine.compaction import Conversation, make_provider_summarizer
from context_engine.memory_tracker import MemoryTracker
from context_engine.session_store import SessionStore
from engine.mcp_client import MCPManager, load_server_configs
from engine.orchestrator import Orchestrator
from engine.registry import Tool, registry
from multiagent.coordinator import build_delegate_tool
from multiagent.roles import load_roles
from observability.log import EventLogger
from observability.usage import UsageTracker
from pipeline import stages, worktree
from pipeline.external_skills import load_external_skills
from providers.base import ToolCall
from providers.factory import OPENAI_COMPATIBLE, build_provider
from providers.model_info import effective_context_budget

# Importing these modules registers their tools onto the shared registry.
import context_engine.memory_tool  # noqa: F401
import engine.builtin.filesystem  # noqa: F401
import engine.builtin.git_tool  # noqa: F401
import engine.builtin.github_tool  # noqa: F401
import engine.builtin.offload
import engine.builtin.planning
import engine.builtin.shell  # noqa: F401
import engine.builtin.web  # noqa: F401
from engine.builtin.search import build_search_tool

HELP = """commands:
  /new                 start a fresh conversation
  /save [id]           save the current session (default id = current)
  /load <id>           load a saved session
  /sessions            list saved sessions
  /cost                show token usage and estimated cost
  /memory              show what the harness has been working on
  /model               show the current model
  /model <name>        switch model mid-session (conversation history kept)
  /whoami              show the logged-in user
  /mcp                 list connected MCP servers and their tools
  /mcp connect <name>  connect a server from the MCP config file
  /mcp disconnect <n>  disconnect a server and remove its tools
  /review [task]       run the self-review skill (default task: current memory task)
  /verify [task]       run the verify skill
  /test [task]         run the test skill
  /docs [task]         run the sync-docs skill
  /roles               list configured sub-agent roles (delegate target)
  /help                show this help
  quit                 exit
anything else is sent to the agent as a task.

custom skills from .harness/skills.json (see skills.json.example) show up as
commands too, alongside the four built-ins above."""

# Named, on-demand instructions reusing pipeline/stages.py's prompt builders
# -- the same "skill" idea Ralph implements via Claude Code Skills, adapted
# to agentpy's own tool-calling loop instead of a second harness underneath.
# Every value here is a plain (task: str, diff_stat: str) -> str callable;
# main() merges in .harness/skills.json's user-defined ones the same shape.
_SKILLS = {
    "review": stages.self_review_prompt,
    "verify": stages.verify_prompt,
    "test": stages.test_prompt,
    "docs": stages.sync_docs_prompt,
}


def _format_args(arguments: dict) -> str:
    parts = []
    for key, value in arguments.items():
        text = str(value).replace("\n", " ")
        if len(text) > 60:
            text = text[:60] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _make_approver():
    def approve(tool_call: ToolCall, tool: Tool) -> bool:
        print(f"\n  ⚠  Approve  {tool_call.name}({_format_args(tool_call.arguments)})")
        print(f"     risk: {tool.risk}")
        answer = input("     Allow this action? [y/N] ").strip().lower()
        return answer in ("y", "yes")

    return approve


def _make_event_handler(*listeners):
    """Fan `on_event` out to any number of independent listeners plus the
    CLI's own printing. Each listener is a plain object with a
    `log(kind, *details)` method (EventLogger, MemoryTracker, ...) -- adding
    or removing one is a one-line change here, not a signature change, and
    none of them depend on each other or on this function existing."""

    def on_event(kind: str, *details) -> None:
        for listener in listeners:
            listener.log(kind, *details)
        if kind == "thinking":
            print(f"\n\U0001f9e0 {details[0]}")
        elif kind == "tool_call":
            name, arguments = details
            print(f"\n\U0001f527 {name}({_format_args(arguments)})")
        elif kind == "tool_result":
            _name, result = details
            preview = result if len(result) <= 300 else result[:300] + " ..."
            indented = "\n".join("     " + line for line in preview.splitlines())
            print(indented or "     (no output)")
        elif kind == "denied":
            print(f"     ✗ denied: {details[0]}")
        elif kind == "compacted":
            print(f"\n\U0001f5dc  compacted history (~{details[0]} tokens now)")
        # "usage" events are logged but not printed each turn (see /cost).

    return on_event


class Session:
    """Bundles the mutable per-session objects the CLI juggles."""

    def __init__(self, config: Config, provider, on_event, username: str = ""):
        self.config = config
        self.provider = provider
        self.on_event = on_event
        self.username = username
        self.usage = UsageTracker()
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.conversation = self._new_conversation()
        self.agent = self._new_agent()

    def _new_conversation(self) -> Conversation:
        return Conversation(
            self.config.system_prompt,
            max_context_tokens=effective_context_budget(
                self.config.model, self.config.max_context_tokens
            ),
            keep_recent_messages=self.config.keep_recent_messages,
            summarizer=make_provider_summarizer(self.provider),
        )

    def _new_agent(self) -> Orchestrator:
        return Orchestrator(
            self.provider,
            registry,
            self.config,
            approver=_make_approver(),
            on_event=self.on_event,
            conversation=self.conversation,
            usage_tracker=self.usage,
        )

    def reset(self) -> None:
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.conversation = self._new_conversation()
        self.agent = self._new_agent()
        engine.builtin.planning.reset_plan()

    def rebuild_agent(self) -> None:
        self.agent = self._new_agent()

    def switch_model(self, model: str) -> None:
        """Swap the active model without losing conversation history.

        Builds a fresh provider (and orchestrator) from a Config that differs
        only in `model`; the existing Conversation object -- messages and
        summary -- is reused as-is, just re-pointed at the new provider's
        summarizer.
        """
        new_config = replace(self.config, model=model)
        new_provider = build_provider(new_config)  # raises before anything is mutated
        self.config = new_config
        self.provider = new_provider
        self.conversation.summarizer = make_provider_summarizer(self.provider)
        self.agent = self._new_agent()


def _handle_mcp_command(args: list[str], mcp_manager: MCPManager, config: Config) -> None:
    if not args:
        connected = mcp_manager.list_connected()
        if not connected:
            print("(no MCP servers connected)")
            return
        for name, tool_names in connected.items():
            print(f"  {name}: {', '.join(tool_names) or '(no tools)'}")
        return

    sub, rest = args[0], args[1:]
    if sub == "connect":
        if not rest:
            print("usage: /mcp connect <name>")
            return
        configs = {c.name: c for c in load_server_configs(config.mcp_config_path)}
        server = configs.get(rest[0])
        if server is None:
            print(f"no such server '{rest[0]}' in {config.mcp_config_path}")
            return
        try:
            names = mcp_manager.connect(server)
        except Exception as exc:
            print(f"failed to connect '{rest[0]}': {exc}")
            return
        print(f"connected '{rest[0]}' -> {', '.join(names) or '(no tools)'}")
    elif sub == "disconnect":
        if not rest:
            print("usage: /mcp disconnect <name>")
            return
        print(f"disconnected '{rest[0]}'" if mcp_manager.disconnect(rest[0]) else f"'{rest[0]}' was not connected")
    else:
        print("usage: /mcp | /mcp connect <name> | /mcp disconnect <name>")


def _handle_model_command(args: list[str], session: Session) -> None:
    if not args:
        print(f"current model: {session.config.model}")
        print(f"known prefixes: {', '.join(['anthropic', *OPENAI_COMPATIBLE])} "
              "(or set HARNESS_BASE_URL for a custom OpenAI-compatible endpoint)")
        return
    new_model = args[0]
    try:
        session.switch_model(new_model)
    except Exception as exc:
        print(f"failed to switch model: {exc}")
        return
    print(f"switched model -> {new_model}  (conversation history kept)")


def _handle_roles_command(roles: dict) -> None:
    if not roles:
        print("(no sub-agent roles configured -- add some to .harness/roles.json to enable /delegate)")
        return
    for name, role in sorted(roles.items()):
        print(f"  {name}: {role.description}")


def _handle_skill_command(
    stage: str,
    args: list[str],
    session: Session,
    store: SessionStore,
    memory_tracker: MemoryTracker,
    skills: dict,
) -> None:
    task = " ".join(args) if args else memory_tracker.task
    if not task:
        print(f"usage: /{stage} [task]  (no current task in memory -- give one, or run a task first)")
        return
    try:
        diff = worktree.diff_stat(".")
    except worktree.WorktreeError:
        diff = "(not a git repository)"

    prompt = skills[stage](task, diff)
    print(f"\n[running /{stage}]")
    try:
        answer = session.agent.run(prompt)
    except Exception as exc:
        print(f"\n✗ error: {exc}")
        return
    print(f"\nagent > {answer}")
    print(f"        [{session.usage.summary()}]")
    store.save(session.id, session.conversation)


def _handle_command(
    text: str,
    session: Session,
    store: SessionStore,
    mcp_manager: MCPManager,
    memory_tracker: MemoryTracker,
    roles: dict,
    skills: dict,
) -> bool:
    """Handle a /command. Returns True if input was a command (handled)."""
    if not text.startswith("/"):
        return False
    parts = text.split()
    cmd, args = parts[0], parts[1:]

    if cmd == "/help":
        print(HELP)
    elif cmd == "/new":
        session.reset()
        print(f"started new session: {session.id}")
    elif cmd == "/save":
        if args:
            session.id = args[0]
        path = store.save(session.id, session.conversation)
        print(f"saved -> {path}")
    elif cmd == "/load":
        if not args:
            print("usage: /load <id>")
        elif store.load(args[0], session.conversation):
            session.id = args[0]
            session.rebuild_agent()
            engine.builtin.planning.reset_plan()
            print(f"loaded session: {args[0]}")
        else:
            print(f"no such session: {args[0]}")
    elif cmd == "/sessions":
        ids = store.list_ids()
        print("\n".join(ids) if ids else "(no saved sessions)")
    elif cmd == "/cost":
        print(session.usage.summary())
    elif cmd == "/memory":
        print(memory_tracker.summary())
    elif cmd == "/model":
        _handle_model_command(args, session)
    elif cmd == "/whoami":
        print(session.username or "(not logged in)")
    elif cmd == "/mcp":
        _handle_mcp_command(args, mcp_manager, session.config)
    elif cmd == "/roles":
        _handle_roles_command(roles)
    elif cmd.lstrip("/") in skills:
        _handle_skill_command(cmd.lstrip("/"), args, session, store, memory_tracker, skills)
    else:
        print(f"unknown command: {cmd} (try /help)")
    return True


def _connect_configured_mcp_servers(mcp_manager: MCPManager, config: Config) -> None:
    for server in load_server_configs(config.mcp_config_path):
        try:
            names = mcp_manager.connect(server)
            print(f"  mcp '{server.name}': {', '.join(names) or '(no tools)'}")
        except Exception as exc:
            # A misbehaving/unreachable MCP server shouldn't stop the CLI
            # from starting; report it and move on (PRINCIPLES: fail safe
            # for runtime concerns, not setup — the server is external,
            # not our config).
            print(f"  mcp '{server.name}': failed to connect ({exc})")


def _login(users_config_path: str, max_attempts: int = 3) -> str:
    """Authenticate a user against `users_config_path`, registering a new
    account on first sight of a username. Returns the logged-in username.

    `HARNESS_USER`/`HARNESS_PASSWORD` skip the interactive prompt (for
    scripted/demo use, same "env var first" convention as the rest of
    Config.load()); with only `HARNESS_USER` set, the username is
    pre-filled but the password is still prompted for."""
    store = UserStore(users_config_path)
    env_user = os.getenv("HARNESS_USER")
    env_password = os.getenv("HARNESS_PASSWORD")
    if env_user and env_password:
        if store.exists(env_user):
            if not store.verify(env_user, env_password):
                raise SystemExit(f"login failed: wrong HARNESS_PASSWORD for '{env_user}'")
        else:
            store.register(env_user, env_password)
            print(f"  created new account '{env_user}'")
        return env_user

    print("=" * 60)
    print("  Agentic Harness  —  sign in")
    print("=" * 60)
    for attempt in range(max_attempts):
        username = (env_user or input("username: ")).strip()
        if not username:
            continue
        if store.exists(username):
            password = getpass.getpass("password: ")
            if store.verify(username, password):
                return username
            print("  wrong password, try again")
        else:
            print(f"  no such user '{username}' -- creating a new account")
            password = getpass.getpass("choose a password: ")
            confirm = getpass.getpass("confirm password: ")
            if password != confirm:
                print("  passwords did not match, try again")
                continue
            try:
                store.register(username, password)
            except ValueError as exc:
                print(f"  {exc}")
                continue
            return username
    raise SystemExit("too many failed login attempts")


def main() -> None:
    config = Config.load()
    username = _login(config.users_config_path)
    config = config.for_user(username)
    provider = build_provider(config)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Each listener is independent: remove the import + these two lines and
    # nothing else in this file (or in either listener) needs to change.
    logger = EventLogger(config.logs_dir, run_id)
    memory_tracker = MemoryTracker(config.memory_dir, run_id)
    context_engine.memory_tool.set_memory_root(config.memory_dir)
    engine.builtin.offload.set_offload_root(config.offload_dir)
    on_event = _make_event_handler(logger, memory_tracker)

    # Skills are opt-in too: no .harness/skills.json means just the four
    # built-ins. A name collision with a built-in still works (external
    # wins) but is surfaced rather than silently shadowing it.
    skills = dict(_SKILLS)
    for name, skill in load_external_skills(config.skills_config_path).items():
        if name in skills:
            print(f"  skills: '{name}' from {config.skills_config_path} overrides the built-in skill of the same name")
        skills[name] = skill.build

    # Multi-agent is opt-in: no roles configured means no `delegate` tool.
    # Removing this block (and .harness/roles.json) turns it back off with
    # no other change anywhere in the harness.
    roles = load_roles(config.roles_config_path)
    if roles:
        registry.register(
            build_delegate_tool(
                provider, registry, config, roles, approver=_make_approver(), on_event=on_event
            )
        )

    # Web search is always available: Tavily when HARNESS_SEARCH_API_KEY is
    # set, a key-less DuckDuckGo fallback otherwise (one tool, two backends
    # -- D25).
    registry.register(build_search_tool(config.search_api_key))

    session = Session(config, provider, on_event, username=username)
    store = SessionStore(config.sessions_dir)
    mcp_manager = MCPManager(registry)

    print("=" * 60)
    print("  Agentic Harness  —  Phase 2 (CLI)")
    print(f"  user: {username}")
    print(f"  model: {config.model}")
    print(f"  permission mode: {config.permission_mode}")
    print(f"  tools: {', '.join(t.name for t in registry.all())}")
    print(f"  skills: {', '.join(sorted(skills))}")
    if roles:
        print(f"  roles: {', '.join(sorted(roles))}")
    _connect_configured_mcp_servers(mcp_manager, config)
    print(f"  session: {session.id}   (/help for commands)")
    print("=" * 60)

    try:
        while True:
            try:
                user_input = input("\nyou > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nbye")
                return

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print("bye")
                return
            if _handle_command(user_input, session, store, mcp_manager, memory_tracker, roles, skills):
                continue

            memory_tracker.set_task(user_input)
            try:
                answer = session.agent.run(user_input)
            except Exception as exc:
                print(f"\n✗ error: {exc}")
                continue

            print(f"\nagent > {answer}")
            print(f"        [{session.usage.summary()}]")
            store.save(session.id, session.conversation)  # auto-save after each turn
    finally:
        mcp_manager.disconnect_all()


if __name__ == "__main__":
    main()
