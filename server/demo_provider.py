"""A scripted, key-less agent provider for the demo.

Lets the whole demo script run deterministically with NO API key -- for
rehearsal, and as the presentation's fallback if the real model provider is
slow or down. It inspects the latest user message + how many tool results are
already present this turn, and drives the exact tool activity the demo wants
(create a file, read it back, run a sandboxed command, get blocked reading
outside the workspace). It also streams its text token-by-token and prefixes
replies with the active model name so live model-switching is visible.

Enabled with HARNESS_DEMO_FAKE=1 or by picking the "demo/scripted" model.
"""

import json
import time

from providers.base import Provider, Response, ToolCall, Usage


def _tool(cid, name, arguments):
    return Response(
        text=None,
        tool_calls=[ToolCall(id=cid, name=name, arguments=arguments)],
        assistant_message={
            "role": "assistant", "content": "",
            "tool_calls": [{"id": cid, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)}}],
        },
        usage=Usage(prompt_tokens=12, completion_tokens=8),
    )


class DemoProvider(Provider):
    """Deterministic, key-less provider that produces good demo tool activity."""

    def __init__(self, model: str = "demo/scripted", delay: float = 0.03):
        self.model = model
        self.delay = delay  # per-token pacing so streaming is visible on screen

    # --- helpers -----------------------------------------------------------

    def _last_user(self, messages):
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content") or ""
                # Vision turns carry a list of content blocks; pull the text.
                if isinstance(content, list):
                    content = " ".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
                return content.lower()
        return ""

    def _tool_results_since_user(self, messages):
        n = 0
        for m in reversed(messages):
            if m.get("role") == "user":
                break
            if m.get("role") == "tool":
                n += 1
        return n

    def _final(self, text):
        return Response(
            text=text, tool_calls=[],
            assistant_message={"role": "assistant", "content": text},
            usage=Usage(prompt_tokens=20, completion_tokens=len(text) // 4),
        )

    def _script(self, messages):
        """Return the Response for this loop iteration based on the prompt and
        how many tools have already run this turn."""
        user = self._last_user(messages)
        done = self._tool_results_since_user(messages)
        tag = f"[{self.model}] "

        # Money shot: blocked read outside the workspace.
        if "/etc/passwd" in user or ("outside" in user and "workspace" in user):
            if done == 0:
                return _tool("d1", "read_file", {"path": "/etc/passwd"})
            return self._final(tag + "That read was blocked by the workspace sandbox — "
                               "the agent cannot escape its per-session directory. "
                               "This is the isolation boundary working.")

        # Create a file then show its contents.
        if "planet" in user or ("file" in user and ("content" in user or "show" in user or "list" in user)):
            if done == 0:
                planets = "Mercury\nVenus\nEarth\nMars\nJupiter\nSaturn\nUranus\nNeptune"
                return _tool("d1", "write_file", {"path": "planets.txt", "content": planets})
            if done == 1:
                return _tool("d2", "read_file", {"path": "planets.txt"})
            return self._final(tag + "Done. I created `planets.txt` with the eight planets "
                               "and read it back — you can see the contents in the tool card above.")

        # Run a sandboxed command.
        if "command" in user or "run " in user or "sandbox" in user:
            if done == 0:
                return _tool("d1", "run_command", {"command": "echo 'hello from inside the sandbox'; uname -s"})
            return self._final(tag + "That command ran inside the sandbox and returned its output above.")

        # Plain chat (no tools) -- still streams, still shows the model chip.
        return self._final(
            tag + "Hi! I'm Floowpay AI. Ask me to create a file and show its "
            "contents, run a sandboxed command, or try reading /etc/passwd to see the "
            "sandbox block it. Switch my model from the composer any time."
        )

    # --- Provider API ------------------------------------------------------

    def complete(self, messages, tools):
        return self._script(messages)

    def stream(self, messages, tools):
        resp = self._script(messages)
        if resp.text:  # stream the natural-language reply token by token
            for word in resp.text.split(" "):
                time.sleep(self.delay)
                yield ("delta", word + " ")
        yield ("response", resp)
