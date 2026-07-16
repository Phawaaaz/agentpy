"""Tests for engine/builtin/search_files.py (find_files/grep_files), the
git_commit checkpoint tool, and the per-call latency now attached to
usage/tool_result events (Milestone 9 / AUDIT C2+D3+I1).
No key, no network; git tests use a disposable local repo.
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from engine.builtin.git_tool import git_commit
from engine.builtin.search_files import find_files, grep_files
from engine.orchestrator import Orchestrator
from engine.registry import registry
from providers.base import Provider, Response, ToolCall, Usage

import engine.builtin.filesystem  # noqa: F401


def _seed(tmp):
    os.makedirs(os.path.join(tmp, "pkg", "__pycache__"), exist_ok=True)
    with open(os.path.join(tmp, "pkg", "alpha.py"), "w", encoding="utf-8") as f:
        f.write("def hello():\n    return 'needle-in-alpha'\n")
    with open(os.path.join(tmp, "beta.txt"), "w", encoding="utf-8") as f:
        f.write("no needles here\n")
    with open(os.path.join(tmp, "pkg", "__pycache__", "junk.py"), "w", encoding="utf-8") as f:
        f.write("needle-in-cache\n")


def test_find_files():
    with tempfile.TemporaryDirectory() as tmp:
        _seed(tmp)
        result = find_files("*.py", tmp)
        assert "alpha.py" in result
        assert "junk.py" not in result, "__pycache__ must be skipped"
        assert find_files("*.nothing", tmp) == "(no matches)"
        assert find_files("*.py", os.path.join(tmp, "missing")).startswith("Error:")
    print("  find_files (glob, skip dirs, no-match, bad path) OK")


def test_grep_files():
    with tempfile.TemporaryDirectory() as tmp:
        _seed(tmp)
        result = grep_files("needle-in", tmp)
        assert "alpha.py:2:" in result and "needle-in-alpha" in result
        assert "junk.py" not in result
        scoped = grep_files("needle-in", tmp, file_glob="*.txt")
        assert scoped == "(no matches)", scoped
        scoped_txt = grep_files("needles", tmp, file_glob="*.txt")
        assert "beta.txt:1:" in scoped_txt and "alpha.py" not in scoped_txt
        assert grep_files("[invalid(regex", tmp).startswith("Error: invalid regular expression")
    print("  grep_files (line numbers, glob scoping, bad regex) OK")


def test_git_commit_checkpoint():
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, check=True)
        with open(os.path.join(tmp, "work.txt"), "w", encoding="utf-8") as f:
            f.write("checkpoint me")
        result = git_commit("checkpoint 1", path=tmp)
        assert "checkpoint 1" in result, result
        log = subprocess.run(["git", "log", "--oneline"], cwd=tmp, capture_output=True, text=True).stdout
        assert "checkpoint 1" in log
        # Clean tree: a friendly no-op, not an error.
        assert git_commit("nothing", path=tmp) == "(nothing to commit -- working tree clean)"
        assert git_commit("   ", path=tmp).startswith("Error:")
    print("  git_commit (checkpoint, clean-tree no-op, empty message) OK")


class _FakeProvider(Provider):
    def __init__(self):
        self._turns = [
            Response(
                text=None,
                tool_calls=[ToolCall(id="c1", name="list_dir", arguments={"path": "."})],
                assistant_message={"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]},
                usage=Usage(10, 5),
            ),
            Response(text="done", tool_calls=[],
                     assistant_message={"role": "assistant", "content": "done"},
                     usage=Usage(20, 10)),
        ]

    def complete(self, messages, tools):
        return self._turns.pop(0)


def test_events_carry_duration():
    from observability.usage import UsageTracker

    events = []
    agent = Orchestrator(
        _FakeProvider(), registry, Config(permission_mode="auto"),
        on_event=lambda kind, *d: events.append((kind, d)),
        usage_tracker=UsageTracker(),
    )
    agent.run("go")
    usage_events = [d for k, d in events if k == "usage"]
    tool_events = [d for k, d in events if k == "tool_result"]
    assert usage_events and all(len(d) == 3 and isinstance(d[2], int) for d in usage_events), usage_events
    assert tool_events and all(len(d) == 3 and isinstance(d[2], int) for d in tool_events), tool_events
    print("  usage/tool_result events carry duration_ms OK")


def main():
    test_find_files()
    test_grep_files()
    test_git_commit_checkpoint()
    test_events_carry_duration()
    print("SEARCH FILES TESTS PASSED")


if __name__ == "__main__":
    main()
