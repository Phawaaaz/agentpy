"""Tests for the web_search tool (engine/builtin/search.py). urllib.request
.urlopen is monkeypatched so this stays in the "no key, no network" tier --
the same tier every other test in this repo lives in.
"""

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.builtin.search import build_search_tool


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(fn):
    original = urllib.request.urlopen
    urllib.request.urlopen = fn
    return original


def _restore_urlopen(original):
    urllib.request.urlopen = original


def test_tool_shape():
    tool = build_search_tool("fake-key")
    assert tool.name == "web_search"
    assert tool.risk == "dangerous"
    assert "query" in tool.parameters["properties"]
    print("  web_search tool shape (name/risk/params) OK")


def test_formats_results_with_title_url_content():
    tool = build_search_tool("fake-key")
    payload = {
        "results": [
            {"title": "Python docs", "url": "https://docs.python.org", "content": "The official docs."},
        ]
    }
    original = _patch_urlopen(lambda *a, **k: _FakeResponse(payload))
    try:
        result = tool.handler(query="python")
    finally:
        _restore_urlopen(original)
    assert "Python docs" in result
    assert "https://docs.python.org" in result
    assert "The official docs." in result
    print("  formats title/url/content for each result OK")


def test_includes_answer_when_present():
    tool = build_search_tool("fake-key")
    payload = {"answer": "42", "results": [{"title": "t", "url": "u", "content": "c"}]}
    original = _patch_urlopen(lambda *a, **k: _FakeResponse(payload))
    try:
        result = tool.handler(query="the answer to everything")
    finally:
        _restore_urlopen(original)
    assert result.startswith("Answer: 42")
    print("  includes the direct answer when Tavily provides one OK")


def test_no_results():
    tool = build_search_tool("fake-key")
    original = _patch_urlopen(lambda *a, **k: _FakeResponse({"results": []}))
    try:
        result = tool.handler(query="asdkjaslkdjasldkjasldjk")
    finally:
        _restore_urlopen(original)
    assert result == "(no results)"
    print("  empty results -> '(no results)' OK")


def test_network_error_returns_string_not_raise():
    tool = build_search_tool("fake-key")

    def boom(*a, **k):
        raise OSError("connection refused")

    original = _patch_urlopen(boom)
    try:
        result = tool.handler(query="python")
    finally:
        _restore_urlopen(original)
    assert result.startswith("Error searching for")
    print("  network failure returns an error string instead of raising OK")


def test_max_results_is_clamped():
    tool = build_search_tool("fake-key")
    captured = {}

    def capture(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse({"results": []})

    original = _patch_urlopen(capture)
    try:
        tool.handler(query="python", max_results=999)
    finally:
        _restore_urlopen(original)
    assert captured["body"]["max_results"] == 10
    print("  max_results is clamped to the API's 1-10 range OK")


_DDG_HTML = """
<a class="result__a" href="https://docs.python.org">Python docs</a>
<div class="result__snippet">The official documentation.</div>
"""


class _FakeHTMLResponse:
    def __init__(self, html: str):
        self._body = html.encode("utf-8")

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_no_key_falls_back_to_duckduckgo():
    tool = build_search_tool(api_key=None)
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        return _FakeHTMLResponse(_DDG_HTML)

    original = _patch_urlopen(fake_urlopen)
    try:
        result = tool.handler(query="python")
    finally:
        _restore_urlopen(original)
    assert "duckduckgo.com" in seen["url"], f"expected the DDG endpoint, got {seen['url']}"
    assert "Python docs" in result and "https://docs.python.org" in result
    print("  no API key -> DuckDuckGo fallback path used OK")


def test_key_uses_tavily_not_duckduckgo():
    tool = build_search_tool(api_key="fake-key")
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        return _FakeResponse({"results": [{"title": "t", "url": "u", "content": "c"}]})

    original = _patch_urlopen(fake_urlopen)
    try:
        tool.handler(query="python")
    finally:
        _restore_urlopen(original)
    assert "tavily.com" in seen["url"], f"expected the Tavily endpoint, got {seen['url']}"
    print("  API key set -> Tavily used, DuckDuckGo untouched OK")


def test_fallback_network_error_returns_string_not_raise():
    tool = build_search_tool(api_key=None)

    def boom(*a, **k):
        raise OSError("connection refused")

    original = _patch_urlopen(boom)
    try:
        result = tool.handler(query="python")
    finally:
        _restore_urlopen(original)
    assert result.startswith("Error searching for"), result
    print("  fallback path network failure returns an error string OK")


def main():
    test_tool_shape()
    test_formats_results_with_title_url_content()
    test_includes_answer_when_present()
    test_no_results()
    test_network_error_returns_string_not_raise()
    test_max_results_is_clamped()
    test_no_key_falls_back_to_duckduckgo()
    test_key_uses_tavily_not_duckduckgo()
    test_fallback_network_error_returns_string_not_raise()
    print("SEARCH TESTS PASSED")


if __name__ == "__main__":
    main()
