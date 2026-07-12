"""Tests for the todo_write/todo_read planning tool
(engine/builtin/planning.py). No key, no network.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.builtin.planning as planning
from engine.registry import registry


def test_tools_registered():
    assert registry.get("todo_write") is not None
    assert registry.get("todo_write").risk == "safe"
    assert registry.get("todo_read") is not None
    print("  todo_write/todo_read registered as safe tools OK")


def test_empty_plan_reads_as_no_plan_set():
    planning.reset_plan()
    assert planning.todo_read() == "(no plan set)"
    print("  empty plan reads as '(no plan set)' OK")


def test_write_then_read_round_trips():
    planning.reset_plan()
    result = planning.todo_write(
        [
            {"step": "find the bug", "status": "completed"},
            {"step": "write a fix", "status": "in_progress"},
            {"step": "add a test"},  # status defaults to pending
        ]
    )
    assert "Plan updated:" in result
    read_back = planning.todo_read()
    assert "[x] find the bug" in read_back
    assert "[~] write a fix" in read_back
    assert "[ ] add a test" in read_back
    print("  todo_write then todo_read round-trips with default status OK")


def test_write_replaces_whole_plan():
    planning.reset_plan()
    planning.todo_write([{"step": "first step"}, {"step": "second step"}])
    planning.todo_write([{"step": "only this one", "status": "completed"}])
    read_back = planning.todo_read()
    assert read_back == "1. [x] only this one"
    print("  todo_write replaces the whole plan, not just one item OK")


def test_rejects_empty_list():
    planning.reset_plan()
    assert planning.todo_write([]).startswith("Error")
    print("  todo_write rejects an empty list OK")


def test_rejects_missing_step_key():
    planning.reset_plan()
    assert planning.todo_write([{"status": "pending"}]).startswith("Error")
    print("  todo_write rejects an item missing 'step' OK")


def test_rejects_invalid_status():
    planning.reset_plan()
    assert planning.todo_write([{"step": "x", "status": "done"}]).startswith("Error")
    print("  todo_write rejects an invalid status OK")


def test_reset_plan_clears_state():
    planning.todo_write([{"step": "x"}])
    planning.reset_plan()
    assert planning.todo_read() == "(no plan set)"
    print("  reset_plan clears state OK")


def main():
    test_tools_registered()
    test_empty_plan_reads_as_no_plan_set()
    test_write_then_read_round_trips()
    test_write_replaces_whole_plan()
    test_rejects_empty_list()
    test_rejects_missing_step_key()
    test_rejects_invalid_status()
    test_reset_plan_clears_state()
    print("PLANNING TESTS PASSED")


if __name__ == "__main__":
    main()
