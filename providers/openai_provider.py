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
