"""The permission layer — the single chokepoint every action passes through.

The behavior is driven by the configured mode, so the same agent can run fully
autonomously in a sandbox or ask-first for a whole company, with no code change:

    auto       -> allow everything
    ask        -> allow safe actions; ask a human about write/dangerous ones
    allowlist  -> allow safe + write automatically; deny dangerous (no prompts)

`check` returns one of: "allow", "ask", or "deny". The caller (orchestrator)
decides how to obtain the human answer when the result is "ask".
"""

from tools.registry import Tool

ALLOW = "allow"
ASK = "ask"
DENY = "deny"


def check(tool: Tool, arguments: dict, mode: str) -> str:
    if mode == "auto":
        return ALLOW

    risk = getattr(tool, "risk", "safe")

    if mode == "allowlist":
        return ALLOW if risk in ("safe", "write") else DENY

    # Default mode: "ask".
    if risk == "safe":
        return ALLOW
    return ASK
