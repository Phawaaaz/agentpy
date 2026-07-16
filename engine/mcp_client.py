"""MCP client integration: connect to external MCP servers and expose their
tools through the same Registry/permission machinery as built-in tools.

Unlike tools/filesystem.py etc., MCP servers are live, stateful connections
(a subprocess or a network client), not plain functions — there is no
import-time self-registration to hook into. `MCPManager` is a deliberate,
documented exception to that pattern (see DESIGN.md D14): it owns real
connection lifecycle and registers/deregisters tools as servers come and go.

All `mcp` SDK calls are async; the rest of the harness is synchronous. A
single background event loop (in its own thread) bridges the two — every
operation is dispatched onto that loop with `run_coroutine_threadsafe` and
waited on synchronously, so tool handlers stay plain functions returning str.
"""

import asyncio
import concurrent.futures as cf
import contextlib
import threading
from dataclasses import dataclass, field
from typing import Literal

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from engine.registry import Registry, Tool

_MAX_OUTPUT = 20_000
_CONNECT_TIMEOUT = 30
_CALL_TIMEOUT = 60
_DISCONNECT_TIMEOUT = 10

Transport = Literal["stdio", "sse", "http"]


@dataclass
class MCPServerConfig:
    """One configured MCP server, from `.harness/mcp.json`."""

    name: str
    transport: Transport = "stdio"
    command: str | None = None  # stdio
    args: list[str] = field(default_factory=list)  # stdio
    env: dict[str, str] | None = None  # stdio
    url: str | None = None  # sse / http
    risk: str | None = None  # override the annotation-derived default


def _risk_from_annotations(annotations) -> str:
    """MCP servers are third-party and untrusted by default. Prefer the
    server's own tool annotations when it declares them; otherwise assume a
    tool can change state (`write`) rather than silently trusting it."""
    if annotations is None:
        return "write"
    if getattr(annotations, "destructiveHint", False):
        return "dangerous"
    if getattr(annotations, "readOnlyHint", False):
        return "safe"
    return "write"


def _result_to_text(result) -> str:
    parts = [getattr(block, "text", None) or str(block) for block in result.content]
    body = "\n".join(parts) if parts else "(no output)"
    if result.isError:
        body = "Error: " + body
    if len(body) > _MAX_OUTPUT:
        body = body[:_MAX_OUTPUT] + "\n... [truncated]"
    return body


class _ServerConnection:
    """Owns one server's transport + session for its whole lifetime inside a
    SINGLE asyncio task.

    The transport/session context managers use anyio cancel scopes, which
    must be entered and exited in the same task. Entering them in one
    `run_coroutine_threadsafe` call and exiting them in another (a different
    task on the same loop) raises "Attempted to exit cancel scope in a
    different task than it was entered in". So the entire lifecycle -- enter,
    stay open across many tool calls, then tear down -- runs in one
    long-lived coroutine (`run`) on the background loop; `connect` waits for
    it to publish the session, `disconnect` signals it to tear down."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._close: asyncio.Event | None = None
        self.task_future: "cf.Future | None" = None  # the run() coroutine's future

    async def _enter(self, stack: contextlib.AsyncExitStack) -> ClientSession:
        config = self.config
        if config.transport == "stdio":
            if not config.command:
                raise ValueError(f"MCP server '{config.name}': stdio transport needs 'command'")
            params = StdioServerParameters(command=config.command, args=config.args, env=config.env)
            read, write = await stack.enter_async_context(stdio_client(params))
        elif config.transport == "sse":
            if not config.url:
                raise ValueError(f"MCP server '{config.name}': sse transport needs 'url'")
            read, write = await stack.enter_async_context(sse_client(config.url))
        elif config.transport == "http":
            if not config.url:
                raise ValueError(f"MCP server '{config.name}': http transport needs 'url'")
            read, write, _get_session_id = await stack.enter_async_context(
                streamable_http_client(config.url)
            )
        else:
            raise ValueError(f"MCP server '{config.name}': unknown transport '{config.transport}'")

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def run(self, ready: "cf.Future") -> None:
        """Enter the transport+session, hand the session back through
        `ready`, stay alive until `signal_close()`, then tear down -- all in
        this one task."""
        self._close = asyncio.Event()
        try:
            async with contextlib.AsyncExitStack() as stack:
                session = await self._enter(stack)
                if not ready.done():
                    ready.set_result(session)
                await self._close.wait()
        except Exception as exc:  # noqa: BLE001 -- surfaced via `ready` or swallowed
            # A failure before the session is published is the connect error
            # the caller must see; a failure during teardown (after ready) is
            # best-effort and swallowed so disconnect never raises.
            if not ready.done():
                ready.set_exception(exc)

    def signal_close(self) -> None:
        """Ask the run() task to tear down. Scheduled onto the loop thread."""
        if self._close is not None:
            self._close.set()


class MCPManager:
    """Connects to configured MCP servers and registers their tools onto a
    shared `Registry`, namespaced as `mcp__<server>__<tool>`."""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._connections: dict[str, _ServerConnection] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._tool_names: dict[str, list[str]] = {}

    def _run(self, coro, timeout: float):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout)

    def connect(self, config: MCPServerConfig) -> list[str]:
        """Connect to a server and register its tools. Returns the registered
        (namespaced) tool names. Reconnects if already connected."""
        if config.name in self._sessions:
            self.disconnect(config.name)

        connection = _ServerConnection(config)
        # Start the lifecycle coroutine on the bg loop; it publishes the
        # session through `ready` once connected, then stays in that same
        # task until disconnect signals it to tear down.
        ready: cf.Future = cf.Future()
        connection.task_future = asyncio.run_coroutine_threadsafe(
            connection.run(ready), self._loop
        )
        session = ready.result(_CONNECT_TIMEOUT)  # raises if _enter failed
        self._connections[config.name] = connection
        return self.connect_session(config.name, session, default_risk=config.risk)

    def connect_session(self, name: str, session: ClientSession, default_risk: str | None = None) -> list[str]:
        """Register tools from an already-open session. Split out from
        `connect` so the list/wrap/register logic is reachable with a fake
        session in tests, independent of the real transport."""
        self._sessions[name] = session

        remote_tools = self._run(session.list_tools(), _CONNECT_TIMEOUT).tools
        names = []
        for remote_tool in remote_tools:
            local_name = f"mcp__{name}__{remote_tool.name}"
            risk = default_risk or _risk_from_annotations(remote_tool.annotations)
            self.registry.register(
                Tool(
                    name=local_name,
                    description=remote_tool.description or remote_tool.name,
                    parameters=remote_tool.inputSchema or {"type": "object", "properties": {}},
                    handler=self._make_handler(name, remote_tool.name),
                    risk=risk,
                )
            )
            names.append(local_name)
        self._tool_names[name] = names
        return names

    def _make_handler(self, server_name: str, remote_tool_name: str):
        def handler(**arguments) -> str:
            session = self._sessions.get(server_name)
            if session is None:
                return f"Error: MCP server '{server_name}' is not connected"
            try:
                result = self._run(session.call_tool(remote_tool_name, arguments), _CALL_TIMEOUT)
            except Exception as exc:
                return f"Error calling {server_name}/{remote_tool_name}: {exc}"
            return _result_to_text(result)

        return handler

    def disconnect(self, name: str) -> bool:
        had_session = self._sessions.pop(name, None) is not None
        tool_names = self._tool_names.pop(name, [])
        for tool_name in tool_names:
            self.registry.unregister(tool_name)
        connection = self._connections.pop(name, None)
        if connection is not None:
            # Signal the run() task to tear down IN ITS OWN TASK (avoids the
            # anyio "exit cancel scope in a different task" crash), then wait
            # for it to finish. Teardown errors are swallowed -- tools are
            # already deregistered, so disconnect must not raise.
            self._loop.call_soon_threadsafe(connection.signal_close)
            if connection.task_future is not None:
                try:
                    connection.task_future.result(_DISCONNECT_TIMEOUT)
                except Exception:
                    pass
        return had_session or bool(tool_names)

    def disconnect_all(self) -> None:
        for name in list(self._sessions):
            self.disconnect(name)
        self._loop.call_soon_threadsafe(self._loop.stop)

    def list_connected(self) -> dict[str, list[str]]:
        """server name -> its registered (namespaced) tool names."""
        return dict(self._tool_names)


def load_server_configs(path: str) -> list[MCPServerConfig]:
    """Parse a Claude-Desktop-style `mcpServers` config file into configs.

    ```json
    {"mcpServers": {
        "search": {"command": "npx", "args": ["-y", "some-mcp-server"]},
        "hosted": {"url": "https://example.com/mcp", "transport": "http"}
    }}
    ```
    A server entry with "command" defaults to stdio; one with "url" defaults
    to "http". Set "transport" explicitly to override (e.g. "sse").
    """
    import json
    import os

    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    configs = []
    for name, entry in data.get("mcpServers", {}).items():
        transport = entry.get("transport") or ("stdio" if "command" in entry else "http")
        configs.append(
            MCPServerConfig(
                name=name,
                transport=transport,
                command=entry.get("command"),
                args=entry.get("args", []),
                env=entry.get("env"),
                url=entry.get("url"),
                risk=entry.get("risk"),
            )
        )
    return configs
