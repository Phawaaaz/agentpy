"""Tests for the /model command -- Session.switch_model and
interfaces.cli._handle_model_command. Uses a fake build_provider/summarizer
(monkeypatched onto the interfaces.cli module) so this stays in the
"no key, no network" tier like every other test here.
"""

import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interfaces.cli as cli
from config import Config
from providers.base import Provider, Response


class FakeProvider(Provider):
    def __init__(self, model, **_kwargs):
        self.model = model

    def complete(self, messages, tools):
        return Response(text="ok", tool_calls=[], assistant_message={"role": "assistant", "content": "ok"})


def _fake_build_provider(config):
    if config.model == "bad/no-such-model":
        raise ValueError(f"Don't know how to build a provider for model '{config.model}'.")
    return FakeProvider(config.model)


def _fake_summarizer(provider, max_chars=2000):
    def summarize(previous_summary, messages):
        return previous_summary
    return summarize


def _new_session(model="anthropic/claude-opus-4-8") -> cli.Session:
    config = Config(model=model)
    provider = _fake_build_provider(config)
    return cli.Session(config, provider, on_event=lambda kind, *d: None)


def test_switch_model_preserves_history_and_swaps_provider():
    cli.build_provider = _fake_build_provider
    cli.make_provider_summarizer = _fake_summarizer
    session = _new_session()
    session.conversation.add({"role": "user", "content": "hello"})
    session.conversation.add({"role": "assistant", "content": "hi there"})
    old_agent = session.agent
    old_conversation = session.conversation

    session.switch_model("openai/gpt-4o-mini")

    assert session.config.model == "openai/gpt-4o-mini"
    assert isinstance(session.provider, FakeProvider) and session.provider.model == "openai/gpt-4o-mini"
    assert session.conversation is old_conversation  # history object reused, not rebuilt
    assert session.conversation.messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    assert session.agent is not old_agent  # orchestrator rebuilt against the new provider
    print("  switch_model preserves history + swaps provider OK")


def test_switch_model_failure_leaves_session_unchanged():
    cli.build_provider = _fake_build_provider
    cli.make_provider_summarizer = _fake_summarizer
    session = _new_session()
    session.conversation.add({"role": "user", "content": "keep me"})
    old_config, old_provider, old_agent = session.config, session.provider, session.agent

    try:
        session.switch_model("bad/no-such-model")
        raised = False
    except ValueError:
        raised = True

    assert raised
    assert session.config is old_config
    assert session.provider is old_provider
    assert session.agent is old_agent
    assert session.conversation.messages == [{"role": "user", "content": "keep me"}]
    print("  failed switch leaves session state untouched OK")


def test_handle_model_command_no_args_shows_current_model():
    cli.build_provider = _fake_build_provider
    cli.make_provider_summarizer = _fake_summarizer
    session = _new_session(model="anthropic/claude-opus-4-8")

    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._handle_model_command([], session)

    assert "anthropic/claude-opus-4-8" in buf.getvalue()
    print("  /model with no args shows current model OK")


def test_handle_model_command_switches_and_reports_success():
    cli.build_provider = _fake_build_provider
    cli.make_provider_summarizer = _fake_summarizer
    session = _new_session()

    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._handle_model_command(["ollama/llama3.2:3b"], session)

    assert session.config.model == "ollama/llama3.2:3b"
    assert "switched" in buf.getvalue().lower()
    print("  /model <name> switches and reports success OK")


def test_handle_model_command_reports_failure_without_crashing():
    cli.build_provider = _fake_build_provider
    cli.make_provider_summarizer = _fake_summarizer
    session = _new_session()

    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._handle_model_command(["bad/no-such-model"], session)  # must not raise

    assert "failed" in buf.getvalue().lower()
    assert session.config.model != "bad/no-such-model"
    print("  /model <bad-name> reports failure without crashing OK")


def main():
    test_switch_model_preserves_history_and_swaps_provider()
    test_switch_model_failure_leaves_session_unchanged()
    test_handle_model_command_no_args_shows_current_model()
    test_handle_model_command_switches_and_reports_success()
    test_handle_model_command_reports_failure_without_crashing()
    print("MODEL SWITCH TESTS PASSED")


if __name__ == "__main__":
    main()
