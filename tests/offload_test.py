"""Tests for tools/offload.py, and that read_file/run_command/fetch_url/
memory-view actually use it instead of the old hard-truncate. No key, no
network -- fetch_url isn't exercised here (needs a real request); its use
of maybe_offload is identical in shape to read_file's, which is covered.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.filesystem as filesystem
import tools.memory as memory
import tools.offload as offload
import tools.shell as shell


def test_small_output_stays_inline_and_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        offload.set_offload_root(tmp)
        result = offload.maybe_offload("short output", 1000, "test")
        assert result == "short output"
        assert os.listdir(tmp) == []
        print("  small output stays inline, no file written OK")


def test_large_output_offloads_with_preview_and_path():
    with tempfile.TemporaryDirectory() as tmp:
        offload.set_offload_root(tmp)
        big = "line\n" * 10_000  # 50,000 chars
        result = offload.maybe_offload(big, 1000, "mytool")

        assert len(result) < len(big)
        assert "mytool-" in result and ".txt" in result
        files = os.listdir(tmp)
        assert len(files) == 1
        with open(os.path.join(tmp, files[0])) as f:
            assert f.read() == big  # nothing lost, unlike hard truncation
        print("  large output offloads: preview + full content recoverable OK")


def test_identical_content_dedups_to_one_file():
    with tempfile.TemporaryDirectory() as tmp:
        offload.set_offload_root(tmp)
        big = "x" * 5000
        offload.maybe_offload(big, 100, "dup")
        offload.maybe_offload(big, 100, "dup")
        assert len(os.listdir(tmp)) == 1
        print("  identical content dedups to one file OK")


def test_read_file_offloads_large_files():
    with tempfile.TemporaryDirectory() as tmp:
        offload.set_offload_root(os.path.join(tmp, "offload"))
        big_path = os.path.join(tmp, "big.txt")
        with open(big_path, "w") as f:
            f.write("y" * 30_000)

        original_max = filesystem._MAX_OUTPUT
        filesystem._MAX_OUTPUT = 1000
        try:
            result = filesystem.read_file(big_path)
        finally:
            filesystem._MAX_OUTPUT = original_max

        assert "Full output saved to" in result
        assert os.path.exists(os.path.join(tmp, "offload"))
        print("  read_file offloads large files OK")


def test_run_command_offloads_large_output():
    with tempfile.TemporaryDirectory() as tmp:
        offload.set_offload_root(tmp)
        original_max = shell._MAX_OUTPUT
        shell._MAX_OUTPUT = 500
        try:
            result = shell.run_command("python3 -c \"print('z' * 5000)\"")
        finally:
            shell._MAX_OUTPUT = original_max

        assert "Full output saved to" in result
        print("  run_command offloads large output OK")


def test_memory_view_offloads_large_files():
    with tempfile.TemporaryDirectory() as tmp:
        memory.set_memory_root(os.path.join(tmp, "memory"))
        offload.set_offload_root(os.path.join(tmp, "offload"))
        memory.memory(command="create", path="big.md", file_text="v" * 30_000)

        original_max = memory._MAX_OUTPUT
        memory._MAX_OUTPUT = 1000
        try:
            result = memory.memory(command="view", path="big.md")
        finally:
            memory._MAX_OUTPUT = original_max

        assert "Full output saved to" in result
        print("  memory view offloads large files OK")


def test_empty_content_still_reports_empty():
    with tempfile.TemporaryDirectory() as tmp:
        offload.set_offload_root(tmp)
        assert (offload.maybe_offload("", 100, "x") or "(empty file)") == "(empty file)"
        print("  empty content still falls back to '(empty file)' OK")


def main():
    test_small_output_stays_inline_and_writes_nothing()
    test_large_output_offloads_with_preview_and_path()
    test_identical_content_dedups_to_one_file()
    test_read_file_offloads_large_files()
    test_run_command_offloads_large_output()
    test_memory_view_offloads_large_files()
    test_empty_content_still_reports_empty()
    print("OFFLOAD TESTS PASSED")


if __name__ == "__main__":
    main()
