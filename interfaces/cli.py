"""Command-line interface — the first way to reach the agent.

Deliberately thin: it captures input, shows what the agent is doing, handles
approval prompts, persists sessions, reports cost, and prints the final answer.
All agent logic lives in engine/. Slash commands (/new, /save, ...) manage the
session; anything else is a task for the agent.
"""

import getpass
import os
import shutil
from dataclasses import replace
from datetime import datetime

from auth.tokens import issue_token, load_or_create_secret, verify_token
from config import Config
from context_engine.compaction import Conversation, make_provider_summarizer
from context_engine.memory_tracker import MemoryTracker
from engine.mcp_client import MCPManager, load_server_configs
from engine.orchestrator import Orchestrator
from engine.registry import Tool, registry
from multiagent.coordinator import build_delegate_tool
from multiagent.roles import load_roles
from observability.log import EventLogger
from observability.usage import UsageTracker
from observability.usage_store import PersistentUsageTracker, usage_by_user, usage_for_user
from pipeline import stages, worktree
from pipeline.external_skills import load_external_skills
from providers.base import ToolCall
from providers.factory import OPENAI_COMPATIBLE, build_provider
from providers.model_info import effective_context_budget
from storage.db import make_engine
from storage.models import ROLE_ADMIN
from storage.session_store import DbSessionStore
from storage.user_store import DbUserStore

# Importing these modules registers their tools onto the shared registry.
import context_engine.memory_tool  # noqa: F401
import engine.builtin.filesystem  # noqa: F401
import engine.builtin.git_tool  # noqa: F401
import engine.builtin.github_tool  # noqa: F401
import engine.builtin.offload
import engine.builtin.planning
import engine.builtin.search_files  # noqa: F401
import engine.builtin.shell  # noqa: F401
import engine.builtin.web  # noqa: F401
import engine.sandbox
import engine.workspace
from engine.builtin.search import build_search_tool
from engine.sandbox import SandboxConfig

HELP = """commands:
  /new                 start a fresh conversation
  /save [id]           save the current session (default id = current)
  /load <id>           load a saved session
  /delete <id>         delete a saved session
  /sessions            list saved sessions
  /cost                show token usage and estimated cost
  /memory              show what the harness has been working on
  /quiet               toggle hiding tool output (e.g. file listings)
  /model               show the current model
  /model <name>        switch model mid-session (conversation history kept)
  /whoami              show the logged-in user and role
  /usage [username]    admin only: token/cost usage per user (or one user's sessions)
  /users               admin only: list accounts and roles
  /users role <u> <r>  admin only: promote/demote an account (admin|user)
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


def _make_event_handler(*listeners, quiet=None):
    """Fan `on_event` out to any number of independent listeners plus the
    CLI's own printing. Each listener is a plain object with a
    `log(kind, *details)` method (EventLogger, MemoryTracker, ...) -- adding
    or removing one is a one-line change here, not a signature change, and
    none of them depend on each other or on this function existing.

    `quiet` is an optional mutable {"on": bool} -- when on, tool-result output
    (e.g. long file listings) is hidden; errors still show. Toggled live by
    /quiet."""

    def on_event(kind: str, *details) -> None:
        for listener in listeners:
            listener.log(kind, *details)
        if kind == "thinking":
            print(f"\n\U0001f9e0 {details[0]}")
        elif kind == "tool_call":
            name, arguments = details
            print(f"\n\U0001f527 {name}({_format_args(arguments)})")
        elif kind == "tool_result":
            _name, result, *_timing = details  # trailing duration_ms (I1)
            # Quiet mode hides tool output (still show errors so failures and
            # the workspace block stay visible).
            if quiet and quiet.get("on") and not result.startswith("Error"):
                return
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

    def __init__(
        self,
        config: Config,
        provider,
        on_event,
        username: str = "",
        role: str = "",
        usage_tracker: UsageTracker | None = None,
    ):
        self.config = config
        self.provider = provider
        self.on_event = on_event
        self.username = username
        self.role = role
        self.usage = usage_tracker or UsageTracker()
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.conversation = self._new_conversation()
        self.agent = self._new_agent()
        self.apply_workspace_root()

    def apply_workspace_root(self) -> None:
        """Point tool confinement at this session's own workspace directory
        (workspaces/{user}/{session}/) when confinement is on (D27); a no-op
        root (None) otherwise. Re-called whenever the session id changes."""
        if self.config.confine_workspace:
            engine.workspace.set_workspace_root(
                os.path.join(self.config.workspace_dir, self.id)
            )
        else:
            engine.workspace.set_workspace_root(None)

    def _new_conversation(self) -> Conversation:
        # Memory injection (D31): what earlier sessions remembered arrives
        # in the system prompt at session start, capped so it can't blow
        # the context budget -- instead of relying on the model choosing
        # to call the memory tool before it acts.
        system_prompt = self.config.system_prompt
        overview = context_engine.memory_tool.memory_overview(self.config.memory_dir)
        if overview:
            system_prompt += (
                "\n\n## Your memory (notes from earlier sessions)\n"
                f"{overview}\n"
                "(Read more or update it with the memory tool.)"
            )
        return Conversation(
            system_prompt,
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
        self.apply_workspace_root()

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
    store: DbSessionStore,
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


def _handle_usage_command(args: list[str], session: Session, db_engine) -> None:
    """Admin-only: /usage (all users) or /usage <username> (drill-down).
    These queries are exactly what a server's admin endpoints would serve."""
    if session.role != ROLE_ADMIN:
        print("only an admin can view usage across users (your own session: /cost)")
        return
    if not args:
        rows = usage_by_user(db_engine)
        if not rows:
            print("(no usage recorded yet)")
            return
        for r in rows:
            total = r["prompt_tokens"] + r["completion_tokens"]
            print(
                f"  {r['username']}: {r['calls']} calls | "
                f"{r['prompt_tokens']} in + {r['completion_tokens']} out = {total} tokens | "
                f"est. ${r['cost_usd']:.4f}"
            )
        return
    rows = usage_for_user(db_engine, args[0])
    if not rows:
        print(f"(no usage recorded for '{args[0]}')")
        return
    for r in rows:
        total = r["prompt_tokens"] + r["completion_tokens"]
        print(
            f"  session {r['session_id']}: {r['calls']} calls | {total} tokens | "
            f"est. ${r['cost_usd']:.4f}\n"
            f"    last task: {r['last_task']}"
        )


def _handle_users_command(args: list[str], session: Session, user_store: DbUserStore) -> None:
    """Admin-only: /users (list accounts + roles) or /users role <name> <role>."""
    if session.role != ROLE_ADMIN:
        print("only an admin can manage users")
        return
    if not args:
        for username, role in user_store.list_users():
            print(f"  {username}: {role}")
        return
    if args[0] == "role" and len(args) == 3:
        try:
            user_store.set_role(args[1], args[2])
        except ValueError as exc:
            print(f"  {exc}")
            return
        print(f"  {args[1]} is now {args[2]}")
        return
    print("usage: /users | /users role <name> <admin|user>")


def _delete_session_workspace(config: Config, session_id: str) -> None:
    """Remove a deleted session's on-disk workspace directory so /delete
    actually purges the session's files, not just its conversation row.
    (Memory is per-user, not per-session, so it is intentionally left; the
    content-hashed offload dir is shared and not per-session either.) The
    session_id is sanitized the same way the workspace path is built."""
    safe = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
    if not safe:
        return
    path = os.path.join(config.workspace_dir, safe)
    # Confine the delete to under workspace_dir -- never let a crafted id
    # escape it (defence in depth; safe already strips separators).
    root = os.path.abspath(config.workspace_dir)
    target = os.path.abspath(path)
    if target != root and target.startswith(root + os.sep) and os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)


def _handle_command(
    text: str,
    session: Session,
    store: DbSessionStore,
    mcp_manager: MCPManager,
    memory_tracker: MemoryTracker,
    roles: dict,
    skills: dict,
    user_store: DbUserStore | None = None,
    db_engine=None,
    quiet=None,
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
            session.apply_workspace_root()
            print(f"loaded session: {args[0]}")
        else:
            print(f"no such session: {args[0]}")
    elif cmd == "/delete":
        if not args:
            print("usage: /delete <id>")
        elif args[0] == session.id:
            print("that's the active session -- /new first, then /delete it")
        elif store.delete(args[0]):
            _delete_session_workspace(session.config, args[0])
            print(f"deleted session: {args[0]}")
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
    elif cmd == "/quiet":
        if quiet is not None:
            quiet["on"] = not quiet["on"]
            print("quiet mode ON — tool output hidden" if quiet["on"]
                  else "quiet mode OFF — tool output shown")
    elif cmd == "/whoami":
        who = session.username or "(not logged in)"
        print(f"{who} ({session.role})" if session.role else who)
    elif cmd == "/usage":
        _handle_usage_command(args, session, db_engine)
    elif cmd == "/users":
        _handle_users_command(args, session, user_store)
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


def _login(store: DbUserStore, max_attempts: int = 3) -> str:
    """Authenticate a user against the account store, registering a new
    account on first sight of a username. Returns the logged-in username.

    `HARNESS_USER`/`HARNESS_PASSWORD` skip the interactive prompt (for
    scripted/demo use, same "env var first" convention as the rest of
    Config.load()); with only `HARNESS_USER` set, the username is
    pre-filled but the password is still prompted for."""
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
    db_engine = make_engine(config.db_url)
    user_store = DbUserStore(db_engine)
    username = _login(user_store)
    user_id = user_store.user_id(username)
    role = user_store.role(username) or ""

    # Token scaffolding (D30): issue a JWT for this login and verify it
    # immediately -- the same round trip a server's per-request middleware
    # will run. The CLI doesn't require the token afterwards (one process,
    # one login), but the path is exercised on every start, so it can't rot.
    secret = load_or_create_secret(config.jwt_secret_path)
    claims = verify_token(issue_token(user_id, role, secret, config.jwt_ttl_s), secret)
    assert claims == {"user_id": user_id, "role": role}  # setup error if not (fail loud)

    config = config.for_user(username)

    # Sandbox (D33): "docker" runs commands in a per-session container and
    # requires workspace confinement (that's all the container mounts). We
    # force confinement on rather than silently running unsandboxed, and
    # verify the daemon now so a broken Docker fails loud at startup.
    if config.sandbox == "docker":
        config = replace(config, confine_workspace=True)
        try:
            engine.sandbox.configure(
                SandboxConfig(
                    image=config.sandbox_image,
                    memory=config.sandbox_memory,
                    cpus=config.sandbox_cpus,
                    pids=config.sandbox_pids,
                    network=config.sandbox_network,
                )
            )
        except engine.sandbox.SandboxError as exc:
            raise SystemExit(f"sandbox startup failed: {exc}")

    provider = build_provider(config)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Each listener is independent: remove the import + these two lines and
    # nothing else in this file (or in either listener) needs to change.
    logger = EventLogger(config.logs_dir, run_id)
    memory_tracker = MemoryTracker(config.memory_dir, run_id)
    context_engine.memory_tool.set_memory_root(config.memory_dir)
    engine.builtin.offload.set_offload_root(config.offload_dir)
    quiet = {"on": config.quiet}  # mutable so /quiet can toggle it live
    on_event = _make_event_handler(logger, memory_tracker, quiet=quiet)

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

    # Durable usage accounting (D30): every model call becomes one
    # usage_log row attributed to this user, the session id current at
    # call time, and the task the user gave -- what /usage aggregates.
    usage_tracker = PersistentUsageTracker(
        db_engine,
        user_id,
        session_id_fn=lambda: session.id,
        task_fn=lambda: memory_tracker.task,
    )
    session = Session(
        config, provider, on_event,
        username=username, role=role, usage_tracker=usage_tracker,
    )
    store = DbSessionStore(db_engine, user_id)
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
            if _handle_command(
                user_input, session, store, mcp_manager, memory_tracker,
                roles, skills, user_store=user_store, db_engine=db_engine,
                quiet=quiet,
            ):
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
        engine.sandbox.shutdown()  # tear down any per-session containers


if __name__ == "__main__":
    main()
