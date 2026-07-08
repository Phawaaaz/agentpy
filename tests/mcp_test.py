"""MCP client tests: tool wrapping, risk mapping, and call dispatch.

Uses a duck-typed fake MCP session (no real subprocess/network) so this stays
in the "no key, no network" test tier. Connecting to a *real* MCP server (e.g.
`npx @modelcontextprotocol/server-filesystem`) is a manual, documented check —
see CONTRIBUTING.md — since the transport itself can't be meaningfully faked.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.mcp_client import MCPManager, load_server_configs
from tools.registry import Registry


def _fake_tool(name, description="", input_schema=None, annotations=None):
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=input_schema or {"type": "object", "properties": {}},
        annotations=annotations,
    )


def _fake_result(text, is_error=False):
    return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=is_error)


class FakeSession:
    """Mimics mcp.ClientSession's async surface without any real transport."""

    def __init__(self, tools, results):
        self._tools = tools
        self._results = results  # tool_name -> result (or Exception to raise)
        self.calls = []

    async def list_tools(self):
        return SimpleNamespace(tools=self._tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        outcome = self._results[name]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_tool_wrapping_and_risk_mapping():
    registry = Registry()
    manager = MCPManager(registry)
    try:
        tools = [
            _fake_tool("search", "search the web", annotations=SimpleNamespace(readOnlyHint=True, destructiveHint=False)),
            _fake_tool("delete_repo", "delete a repo", annotations=SimpleNamespace(readOnlyHint=False, destructiveHint=True)),
            _fake_tool("no_hints", "unknown risk"),
        ]
        session = FakeSession(tools, results={})
        names = manager.connect_session("gh", session)

        assert names == ["mcp__gh__search", "mcp__gh__delete_repo", "mcp__gh__no_hints"]
        assert registry.get("mcp__gh__search").risk == "safe"
        assert registry.get("mcp__gh__delete_repo").risk == "dangerous"
        assert registry.get("mcp__gh__no_hints").risk == "write"  # unknown => assume it can write
        print("  tool wrapping + risk mapping OK")
    finally:
        manager.disconnect_all()


def test_call_dispatch_and_error_handling():
    registry = Registry()
    manager = MCPManager(registry)
    try:
        tools = [_fake_tool("echo")]
        session = FakeSession(tools, results={"echo": _fake_result("hello back")})
        manager.connect_session("svc", session)

        result = registry.run("mcp__svc__echo", {"msg": "hi"})
        assert result == "hello back", result
        assert session.calls == [("echo", {"msg": "hi"})]

        # A server-reported error surfaces as text, never raises into the loop.
        error_session = FakeSession([_fake_tool("boom")], results={"boom": _fake_result("bad input", is_error=True)})
        manager.connect_session("svc2", error_session)
        result = registry.run("mcp__svc2__boom", {})
        assert result.startswith("Error:"), result

        # A disconnected server reports an error string too, never a crash.
        manager.disconnect("svc")
        result = registry.run("mcp__svc__echo", {"msg": "hi"})
        assert "unknown tool" in result  # unregistered on disconnect
        print("  call dispatch + error handling OK")
    finally:
        manager.disconnect_all()


def test_disconnect_removes_tools():
    registry = Registry()
    manager = MCPManager(registry)
    try:
        session = FakeSession([_fake_tool("a"), _fake_tool("b")], results={})
        manager.connect_session("svc", session)
        assert len(registry.all()) == 2

        assert manager.disconnect("svc") is True
        assert registry.all() == []
        assert manager.disconnect("svc") is False  # already gone
        print("  disconnect removes tools OK")
    finally:
        manager.disconnect_all()


def test_load_server_configs():
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "mcp.json")
        with open(path, "w") as f:
            json.dump(
                {
                    "mcpServers": {
                        "local": {"command": "npx", "args": ["-y", "some-server"]},
                        "hosted": {"url": "https://example.com/mcp", "transport": "http"},
                    }
                },
                f,
            )
        configs = {c.name: c for c in load_server_configs(path)}
        assert configs["local"].transport == "stdio"
        assert configs["local"].command == "npx"
        assert configs["hosted"].transport == "http"
        assert configs["hosted"].url == "https://example.com/mcp"
        assert load_server_configs(os.path.join(d, "missing.json")) == []
        print("  load_server_configs OK")


def main():
    test_tool_wrapping_and_risk_mapping()
    test_call_dispatch_and_error_handling()
    test_disconnect_removes_tools()
    test_load_server_configs()
    print("MCP TESTS PASSED")


if __name__ == "__main__":
    main()
