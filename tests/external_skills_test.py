"""Tests for pipeline/external_skills.py: loading skills.json and building
prompts from user-defined templates. No key, no network.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.external_skills import ExternalSkill, load_external_skills


def test_missing_file_returns_empty():
    assert load_external_skills("/nonexistent/skills.json") == {}
    print("  missing file -> {} OK")


def test_load_and_parse():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "skills.json")
        with open(path, "w") as f:
            json.dump(
                {
                    "skills": {
                        "style-check": {
                            "description": "Check style",
                            "prompt": "Check style.\n\nTASK:\n{task}\n\nDIFF:\n{diff_stat}",
                        },
                        "no-description": {"prompt": "{task}"},
                    }
                },
                f,
            )
        skills = load_external_skills(path)
        assert set(skills) == {"style-check", "no-description"}
        assert skills["style-check"].description == "Check style"
        assert skills["no-description"].description == ""
        print("  load and parse OK")


def test_build_substitutes_placeholders():
    skill = ExternalSkill(name="x", description="d", prompt_template="TASK: {task}\nDIFF: {diff_stat}\nEND")
    result = skill.build("do the thing", "1 file changed")
    assert result == "TASK: do the thing\nDIFF: 1 file changed\nEND"
    print("  placeholder substitution OK")


def test_build_is_robust_to_unrelated_braces():
    """A user's prompt might legitimately contain other {braces} (JSON
    examples, code) -- build() must not choke on them the way str.format
    would (KeyError / IndexError on unrecognized fields)."""
    skill = ExternalSkill(
        name="x", description="d",
        prompt_template='TASK: {task}\nExample output: {"key": "value", "other": {nested}}',
    )
    result = skill.build("my task", "diff")
    assert result == 'TASK: my task\nExample output: {"key": "value", "other": {nested}}'
    print("  robust to unrelated braces OK")


def test_default_prompt_template_when_missing():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "skills.json")
        with open(path, "w") as f:
            json.dump({"skills": {"bare": {}}}, f)
        skills = load_external_skills(path)
        assert skills["bare"].build("hello", "diff") == "hello"
        print("  default prompt template (bare {task}) OK")


def main():
    test_missing_file_returns_empty()
    test_load_and_parse()
    test_build_substitutes_placeholders()
    test_build_is_robust_to_unrelated_braces()
    test_default_prompt_template_when_missing()
    print("EXTERNAL SKILLS TESTS PASSED")


if __name__ == "__main__":
    main()
