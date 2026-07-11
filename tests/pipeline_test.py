"""Pipeline tests: stage sequencing, stuck detection, iteration cap, and the
repair loop -- all against a FakeProvider (no key, no network) and a real,
local, disposable git repo (fast; no network involved in git operations).
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from engine.registry import Registry, Tool
from pipeline.config import PipelineConfig
from pipeline.runner import PipelineRunner
from providers.base import Provider, Response, ToolCall
import engine.builtin.filesystem as filesystem


class FakeProvider(Provider):
    """Returns a fixed script of turns instead of calling a real model."""

    def __init__(self, script):
        self._script = list(script)
        self._i = 0

    def complete(self, messages, tools):
        if self._i >= len(self._script):
            raise AssertionError(
                f"FakeProvider script exhausted after {self._i} calls -- "
                "the pipeline made more stage calls than the test expected"
            )
        turn = self._script[self._i]
        self._i += 1
        return turn


def _tool_turn(call_id, name, arguments):
    return Response(
        text=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        assistant_message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}
            ],
        },
    )


def _final_turn(text):
    return Response(text=text, tool_calls=[], assistant_message={"role": "assistant", "content": text})


def _init_repo(path: str) -> None:
    def git(*args):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write("hello\n")
    git("add", "-A")
    git("commit", "-q", "-m", "initial commit")


def _registry_with_filesystem_tools() -> Registry:
    """A private registry (not the shared singleton) so tests don't depend on
    import order or leak tools between test functions."""
    registry = Registry()
    registry.register(
        Tool(
            name="write_file",
            description="write a file",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            handler=filesystem.write_file,
            risk="write",
        )
    )
    return registry


def _run_in_repo(repo_path, fn):
    previous = os.getcwd()
    os.chdir(repo_path)
    try:
        return fn()
    finally:
        os.chdir(previous)


def test_normal_completion():
    with tempfile.TemporaryDirectory() as repo:
        _init_repo(repo)
        script = [
            _tool_turn("c1", "write_file", {"path": "out.txt", "content": "iteration 1"}),
            _final_turn("done implementing.\n<promise>COMPLETE</promise>"),
            _final_turn("self-review: looks correct.\n<promise>COMPLETE</promise>"),
            _final_turn("verify: ran it, works.\n<promise>COMPLETE</promise>"),
            _final_turn("test: all good.\n<tests>PASS</tests>\n<promise>COMPLETE</promise>"),
            _final_turn("docs: nothing stale.\n<promise>COMPLETE</promise>"),
        ]
        runner = PipelineRunner(
            FakeProvider(script),
            _registry_with_filesystem_tools(),
            Config(permission_mode="auto"),
            PipelineConfig(max_iterations=5, stuck_after=3, slice_timeout_s=60, max_repair_attempts=2),
        )
        result = _run_in_repo(repo, lambda: runner.run("write a file"))

        assert result.status == "complete", result
        assert result.stage == "done", result
        assert result.iterations == 1, result
        assert os.path.exists(os.path.join(result.worktree_path, "out.txt"))
        print("  normal completion OK:", result.summary)


def test_stuck_detection():
    with tempfile.TemporaryDirectory() as repo:
        _init_repo(repo)
        # Two iterations that make no changes and never signal completion.
        script = [_final_turn("thinking...") for _ in range(2)]
        runner = PipelineRunner(
            FakeProvider(script),
            _registry_with_filesystem_tools(),
            Config(permission_mode="auto"),
            PipelineConfig(max_iterations=10, stuck_after=2, slice_timeout_s=60),
        )
        result = _run_in_repo(repo, lambda: runner.run("do nothing task"))

        assert result.status == "stuck", result
        assert result.stage == "implement", result
        assert result.iterations == 2, result
        print("  stuck detection OK:", result.summary)


def test_max_iterations():
    with tempfile.TemporaryDirectory() as repo:
        _init_repo(repo)
        script = [_final_turn("still working...") for _ in range(3)]
        runner = PipelineRunner(
            FakeProvider(script),
            _registry_with_filesystem_tools(),
            Config(permission_mode="auto"),
            # stuck_after higher than max_iterations so the iteration cap
            # fires first, not stuck detection.
            PipelineConfig(max_iterations=3, stuck_after=100, slice_timeout_s=60),
        )
        result = _run_in_repo(repo, lambda: runner.run("a task that never finishes"))

        assert result.status == "max_iterations", result
        assert result.iterations == 3, result
        print("  max iterations cap OK:", result.summary)


def test_abort_signal():
    with tempfile.TemporaryDirectory() as repo:
        _init_repo(repo)
        script = [_final_turn("I need more information.\n<promise>ABORT</promise>")]
        runner = PipelineRunner(
            FakeProvider(script),
            _registry_with_filesystem_tools(),
            Config(permission_mode="auto"),
            PipelineConfig(max_iterations=5, stuck_after=3, slice_timeout_s=60),
        )
        result = _run_in_repo(repo, lambda: runner.run("an ambiguous task"))

        assert result.status == "aborted", result
        assert result.iterations == 1, result
        print("  abort signal OK:", result.summary)


def test_repair_loop_on_test_failure():
    with tempfile.TemporaryDirectory() as repo:
        _init_repo(repo)
        script = [
            # implement: one change, then COMPLETE
            _tool_turn("c1", "write_file", {"path": "code.py", "content": "buggy"}),
            _final_turn("implemented.\n<promise>COMPLETE</promise>"),
            # self_review, verify: no changes
            _final_turn("self-review OK.\n<promise>COMPLETE</promise>"),
            _final_turn("verify OK.\n<promise>COMPLETE</promise>"),
            # test attempt 1: FAIL
            _final_turn("ran tests.\n<tests>FAIL</tests>"),
            # repair attempt 1: fixes it
            _tool_turn("c2", "write_file", {"path": "code.py", "content": "fixed"}),
            _final_turn("fixed the bug.\n<promise>COMPLETE</promise>"),
            # test attempt 2: PASS
            _final_turn("ran tests again.\n<tests>PASS</tests>\n<promise>COMPLETE</promise>"),
            # sync_docs
            _final_turn("docs fine.\n<promise>COMPLETE</promise>"),
        ]
        runner = PipelineRunner(
            FakeProvider(script),
            _registry_with_filesystem_tools(),
            Config(permission_mode="auto"),
            PipelineConfig(max_iterations=5, stuck_after=3, slice_timeout_s=60, max_repair_attempts=2),
        )
        result = _run_in_repo(repo, lambda: runner.run("write code with a bug, then fix it"))

        assert result.status == "complete", result
        assert result.stage == "done", result
        with open(os.path.join(result.worktree_path, "code.py")) as f:
            assert f.read() == "fixed"
        print("  repair loop on test failure OK:", result.summary)


def main():
    test_normal_completion()
    test_stuck_detection()
    test_max_iterations()
    test_abort_signal()
    test_repair_loop_on_test_failure()
    print("PIPELINE TESTS PASSED")


if __name__ == "__main__":
    main()
