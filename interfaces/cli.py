"""Command-line interface — the first way to reach the agent.

Deliberately thin: it captures input, shows what the agent is doing, handles
approval prompts, persists sessions, reports cost, and prints the final answer.
All agent logic lives in core/. Slash commands (/new, /save, ...) manage the
session; anything else is a task for the agent.
"""

from datetime import datetime

from config import Config
from core.context import Conversation, make_provider_summarizer
from core.orchestrator import Orchestrator
from observability.log import EventLogger
from observability.memory_tracker import MemoryTracker
from observability.usage import UsageTracker
from providers.base import ToolCall
from providers.factory import build_provider
from store.session_store import SessionStore
from tools.mcp_client import MCPManager, load_server_configs
from tools.registry import Tool, registry

# Importing these modules registers their tools onto the shared registry.
import tools.filesystem  # noqa: F401
import tools.memory  # noqa: F401
import tools.shell  # noqa: F401
import tools.web  # noqa: F401

HELP = """commands:
  /new                 start a fresh conversation
  /save [id]           save the current session (default id = current)
  /load <id>           load a saved session
  /sessions            list saved sessions
  /cost                show token usage and estimated cost
  /memory              show what the harness has been working on
  /mcp                 list connected MCP servers and their tools
  /mcp connect <name>  connect a server from the MCP config file
  /mcp disconnect <n>  disconnect a server and remove its tools
  /help                show this help
  quit                 exit
anything else is sent to the agent as a task."""


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

    def __init__(self, config: Config, provider, on_event):
        self.config = config
        self.provider = provider
        self.on_event = on_event
        self.usage = UsageTracker()
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.conversation = self._new_conversation()
        self.agent = self._new_agent()

    def _new_conversation(self) -> Conversation:
        return Conversation(
            self.config.system_prompt,
            max_context_tokens=self.config.max_context_tokens,
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

    def rebuild_agent(self) -> None:
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


def _handle_command(
    text: str,
    session: Session,
    store: SessionStore,
    mcp_manager: MCPManager,
    memory_tracker: MemoryTracker,
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
    elif cmd == "/mcp":
        _handle_mcp_command(args, mcp_manager, session.config)
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


def main() -> None:
    config = Config.load()
    provider = build_provider(config)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Each listener is independent: remove the import + these two lines and
    # nothing else in this file (or in either listener) needs to change.
    logger = EventLogger(config.logs_dir, run_id)
    memory_tracker = MemoryTracker(config.memory_dir, run_id)
    tools.memory.set_memory_root(config.memory_dir)

    session = Session(config, provider, _make_event_handler(logger, memory_tracker))
    store = SessionStore(config.sessions_dir)
    mcp_manager = MCPManager(registry)

    print("=" * 60)
    print("  Agentic Harness  —  Phase 2 (CLI)")
    print(f"  model: {config.model}")
    print(f"  permission mode: {config.permission_mode}")
    print(f"  tools: {', '.join(t.name for t in registry.all())}")
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
            if _handle_command(user_input, session, store, mcp_manager, memory_tracker):
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
