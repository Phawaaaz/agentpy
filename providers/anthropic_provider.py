"""Anthropic (Claude) provider.

Anthropic's API differs from OpenAI's: the system prompt is a separate argument,
tools use `input_schema`, and tool *results* are folded into user turns as
content blocks. This class translates our neutral (OpenAI-style) history into
that shape on the way in, and normalizes Claude's reply on the way out — so the
orchestrator never has to know which provider it's talking to.
"""

import json

from anthropic import Anthropic

from .base import Provider, Response, ToolCall, Usage


class AnthropicProvider(Provider):
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = Anthropic(api_key=api_key)

    def complete(self, messages: list[dict], tools: list[dict]) -> Response:
        system_text, native_messages = self._translate_messages(messages)
        native_tools = self._translate_tools(tools)

        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": native_messages,
        }
        if system_text:
            kwargs["system"] = system_text
        if native_tools:
            kwargs["tools"] = native_tools

        message = self.client.messages.create(**kwargs)

        # Split Claude's reply into text and tool-use blocks.
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        neutral_tool_calls: list[dict] = []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input or {})
                )
                neutral_tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input or {}),
                        },
                    }
                )

        text = "".join(text_parts)
        assistant_message: dict = {"role": "assistant", "content": text}
        if neutral_tool_calls:
            assistant_message["tool_calls"] = neutral_tool_calls

        usage = None
        if getattr(message, "usage", None) is not None:
            usage = Usage(
                prompt_tokens=message.usage.input_tokens or 0,
                completion_tokens=message.usage.output_tokens or 0,
            )

        return Response(
            text=text or None,
            tool_calls=tool_calls,
            assistant_message=assistant_message,
            usage=usage,
        )

    @staticmethod
    def _translate_tools(tools: list[dict]) -> list[dict]:
        native = []
        for spec in tools:
            fn = spec["function"]
            native.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn["parameters"],
                }
            )
        return native

    @staticmethod
    def _translate_messages(messages: list[dict]) -> tuple[str, list[dict]]:
        """Neutral (OpenAI-style) history -> (system_text, Anthropic messages)."""
        system_parts: list[str] = []
        out: list[dict] = []

        for msg in messages:
            role = msg.get("role")

            if role == "system":
                system_parts.append(msg.get("content", ""))

            elif role == "user":
                out.append({"role": "user", "content": msg.get("content", "")})

            elif role == "assistant":
                blocks: list[dict] = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg.get("tool_calls", []):
                    try:
                        tool_input = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        tool_input = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["function"]["name"],
                            "input": tool_input,
                        }
                    )
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})

            elif role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": msg["tool_call_id"],
                    "content": msg.get("content", ""),
                }
                # Anthropic wants tool results inside a user turn; coalesce
                # consecutive results from the same assistant turn together.
                prev = out[-1] if out else None
                if (
                    prev
                    and prev["role"] == "user"
                    and isinstance(prev["content"], list)
                    and prev["content"]
                    and prev["content"][0].get("type") == "tool_result"
                ):
                    prev["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})

        return "\n".join(p for p in system_parts if p), out
