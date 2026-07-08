"""Conversation state and context management.

Owns the message history (a Single Responsibility split out of the orchestrator)
and keeps it from overflowing the model's context window. When the estimated
size crosses a budget, the oldest messages are folded into a running summary
while recent turns are kept verbatim.

The summarizer is *injected* (Dependency Inversion), so this module never depends
on a concrete model provider and stays testable with a fake.
"""

import json
from typing import Callable

# Given (previous_summary, messages_to_fold) -> a new combined summary string.
Summarizer = Callable[[str, list[dict]], str]


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate (~4 chars/token). Good enough to trigger compaction."""
    return len(json.dumps(messages)) // 4


class Conversation:
    def __init__(
        self,
        system_prompt: str,
        max_context_tokens: int = 100_000,
        keep_recent_messages: int = 20,
        summarizer: Summarizer | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.max_context_tokens = max_context_tokens
        self.keep_recent_messages = keep_recent_messages
        self.summarizer = summarizer
        self.messages: list[dict] = []  # everything except the system prompt
        self.summary: str = ""  # running summary of folded-away messages

    def add(self, message: dict) -> None:
        self.messages.append(message)

    def to_list(self) -> list[dict]:
        """The full message list to send to a provider (system prompt first)."""
        system_content = self.system_prompt
        if self.summary:
            system_content += "\n\n## Summary of earlier conversation\n" + self.summary
        return [{"role": "system", "content": system_content}, *self.messages]

    def estimated_tokens(self) -> int:
        return estimate_tokens(self.to_list())

    def maybe_compact(self) -> bool:
        """Fold old messages into the summary if over budget. Returns True if it did."""
        if self.summarizer is None:
            return False
        if self.estimated_tokens() <= self.max_context_tokens:
            return False

        cut = self._safe_cut_index()
        if cut <= 0:
            return False

        older = self.messages[:cut]
        self.summary = self.summarizer(self.summary, older)
        self.messages = self.messages[cut:]
        return True

    def _safe_cut_index(self) -> int:
        """Where to split old vs. recent without orphaning a tool result.

        We keep the last `keep_recent_messages`, then slide the cut forward past
        any leading `tool` messages so the kept slice never starts with a tool
        result whose assistant call was folded away.
        """
        cut = len(self.messages) - self.keep_recent_messages
        if cut <= 0:
            return 0
        while cut < len(self.messages) and self.messages[cut].get("role") == "tool":
            cut += 1
        return cut

    # --- persistence support (used by store/) ---

    def snapshot(self) -> dict:
        return {
            "system_prompt": self.system_prompt,
            "summary": self.summary,
            "messages": self.messages,
        }

    def restore(self, state: dict) -> None:
        self.system_prompt = state.get("system_prompt", self.system_prompt)
        self.summary = state.get("summary", "")
        self.messages = state.get("messages", [])


def make_provider_summarizer(provider, max_chars: int = 2000) -> Summarizer:
    """Build a summarizer backed by a Provider (wired at the edge, not here).

    It asks the model to compress the folded messages into a compact, factual
    summary that preserves decisions, file paths, and open tasks.
    """

    def summarize(previous_summary: str, messages: list[dict]) -> str:
        instruction = (
            "You are compacting a long agent conversation to save context. "
            "Produce a concise, factual summary that preserves: the user's goals, "
            "key decisions, important file paths and identifiers, results of tool "
            "calls, and anything still in progress. Omit chit-chat. Be terse."
        )
        payload = {
            "previous_summary": previous_summary,
            "messages_to_summarize": messages,
        }
        prompt_messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(payload)[: max_chars * 8]},
        ]
        response = provider.complete(prompt_messages, tools=[])
        summary = (response.text or "").strip()
        return summary[: max_chars * 4] if summary else previous_summary

    return summarize
