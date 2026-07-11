"""The `delegate` tool: how one agent becomes a coordinator for others.

Delegation is a *tool call*, not new control flow -- from the coordinator's
own loop, calling `delegate` looks exactly like calling `run_command`.
engine/orchestrator.py is untouched; this composes it from the outside, same
as pipeline/runner.py does for stages.

Sub-agents share the coordinator's Registry (via FilteredRegistry, so newly
connected MCP tools are visible too), Config (model, permission_mode,
memory_dir), and approver -- shared memory and consistent permissions come
from reusing the same objects, not from new plumbing. The one thing hidden
from a sub-agent is `delegate` itself: one level of delegation only, the
same constraint Anthropic's own Managed Agents multiagent sessions apply,
so recursion is structurally impossible rather than something a runtime
counter has to catch.
"""

from dataclasses import replace

from config import Config
from context_engine.compaction import Conversation, make_provider_summarizer
from engine.orchestrator import Approver, EventHook, Orchestrator
from engine.registry import Registry, Tool
from multiagent.roles import AgentRole
from providers.base import Provider


class FilteredRegistry(Registry):
    """A live view of `source` with certain tool names hidden."""

    def __init__(self, source: Registry, hidden: set[str]) -> None:
        super().__init__()
        self._source = source
        self._hidden = hidden

    def get(self, name: str) -> Tool | None:
        return None if name in self._hidden else self._source.get(name)

    def all(self) -> list[Tool]:
        return [t for t in self._source.all() if t.name not in self._hidden]

    def specs(self) -> list[dict]:
        return [s for s in self._source.specs() if s["function"]["name"] not in self._hidden]

    def run(self, name: str, arguments: dict) -> str:
        if name in self._hidden:
            return f"Error: unknown tool '{name}'"
        return self._source.run(name, arguments)


def build_delegate_tool(
    provider: Provider,
    registry: Registry,
    base_config: Config,
    roles: dict[str, AgentRole],
    approver: Approver,
    on_event: EventHook | None = None,
) -> Tool:
    """One tool, `delegate`. `approver` is required (not defaulted) so a
    sub-agent's write/dangerous actions are never silently auto-approved
    just because the caller forgot to pass one -- Orchestrator's own default
    approver is "allow everything", which is the wrong failure mode here."""

    sub_registry = FilteredRegistry(registry, hidden={"delegate"})

    def delegate(role: str, task: str) -> str:
        agent_role = roles.get(role)
        if agent_role is None:
            available = ", ".join(sorted(roles)) or "(none configured)"
            return f"Error: unknown role '{role}'. Available roles: {available}"

        sub_config = replace(base_config, system_prompt=agent_role.system_prompt)
        sub_conversation = Conversation(
            sub_config.system_prompt,
            max_context_tokens=sub_config.max_context_tokens,
            keep_recent_messages=sub_config.keep_recent_messages,
            summarizer=make_provider_summarizer(provider),
        )
        sub_agent = Orchestrator(
            provider,
            sub_registry,
            sub_config,
            approver=approver,
            on_event=on_event,
            conversation=sub_conversation,
        )
        try:
            return sub_agent.run(task)
        except Exception as exc:
            return f"Error: sub-agent '{role}' failed: {exc}"

    role_list = "\n".join(f"- {r.name}: {r.description}" for r in roles.values())
    return Tool(
        name="delegate",
        description=(
            "Delegate a self-contained sub-task to a specialized sub-agent and get back its "
            "final answer. The sub-agent has its own tools and its own conversation -- it does "
            "not see this conversation, so give it a complete, self-contained task description. "
            "Available roles:\n" + (role_list or "(none configured)")
        ),
        parameters={
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": sorted(roles),
                    "description": "Which role to delegate to.",
                },
                "task": {
                    "type": "string",
                    "description": "A complete, self-contained task description for the sub-agent.",
                },
            },
            "required": ["role", "task"],
        },
        handler=delegate,
        risk="dangerous",
    )
