"""Agent skills: Claude-Code-style SKILL.md folders a user installs, that the
agent can discover, read, and run.

A skill is a directory `{skills_root}/{name}/` containing a `SKILL.md`
(optional YAML frontmatter with `name` + `description`, then instructions)
plus any scripts/resources. Progressive disclosure, like Claude Code:

  1. The names + descriptions of installed skills are injected into the system
     prompt each turn (`skills_catalog_text`) so the model knows they exist.
  2. When a skill is relevant, the model calls `use_skill(name)`. That returns
     the full SKILL.md AND stages the skill's files into the session workspace
     at `skills/<name>/`, so the model can run the bundled scripts with the
     existing (sandboxed) run_command tool.

The skills root is a ContextVar (D28): each request sets its own per-user root
so concurrent users never see each other's skills.
"""

import os
import shutil
from contextvars import ContextVar

from .. import workspace
from ..registry import Tool, registry

_SKILLS_ROOT: ContextVar[str] = ContextVar("skills_root", default=".harness/skills")
_SKILL_MD = "SKILL.md"
_MAX_SKILL_MD = 20_000


def set_skills_root(path: str) -> None:
    """Point skill discovery/use at a directory (per-user root). Called at the
    start of each request by the server, not through tool signatures."""
    _SKILLS_ROOT.set(path)


def _safe_name(name: str) -> str:
    """A skill name is a single path component: strip any directory parts and
    confine to a safe charset so `use_skill('../x')` can't escape the root."""
    base = os.path.basename((name or "").strip())
    return "".join(c for c in base if c.isalnum() or c in "._- ").strip()[:64]


def _parse_frontmatter(text: str) -> tuple[str, str]:
    """Pull (name, description) out of a SKILL.md's leading `--- ... ---` YAML
    block. Falls back to ('', '') when absent — the caller supplies defaults.
    Deliberately tiny: only the two flat keys we use, no YAML dependency."""
    name = desc = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key, val = key.strip().lower(), val.strip().strip("'\"")
                if key == "name":
                    name = val
                elif key == "description":
                    desc = val
    return name, desc


def skill_meta(skill_dir: str, fallback_name: str) -> dict:
    """(name, description) for one installed skill directory."""
    md_path = os.path.join(skill_dir, _SKILL_MD)
    name = desc = ""
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            name, desc = _parse_frontmatter(f.read(4096))
    except OSError:
        pass
    return {"name": name or fallback_name, "description": desc}


def list_installed_skills(root: str | None = None) -> list[dict]:
    """Every installed skill under the root: [{name, description}] sorted by
    name. A directory only counts as a skill if it has a SKILL.md."""
    root = root if root is not None else _SKILLS_ROOT.get()
    out = []
    if os.path.isdir(root):
        for entry in sorted(os.listdir(root)):
            d = os.path.join(root, entry)
            if os.path.isfile(os.path.join(d, _SKILL_MD)):
                out.append(skill_meta(d, entry))
    return out


def skills_catalog_text(root: str | None = None) -> str:
    """A block appended to the system prompt so the model can discover skills.
    Empty string when none are installed (adds nothing to the prompt)."""
    skills = list_installed_skills(root)
    if not skills:
        return ""
    lines = [
        "\n\n## Installed skills",
        "You have these user-installed skills available. When one is clearly "
        "relevant to the request, call `use_skill(name)` to load its full "
        "instructions and files before proceeding:",
    ]
    for s in skills:
        lines.append(f"- {s['name']}: {s['description'] or '(no description)'}")
    return "\n".join(lines)


def use_skill(name: str) -> str:
    """Load an installed skill: return its SKILL.md and stage its files into
    the session workspace at skills/<name>/ so its scripts can be run."""
    safe = _safe_name(name)
    if not safe:
        return f"Error: invalid skill name {name!r}"
    skill_dir = os.path.join(_SKILLS_ROOT.get(), safe)
    md_path = os.path.join(skill_dir, _SKILL_MD)
    if not os.path.isfile(md_path):
        installed = ", ".join(s["name"] for s in list_installed_skills()) or "(none)"
        return f"Error: no installed skill named {safe!r}. Installed: {installed}"
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            body = f.read(_MAX_SKILL_MD)
    except OSError as exc:
        return f"Error reading skill {safe!r}: {exc}"

    # Stage the skill's files into the session workspace so the (sandboxed)
    # run_command tool can execute bundled scripts. resolve() confines the
    # destination to the workspace root.
    staged = ""
    try:
        dest = workspace.resolve(os.path.join("skills", safe))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copytree(skill_dir, dest, dirs_exist_ok=True)
        staged = (f"\n\n---\n[The skill's files are staged in your workspace at "
                  f"`skills/{safe}/`. Run its scripts from there with run_command "
                  f"as the instructions above describe.]")
    except Exception as exc:  # staging is best-effort; the instructions still help
        staged = f"\n\n---\n[Note: could not stage skill files ({exc}); follow the instructions above.]"

    return body + staged


registry.register(
    Tool(
        name="use_skill",
        description=(
            "Load an installed skill by name: returns its full SKILL.md "
            "instructions and stages its files into your workspace so you can "
            "run its scripts. Use when an installed skill fits the task."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The installed skill's name."}
            },
            "required": ["name"],
        },
        handler=use_skill,
        risk="safe",
    )
)
