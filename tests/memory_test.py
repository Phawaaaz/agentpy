"""Memory feature tests: the memory tool's commands + path confinement, and
MemoryTracker's automatic activity summary -- both independently, and the
EventLogger/MemoryTracker "listener" contract they share with on_event.

No key, no network.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import context_engine.memory_tool as memory_tool
from context_engine.memory_tracker import MemoryTracker
from engine.registry import registry
from observability.log import EventLogger


def test_memory_tool_crud():
    with tempfile.TemporaryDirectory() as tmp:
        memory_tool.set_memory_root(tmp)

        assert memory_tool.memory(command="create", path="notes.md", file_text="a\nb\n").startswith("Created")
        assert memory_tool.memory(command="view", path="notes.md") == "1: a\n2: b"

        assert memory_tool.memory(command="str_replace", path="notes.md", old_str="a", new_str="A").startswith("Edited")
        assert memory_tool.memory(command="view", path="notes.md") == "1: A\n2: b"

        assert memory_tool.memory(command="insert", path="notes.md", insert_line=1, insert_text="mid").startswith("Inserted")
        assert memory_tool.memory(command="view", path="notes.md") == "1: A\n2: mid\n3: b"

        assert memory_tool.memory(command="rename", old_path="notes.md", new_path="archive/notes.md").startswith("Renamed")
        assert "does not exist" in memory_tool.memory(command="view", path="notes.md")
        assert memory_tool.memory(command="view", path="archive/notes.md") == "1: A\n2: mid\n3: b"

        assert memory_tool.memory(command="delete", path="archive/notes.md").startswith("Deleted")
        assert "does not exist" in memory_tool.memory(command="view", path="archive/notes.md")
        print("  memory tool CRUD OK")


def test_memory_tool_error_paths_never_raise():
    with tempfile.TemporaryDirectory() as tmp:
        memory_tool.set_memory_root(tmp)

        assert "escapes" in memory_tool.memory(command="view", path="../../etc/passwd")
        assert "escapes" in memory_tool.memory(command="create", path="/../outside.md", file_text="x")
        assert "unknown memory command" in memory_tool.memory(command="frobnicate", path="x")
        assert "does not exist" in memory_tool.memory(command="str_replace", path="missing.md", old_str="a", new_str="b")

        memory_tool.memory(command="create", path="dup.md", file_text="aaa")
        result = memory_tool.memory(command="str_replace", path="dup.md", old_str="a", new_str="b")
        assert "appears 3 times" in result
        print("  memory tool error paths OK (all strings, nothing raised)")


def test_memory_tool_registered_and_dispatchable_via_registry():
    with tempfile.TemporaryDirectory() as tmp:
        memory_tool.set_memory_root(tmp)
        tool = registry.get("memory")
        assert tool is not None and tool.risk == "write"
        result = registry.run("memory", {"command": "create", "path": "x.md", "file_text": "hi"})
        assert result.startswith("Created")
        print("  memory tool reachable through the shared registry OK")


def test_memory_tracker_summary():
    with tempfile.TemporaryDirectory() as tmp:
        tracker = MemoryTracker(tmp, "sess1")
        tracker.set_task("add a health check endpoint")
        tracker.log("tool_call", "read_file", {"path": "app.py"})
        tracker.log("tool_call", "write_file", {"path": "app.py"})
        tracker.log("tool_call", "write_file", {"path": "tests/test_app.py"})
        tracker.log("tool_result", "write_file", "ok")  # non-tool_call events are ignored

        summary = tracker.summary()
        assert "add a health check endpoint" in summary
        assert "app.py (read, write)" in summary
        assert "tests/test_app.py (write)" in summary
        assert "write_file: 2" in summary
        assert "read_file: 1" in summary

        with open(os.path.join(tmp, "activity.md")) as f:
            assert f.read() == summary
        print("  MemoryTracker summary + on-disk persistence OK")


def test_listeners_share_one_contract():
    """EventLogger and MemoryTracker both expose log(kind, *details) -- the
    same shape as core/orchestrator.py's on_event callback -- so a fan-out
    caller can treat them interchangeably without either knowing the other
    exists (this is what interfaces/cli.py's _make_event_handler relies on)."""
    with tempfile.TemporaryDirectory() as tmp:
        el = EventLogger(tmp, "sess1")
        mt = MemoryTracker(tmp, "sess1")
        listeners = [el, mt]
        for listener in listeners:
            listener.log("tool_call", "read_file", {"path": "x.py"})
        assert mt.tool_counts["read_file"] == 1
        with open(el.path) as f:
            record = f.read()
        assert '"kind": "tool_call"' in record
        print("  shared listener contract OK")


def test_memory_overview_for_injection():
    with tempfile.TemporaryDirectory() as tmp:
        # Empty memory -> no injection text at all.
        assert memory_tool.memory_overview(tmp) == ""
        # Seed a note + a subdirectory; both appear in the digest.
        memory_tool.set_memory_root(tmp)
        memory_tool.memory(command="create", path="notes.md", file_text="remember: use SQLite")
        memory_tool.memory(command="create", path="projects/x.md", file_text="deep note")
        overview = memory_tool.memory_overview(tmp)
        assert "remember: use SQLite" in overview
        assert "projects/" in overview and "deep note" not in overview, "subdirs listed, not inlined"
        # The cap is enforced, with a pointer to the memory tool.
        memory_tool.memory(command="create", path="big.md", file_text="x" * 10_000)
        capped = memory_tool.memory_overview(tmp, max_chars=500)
        assert len(capped) < 700 and "truncated" in capped
        print("  memory_overview digest (content, subdirs, cap) OK")


def test_memory_injected_into_new_session_system_prompt():
    import interfaces.cli as cli
    from config import Config
    from providers.base import Provider, Response

    class _Silent(Provider):
        def complete(self, messages, tools):
            return Response(text="ok", tool_calls=[], assistant_message={"role": "assistant", "content": "ok"})

    with tempfile.TemporaryDirectory() as tmp:
        memory_tool.set_memory_root(tmp)
        memory_tool.memory(command="create", path="notes.md", file_text="the deploy password lives in vault X")
        config = Config(model="anthropic/claude-opus-4-8", memory_dir=tmp)
        session = cli.Session(config, _Silent(), lambda *a: None, username="t")
        system = session.conversation.to_list()[0]["content"]
        assert "the deploy password lives in vault X" in system
        assert "Your memory" in system
        # And with an empty memory dir, the prompt stays untouched.
        with tempfile.TemporaryDirectory() as empty:
            config2 = Config(model="anthropic/claude-opus-4-8", memory_dir=empty)
            session2 = cli.Session(config2, _Silent(), lambda *a: None, username="t")
            assert "Your memory" not in session2.conversation.to_list()[0]["content"]
        print("  memory auto-injected into a new session's system prompt (D31) OK")


def main():
    test_memory_tool_crud()
    test_memory_overview_for_injection()
    test_memory_injected_into_new_session_system_prompt()
    test_memory_tool_error_paths_never_raise()
    test_memory_tool_registered_and_dispatchable_via_registry()
    test_memory_tracker_summary()
    test_listeners_share_one_contract()
    print("MEMORY TESTS PASSED")


if __name__ == "__main__":
    main()
