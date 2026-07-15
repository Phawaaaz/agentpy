"""Planning tool: the model maintains an explicit step-by-step plan as an
ordered checklist instead of only holding it implicitly in its own
reasoning -- closes the "planning and decomposition" gap from
https://www.langchain.com/blog/the-anatomy-of-an-agent-harness.

In-memory only (not persisted -- the `memory` tool is for anything that
needs to survive a restart): one plan per *execution context* (D28) --
a ContextVar, so concurrent sessions each hold their own plan, and a
delegated sub-agent running in a copied context (multiagent/coordinator.py)
gets a fresh plan that never overwrites the coordinator's. Reset at the
start of a new conversation via `reset_plan()` (interfaces/cli.py calls
this from Session.reset() and after /load).
"""

from contextvars import ContextVar

from ..registry import Tool, registry

_STATUSES = ("pending", "in_progress", "completed")
# The current context's plan. The default is None (not a shared mutable
# list -- a ContextVar default is one object shared by every context that
# never set it); writers always .set() a fresh list.
_plan: ContextVar[list[dict] | None] = ContextVar("plan", default=None)


def reset_plan() -> None:
    _plan.set([])


def _current() -> list[dict]:
    return _plan.get() or []


def _render() -> str:
    plan = _current()
    if not plan:
        return "(no plan set)"
    marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
    return "\n".join(f"{i}. {marks[item['status']]} {item['step']}" for i, item in enumerate(plan, 1))


def todo_write(steps: list[dict]) -> str:
    if not isinstance(steps, list) or not steps:
        return "Error: steps must be a non-empty list of {step, status} objects"
    validated = []
    for item in steps:
        if not isinstance(item, dict) or "step" not in item:
            return f"Error: each item needs a 'step' string; got {item!r}"
        status = item.get("status", "pending")
        if status not in _STATUSES:
            return f"Error: status must be one of {_STATUSES}, got {status!r}"
        validated.append({"step": str(item["step"]), "status": status})
    _plan.set(validated)
    return "Plan updated:\n" + _render()


def todo_read() -> str:
    return _render()


registry.register(
    Tool(
        name="todo_write",
        description=(
            "Set or update your step-by-step plan for the current task as a checklist. "
            "Call it when you start a multi-step task to lay out the steps, and again "
            "whenever a step's status changes (mark a step in_progress before starting "
            "it, completed when done). Replaces the whole plan each call -- pass every "
            "step, not just the one that changed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "The full ordered list of steps.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "string", "description": "Short description of this step."},
                            "status": {
                                "type": "string",
                                "enum": list(_STATUSES),
                                "description": "pending, in_progress, or completed. Defaults to pending.",
                            },
                        },
                        "required": ["step"],
                    },
                }
            },
            "required": ["steps"],
        },
        handler=todo_write,
        risk="safe",
    )
)

registry.register(
    Tool(
        name="todo_read",
        description="Show your current step-by-step plan and each step's status.",
        parameters={"type": "object", "properties": {}},
        handler=todo_read,
        risk="safe",
    )
)
