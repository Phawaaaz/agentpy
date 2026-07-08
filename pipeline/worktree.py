"""Git worktree management for isolating one pipeline slice.

Plain subprocess calls, not a model tool: creating/removing a worktree and
deciding whether to commit is deterministic infrastructure the pipeline
controls, not something the model should be asked to decide.
"""

import os
import subprocess


class WorktreeError(Exception):
    pass


def _run_git(args: list[str], cwd: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def repo_root(start_dir: str = ".") -> str:
    return _run_git(["rev-parse", "--show-toplevel"], cwd=start_dir)


def create_worktree(repo_root_path: str, slice_id: str, base_ref: str = "HEAD") -> tuple[str, str]:
    """Create an isolated branch + worktree for one pipeline slice.

    Returns (worktree_path, branch_name).
    """
    branch = f"pipeline/{slice_id}"
    worktree_path = os.path.join(repo_root_path, ".harness", "worktrees", slice_id)
    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)
    _run_git(["worktree", "add", "-b", branch, worktree_path, base_ref], cwd=repo_root_path)
    return worktree_path, branch


def remove_worktree(repo_root_path: str, worktree_path: str) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", worktree_path],
        cwd=repo_root_path,
        capture_output=True,
        text=True,
    )


def has_uncommitted_changes(worktree_path: str) -> bool:
    """Whether the model made any (uncommitted) change this iteration --
    the stuck-detection signal."""
    return bool(_run_git(["status", "--porcelain"], cwd=worktree_path))


def diff_stat(worktree_path: str) -> str:
    return _run_git(["diff", "--stat", "HEAD"], cwd=worktree_path) or "(no changes)"


def commit_all(worktree_path: str, message: str) -> bool:
    """Stage and commit everything. Returns False if there was nothing to
    commit (not an error -- just a no-op)."""
    _run_git(["add", "-A"], cwd=worktree_path)
    result = subprocess.run(
        ["git", "commit", "-m", message], cwd=worktree_path, capture_output=True, text=True
    )
    return result.returncode == 0
