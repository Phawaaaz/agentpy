"""External skills: user-defined prompt templates loaded from JSON -- the
same config pattern as tools/mcp_client.py's MCP servers and
multiagent/roles.py's roles. No file means no external skills; the four
built-in ones (review, verify, test, docs -- see interfaces/cli.py's
_SKILLS, sourced from pipeline/stages.py) are unaffected either way.
"""

import json
import os
from dataclasses import dataclass


@dataclass
class ExternalSkill:
    name: str
    description: str
    prompt_template: str  # may reference {task} and {diff_stat}

    def build(self, task: str, diff_stat: str) -> str:
        # Plain substitution, not str.format() -- a user's prompt template
        # may legitimately contain other `{`/`}` characters (JSON examples,
        # code snippets) that str.format would misinterpret as fields.
        return self.prompt_template.replace("{task}", task).replace("{diff_stat}", diff_stat)


def load_external_skills(path: str) -> dict[str, ExternalSkill]:
    """
    ```json
    {"skills": {
        "style-check": {
            "description": "Review the diff against our style guide",
            "prompt": "Review this change against our style guide.\n\nTASK:\n{task}\n\nCHANGES (--stat):\n{diff_stat}"
        }
    }}
    ```
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    skills = {}
    for name, entry in data.get("skills", {}).items():
        skills[name] = ExternalSkill(
            name=name,
            description=entry.get("description", ""),
            prompt_template=entry.get("prompt", "{task}"),
        )
    return skills
