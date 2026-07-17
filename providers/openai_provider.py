"""OpenAI provider — also drives any OpenAI-compatible endpoint via base_url.

Our internal ("neutral") message format is already OpenAI's chat format, so no
translation is needed here. Point base_url at Ollama, LM Studio, OpenRouter,
Groq, Together, vLLM, etc. and the same code talks to them.
"""

import json

import openai
from openai import OpenAI

from .base import Provider, Response, ToolCall, Usage
from .retry import call_with_retries

# Same transient-failure retry set as the Anthropic adapter, in this SDK's
# own exception types (these cover every OpenAI-compatible endpoint too).
_RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


class OpenAIProvider(Provider):
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
    ):
        self.model = model
        self.temperature = temperature
        # base_url=None => real OpenAI. Set it for any compatible server.
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def complete(self, messages: list[dict], tools: list[dict]) -> Response:
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        completion = call_with_retries(
            lambda: self.client.chat.completions.create(**kwargs), _RETRYABLE
        )
        message = completion.choices[0].message

        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolCall(id=tc.id, name=tc.function.name, arguments=arguments)
            )

        assistant_message: dict = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

        usage = None
        if completion.usage is not None:
            usage = Usage(
                prompt_tokens=completion.usage.prompt_tokens or 0,
                completion_tokens=completion.usage.completion_tokens or 0,
            )

        return Response(
            text=message.content,
            tool_calls=tool_calls,
            assistant_message=assistant_message,
            usage=usage,
        )

    def stream(self, messages: list[dict], tools: list[dict]):
        """Stream the turn: yield ("delta", text) as content tokens arrive,
        then a final ("response", Response). OpenAI streams tool-call
        arguments in fragments, so those are reassembled by index before the
        terminal Response is built (D35)."""
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        def _create():
            try:
                return self.client.chat.completions.create(**kwargs)
            except openai.BadRequestError:
                # Some OpenAI-compatible endpoints reject `stream_options`;
                # drop it and retry so streaming still works (usage may be
                # absent for that turn). A genuinely bad request fails again.
                kwargs.pop("stream_options", None)
                return self.client.chat.completions.create(**kwargs)

        stream = call_with_retries(_create, _RETRYABLE)

        text_parts: list[str] = []
        # index -> {"id", "name", "args"} accumulated across fragments
        tool_frags: dict[int, dict] = {}
        usage = None
        for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = Usage(
                    prompt_tokens=chunk.usage.prompt_tokens or 0,
                    completion_tokens=chunk.usage.completion_tokens or 0,
                )
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                text_parts.append(delta.content)
                yield ("delta", delta.content)
            for tc in delta.tool_calls or []:
                frag = tool_frags.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    frag["id"] = tc.id
                if tc.function and tc.function.name:
                    frag["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    frag["args"] += tc.function.arguments

        text = "".join(text_parts)
        tool_calls: list[ToolCall] = []
        neutral_tool_calls: list[dict] = []
        for _idx, frag in sorted(tool_frags.items()):
            try:
                arguments = json.loads(frag["args"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(ToolCall(id=frag["id"], name=frag["name"], arguments=arguments))
            neutral_tool_calls.append({
                "id": frag["id"], "type": "function",
                "function": {"name": frag["name"], "arguments": frag["args"] or "{}"},
            })

        assistant_message: dict = {"role": "assistant", "content": text}
        if neutral_tool_calls:
            assistant_message["tool_calls"] = neutral_tool_calls

        yield ("response", Response(
            text=text or None,
            tool_calls=tool_calls,
            assistant_message=assistant_message,
            usage=usage,
        ))
