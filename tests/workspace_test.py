"""Tests for engine/workspace.py (D27) -- opt-in confinement of the
filesystem/shell tools to a per-session workspace directory, and the
regression guard that the default (no root) reproduces the historical
unconfined behavior exactly. No key, no network.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import workspace
from engine.builtin.filesystem import list_dir, read_file, write_file
from engine.builtin.shell import run_command


def _with_root(root):
    workspace.set_workspace_root(root)


def _reset():
    workspace.set_workspace_root(None)


def test_confine_rejects_escapes():
    with tempfile.TemporaryDirectory() as root:
        for bad in ("../outside.txt", "../../etc/passwd", "a/../../outside"):
            try:
                workspace.confine(root, bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{bad!r} should have been rejected")
        # An absolute path outside the root is rejected too.
        try:
            workspace.confine(root, "/etc/passwd")
        except ValueError:
            pass
        else:
            raise AssertionError("absolute path outside the root should be rejected")
    print("  confine() rejects ../ traversal and outside absolute paths OK")


def test_confine_allows_inside_paths():
    with tempfile.TemporaryDirectory() as root:
        resolved = workspace.confine(root, "notes/a.txt")
        assert resolved.startswith(os.path.realpath(root))
        # An absolute path already inside the root is fine.
        inside = os.path.join(os.path.realpath(root), "b.txt")
        assert workspace.confine(root, inside) == inside
        # The root itself is fine.
        assert workspace.confine(root, ".") == os.path.realpath(root)
    print("  confine() allows relative and inside-absolute paths OK")


def test_symlink_escape_rejected():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
        secret = os.path.join(outside, "secret.txt")
        with open(secret, "w", encoding="utf-8") as f:
            f.write("secret")
        link = os.path.join(root, "sneaky")
        try:
            os.symlink(outside, link)
        except OSError:
            # On Windows, creating symlinks requires admin privilege or Developer Mode.
            # Skip this test gracefully if we cannot create symlinks.
            print("  symlink escape test SKIPPED (no symlink privilege on Windows)")
            return
        try:
            workspace.confine(root, "sneaky/secret.txt")
        except ValueError:
            pass
        else:
            raise AssertionError("symlink pointing outside the root should be rejected")
    print("  symlink escape rejected (realpath) OK")


def test_tools_confined_when_root_set():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
        _with_root(root)
        try:
            # Inside: works, lands under the root.
            assert write_file("hello.txt", "hi").startswith("Wrote")
            assert os.path.exists(os.path.join(os.path.realpath(root), "hello.txt"))
            assert read_file("hello.txt") == "hi"
            assert "hello.txt" in list_dir(".")
            # Outside: every tool returns an error string, never raises.
            out_path = os.path.join(outside, "evil.txt")
            assert write_file(out_path, "x").startswith("Error:")
            assert not os.path.exists(out_path)
            assert read_file("../escape.txt").startswith("Error:")
            assert list_dir("/").startswith("Error:")
            # run_command executes inside the workspace root.
            cmd = "cd" if sys.platform == "win32" else "pwd"
            result = run_command(cmd)
            assert os.path.realpath(root) in result, result
        finally:
            _reset()
    print("  filesystem/shell tools confined when a root is set OK")


def test_default_behavior_unconfined():
    _reset()
    with tempfile.TemporaryDirectory() as anywhere:
        target = os.path.join(anywhere, "free.txt")
        # Absolute path far from the cwd: allowed, exactly as before D27.
        assert write_file(target, "free").startswith("Wrote")
        assert read_file(target) == "free"
        # run_command keeps the process's own cwd.
        cmd = "cd" if sys.platform == "win32" else "pwd"
        result = run_command(cmd)
        assert os.path.realpath(os.getcwd()) in result, result
    print("  default (no root) keeps the historical unconfined behavior OK")


def main():
    test_confine_rejects_escapes()
    test_confine_allows_inside_paths()
    test_symlink_escape_rejected()
    test_tools_confined_when_root_set()
    test_default_behavior_unconfined()
    print("WORKSPACE TESTS PASSED")


if __name__ == "__main__":
    main()
