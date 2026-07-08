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
from observability.usage import UsageTracker
from providers.base import ToolCall
from providers.factory import build_provider
from store.session_store import SessionStore
from tools.registry import Tool, registry

# Importing these modules registers their tools onto the shared registry.
import tools.filesystem  # noqa: F401
import tools.shell  # noqa: F401
import tools.web  # noqa: F401

HELP = """commands:
  /new           start a fresh conversation
  /save [id]     save the current session (default id = current)
  /load <id>     load a saved session
  /sessions      list saved sessions
  /cost          show token usage and estimated cost
  /help          show this help
  quit           exit
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


def _make_event_handler(logger: EventLogger):
    def on_event(kind: str, *details) -> None:
        logger.log(kind, details=list(details))  # trace everything to file
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


def _handle_command(text: str, session: Session, store: SessionStore) -> bool:
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
    else:
        print(f"unknown command: {cmd} (try /help)")
    return True


def main() -> None:
    config = Config.load()
    provider = build_provider(config)
    logger = EventLogger(config.logs_dir, datetime.now().strftime("%Y%m%d-%H%M%S"))
    session = Session(config, provider, _make_event_handler(logger))
    store = SessionStore(config.sessions_dir)

    print("=" * 60)
    print("  Agentic Harness  —  Phase 2 (CLI)")
    print(f"  model: {config.model}")
    print(f"  permission mode: {config.permission_mode}")
    print(f"  tools: {', '.join(t.name for t in registry.all())}")
    print(f"  session: {session.id}   (/help for commands)")
    print("=" * 60)

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
        if _handle_command(user_input, session, store):
            continue

        try:
            answer = session.agent.run(user_input)
        except Exception as exc:
            print(f"\n✗ error: {exc}")
            continue

        print(f"\nagent > {answer}")
        print(f"        [{session.usage.summary()}]")
        store.save(session.id, session.conversation)  # auto-save after each turn


if __name__ == "__main__":
    main()
