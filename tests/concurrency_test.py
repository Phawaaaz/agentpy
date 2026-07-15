"""Concurrency test (D28): two sessions in one process, each with its own
memory root, offload root, workspace root, and plan, running interleaved --
neither's state may leak into the other's. This is the test that could NOT
pass before the module globals became ContextVars: with globals, whichever
session set its roots last would capture the other's memory writes,
offloaded output, and plan. No key, no network.
"""

import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextvars

from context_engine.memory_tool import memory, set_memory_root
from engine import workspace
from engine.builtin.offload import maybe_offload, set_offload_root
from engine.builtin.planning import reset_plan, todo_read, todo_write
from engine.builtin.filesystem import write_file


def _session_work(name: str, base: str, barrier: threading.Barrier, results: dict):
    """Simulate one session: set per-session roots, then interleave work
    with the other session (barrier steps force true interleaving)."""
    memory_dir = os.path.join(base, "memory", name)
    offload_dir = os.path.join(base, "offload", name)
    workspace_dir = os.path.join(base, "ws", name)
    set_memory_root(memory_dir)
    set_offload_root(offload_dir)
    workspace.set_workspace_root(workspace_dir)
    reset_plan()

    barrier.wait()  # both sessions have set their roots -- with globals, one clobbered the other here
    memory(command="create", path="notes.md", file_text=f"notes of {name}")
    todo_write([{"step": f"step for {name}", "status": "in_progress"}])

    barrier.wait()  # both wrote -- now read back after the other session's writes
    big = f"offload-{name}-" + ("x" * 30_000)
    offload_result = maybe_offload(big, 20_000, "test")
    write_file("session_file.txt", f"file of {name}")

    barrier.wait()
    results[name] = {
        "memory": memory(command="view", path="notes.md"),
        "plan": todo_read(),
        "offload_dir_files": os.listdir(offload_dir) if os.path.isdir(offload_dir) else [],
        "offload_result": offload_result,
        "workspace_file": open(os.path.join(workspace_dir, "session_file.txt"), encoding="utf-8").read(),
    }


def test_two_sessions_do_not_corrupt_each_other():
    with tempfile.TemporaryDirectory() as base:
        barrier = threading.Barrier(2)
        results: dict = {}
        threads = [
            threading.Thread(target=_session_work, args=(name, base, barrier, results))
            for name in ("alice", "bob")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for name, other in (("alice", "bob"), ("bob", "alice")):
            r = results[name]
            assert f"notes of {name}" in r["memory"], r["memory"]
            assert f"notes of {other}" not in r["memory"]
            assert f"step for {name}" in r["plan"], r["plan"]
            assert f"step for {other}" not in r["plan"]
            assert r["offload_dir_files"], f"{name}'s offload dir is empty"
            assert f"offload/{name}" in r["offload_result"] or name in str(r["offload_dir_files"])
            assert r["workspace_file"] == f"file of {name}"
        # Each session's memory file lives only under its own root.
        assert os.path.exists(os.path.join(base, "memory", "alice", "notes.md"))
        assert os.path.exists(os.path.join(base, "memory", "bob", "notes.md"))
    print("  two interleaved sessions keep memory/plan/offload/workspace isolated OK")


def test_copied_context_isolates_plan_but_shares_memory_root():
    """The delegate tool's isolation shape (multiagent/coordinator.py): a
    sub-agent runs in a copied context -- fresh plan, inherited memory."""
    with tempfile.TemporaryDirectory() as base:
        set_memory_root(os.path.join(base, "memory"))
        reset_plan()
        todo_write([{"step": "coordinator step", "status": "in_progress"}])

        def sub_agent():
            reset_plan()
            todo_write([{"step": "sub-agent step"}])
            memory(command="create", path="shared.md", file_text="from the sub-agent")
            return todo_read()

        sub_plan = contextvars.copy_context().run(sub_agent)
        assert "sub-agent step" in sub_plan
        # The coordinator's plan is untouched by the sub-agent's writes...
        assert "coordinator step" in todo_read()
        assert "sub-agent step" not in todo_read()
        # ...but memory is shared by design (D17): the coordinator can read
        # what the sub-agent wrote.
        assert "from the sub-agent" in memory(command="view", path="shared.md")
    print("  copied context: sub-agent plan isolated, memory shared (D17/D23) OK")


def main():
    test_two_sessions_do_not_corrupt_each_other()
    test_copied_context_isolates_plan_but_shares_memory_root()
    print("CONCURRENCY TESTS PASSED")


if __name__ == "__main__":
    main()
