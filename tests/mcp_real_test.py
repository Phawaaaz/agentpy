"""Real-stdio-MCP-server regression test (verification finding): the
fake-session unit test in mcp_test.py could not catch the anyio 'exit cancel
scope in a different task' crash that a REAL transport hits on disconnect.

This spins up an actual MCP stdio server (via the installed `mcp` SDK, so no
external binary is needed) through the real MCPManager and proves the full
lifecycle -- connect, discover, call, disconnect, reconnect, disconnect_all
-- runs without raising. Skips (with a notice) if the mcp server SDK isn't
importable.
"""

import os
import subprocess
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.mcp_client import MCPManager, MCPServerConfig
from engine.registry import Registry

_SERVER_SRC = textwrap.dedent(
    """
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("probe")

    @mcp.tool()
    def echo(text: str) -> str:
        \"\"\"Echo the text back.\"\"\"
        return f"echoed: {text}"

    if __name__ == "__main__":
        mcp.run()
    """
)


def _server_sdk_available() -> bool:
    try:
        import mcp.server.fastmcp  # noqa: F401
        return True
    except Exception:
        return False


def test_real_stdio_lifecycle_no_crash():
    if not _server_sdk_available():
        print("  [skipped] real MCP lifecycle (mcp server SDK not importable)")
        return
    with tempfile.TemporaryDirectory() as d:
        srv = os.path.join(d, "srv.py")
        with open(srv, "w", encoding="utf-8") as f:
            f.write(_SERVER_SRC)

        reg = Registry()
        mgr = MCPManager(reg)
        cfg = MCPServerConfig(
            name="probe", transport="stdio", command=sys.executable, args=[srv]
        )
        try:
            # Two full cycles -- disconnect used to crash with
            # "exit cancel scope in a different task".
            for i in range(2):
                names = mgr.connect(cfg)
                assert names == ["mcp__probe__echo"], names
                assert reg.get("mcp__probe__echo") is not None
                assert reg.run("mcp__probe__echo", {"text": f"c{i}"}) == f"echoed: c{i}"
                assert mgr.disconnect("probe") is True
                assert reg.get("mcp__probe__echo") is None
            # And the CLI's exit path over a live connection.
            mgr.connect(cfg)
            mgr.disconnect_all()  # must not raise
        finally:
            mgr.disconnect_all()
    print("  real stdio MCP: connect/call/disconnect x2 + disconnect_all, no crash OK")


def test_call_after_server_gone_returns_error_string():
    reg = Registry()
    mgr = MCPManager(reg)
    try:
        handler = mgr._make_handler("deadsrv", "echo")  # no live session
        result = handler(text="x")
        assert result.startswith("Error:") and "not connected" in result, result
    finally:
        mgr.disconnect_all()
    print("  call against a gone/absent MCP session returns a structured error OK")


def main():
    test_real_stdio_lifecycle_no_crash()
    test_call_after_server_gone_returns_error_string()
    print("MCP REAL TESTS PASSED")


if __name__ == "__main__":
    main()
