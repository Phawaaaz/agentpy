"""Tests for the /review /verify /test /docs skill commands in the CLI --
`interfaces.cli._handle_skill_command`. Uses fakes for Session/store/memory
so this stays in the "no key, no network" tier; only `pipeline.worktree` runs
real (local, no network) git commands against a throwaway repo.
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interfaces.cli import _SKILLS, _handle_skill_command


class FakeAgent:
    def __init__(self, answer="done"):
        self.answer = answer
        self.last_prompt = None

    def run(self, prompt):
        self.last_prompt = prompt
        return self.answer


class FakeUsage:
    def summary(self):
        return "0 calls"


class FakeSession:
    def __init__(self):
        self.agent = FakeAgent()
        self.usage = FakeUsage()
        self.id = "sess1"
        self.conversation = object()


class FakeStore:
    def __init__(self):
        self.saved = []

    def save(self, session_id, conversation):
        self.saved.append(session_id)


class FakeMemoryTracker:
    def __init__(self, task=""):
        self.task = task


def _init_repo(path: str) -> None:
    def git(*args):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    with open(os.path.join(path, "f.txt"), "w") as f:
        f.write("x\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


def _run_in(path, fn):
    previous = os.getcwd()
    os.chdir(path)
    try:
        return fn()
    finally:
        os.chdir(previous)


def test_all_four_skills_registered():
    assert set(_SKILLS.keys()) == {"review", "verify", "test", "docs"}
    print("  all four skills registered OK")


def test_explicit_task_overrides_memory():
    with tempfile.TemporaryDirectory() as repo:
        _init_repo(repo)
        session, store, tracker = FakeSession(), FakeStore(), FakeMemoryTracker(task="stale task")

        def run():
            _handle_skill_command("verify", ["a", "fresh", "task"], session, store, tracker)

        _run_in(repo, run)
        assert "a fresh task" in session.agent.last_prompt
        assert "stale task" not in session.agent.last_prompt
        assert store.saved == ["sess1"]
        print("  explicit task overrides memory task OK")


def test_falls_back_to_memory_task():
    with tempfile.TemporaryDirectory() as repo:
        _init_repo(repo)
        session, store, tracker = FakeSession(), FakeStore(), FakeMemoryTracker(task="the current task")

        def run():
            _handle_skill_command("docs", [], session, store, tracker)

        _run_in(repo, run)
        assert "the current task" in session.agent.last_prompt
        print("  falls back to memory task OK")


def test_no_task_anywhere_does_not_call_agent():
    with tempfile.TemporaryDirectory() as repo:
        _init_repo(repo)
        session, store, tracker = FakeSession(), FakeStore(), FakeMemoryTracker(task="")

        def run():
            _handle_skill_command("test", [], session, store, tracker)

        _run_in(repo, run)
        assert session.agent.last_prompt is None  # never ran -- nothing to check
        assert store.saved == []
        print("  no task anywhere -> skips the agent call OK")


def test_non_git_directory_falls_back_gracefully():
    with tempfile.TemporaryDirectory() as not_a_repo:
        session, store, tracker = FakeSession(), FakeStore(), FakeMemoryTracker(task="a task")

        def run():
            _handle_skill_command("review", [], session, store, tracker)

        _run_in(not_a_repo, run)
        assert "not a git repository" in session.agent.last_prompt
        print("  non-git directory falls back gracefully OK")


def main():
    test_all_four_skills_registered()
    test_explicit_task_overrides_memory()
    test_falls_back_to_memory_task()
    test_no_task_anywhere_does_not_call_agent()
    test_non_git_directory_falls_back_gracefully()
    print("CLI SKILLS TESTS PASSED")


if __name__ == "__main__":
    main()
