"""Named sub-agent roles the coordinator can delegate to.

Loaded from a JSON config file (.harness/roles.json by default) -- the same
external-config pattern as tools/mcp_client.py's server list, so adding a
role is a data change, not a Python change.
"""

import json
import os
from dataclasses import dataclass


@dataclass
class AgentRole:
    name: str
    description: str  # shown to the coordinator so it knows when to delegate here
    system_prompt: str


def load_roles(path: str) -> dict[str, AgentRole]:
    """
    ```json
    {"roles": {
        "researcher": {"description": "...", "system_prompt": "..."},
        "reviewer":   {"description": "...", "system_prompt": "..."}
    }}
    ```
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    roles = {}
    for name, entry in data.get("roles", {}).items():
        roles[name] = AgentRole(
            name=name,
            description=entry.get("description", ""),
            system_prompt=entry.get("system_prompt", ""),
        )
    return roles
