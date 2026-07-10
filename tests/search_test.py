"""Tests for search tools (grep, find_files). No API key needed."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.search as search


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)

    grep_hits = search.grep("class Orchestrator", path=root, glob="**/*.py")
    assert "core/orchestrator.py" in grep_hits.replace("\\", "/"), grep_hits

    bad_pattern = search.grep("[", path=root)
    assert bad_pattern.startswith("Error:"), bad_pattern

    missing = search.grep("foo", path=os.path.join(root, "no_such_path"))
    assert missing.startswith("Error:"), missing

    files = search.find_files(os.path.join(root, "tools", "*.py"))
    assert "search.py" in files.replace("\\", "/"), files

    empty = search.find_files(os.path.join(root, "no_such_glob_*.xyz"))
    assert empty == "(no matches)", empty

    print("SEARCH TESTS PASSED")


if __name__ == "__main__":
    main()
