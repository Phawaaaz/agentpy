"""Hybrid knowledge engine (RAG): index a repo's docs and code into two
separate vector stores and let the agent search them before it writes code.

Why two stores? (from "Building an Autonomous AI Developer") — documentation
and code embeddings cluster differently, so mixing them means a search for
"checkout error" can surface a CSS file instead of the architecture guide. Kept
apart, the agent builds a mental model from the docs first, then looks at the
code.

Embeddings are deliberately dependency-free and offline: a normalized
bag-of-words (log term frequency) with cosine similarity. No embedding API, no
model download, no keys — good enough for retrieval and trivially testable. The
index is two JSON files per session, so it survives across turns. The index
root is a ContextVar (D28), set per request like the workspace root.
"""

import json
import math
import os
import re
from contextvars import ContextVar

from ..registry import Tool, registry

_INDEX_ROOT: ContextVar[str | None] = ContextVar("knowledge_index_root", default=None)

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
_DOC_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc"}
# Common docs that ship without an extension.
_DOC_NAMES = {"readme", "changelog", "contributing", "license", "notice", "authors"}
_CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb",
             ".php", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".css", ".scss",
             ".html", ".vue", ".svelte", ".sh", ".sql", ".yaml", ".yml", ".json", ".toml"}
_SKIP_DIRS = {".git", "node_modules", "dist", "build", ".rag", "__pycache__",
              ".venv", "venv", ".next", "vendor", "target"}
_MAX_FILE_BYTES = 400_000
_CHUNK_LINES = 40


def set_knowledge_root(path: str | None) -> None:
    """Point search_knowledge at a session's index directory (holds
    docs.json / code.json). Set per request by the server; None = no index."""
    _INDEX_ROOT.set(path)


def _embed(text: str) -> dict:
    """A sparse bag-of-words vector: token -> 1 + log(count)."""
    counts: dict[str, int] = {}
    for tok in _TOKEN.findall(text.lower()):
        counts[tok] = counts.get(tok, 0) + 1
    return {t: 1.0 + math.log(n) for t, n in counts.items()}


def _cosine(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _chunks(text: str):
    lines = text.splitlines()
    for i in range(0, len(lines), _CHUNK_LINES):
        block = "\n".join(lines[i:i + _CHUNK_LINES])
        if block.strip():
            yield i + 1, block  # 1-based start line


def build_index(source_dir: str, index_root: str) -> dict:
    """Walk source_dir, split files into docs vs code, chunk + embed each, and
    write index_root/docs.json and code.json. Returns {docs, code} chunk counts."""
    docs, code = [], []
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            stem = os.path.splitext(fn)[0].lower()
            if ext in _DOC_EXT or (not ext and stem in _DOC_NAMES):
                bucket = docs
            elif ext in _CODE_EXT:
                bucket = code
            else:
                continue
            fp = os.path.join(root, fn)
            try:
                if os.path.getsize(fp) > _MAX_FILE_BYTES:
                    continue
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            rel = os.path.relpath(fp, source_dir)
            for start, block in _chunks(text):
                bucket.append({"file": rel, "line": start, "text": block, "vec": _embed(block)})

    os.makedirs(index_root, exist_ok=True)
    with open(os.path.join(index_root, "docs.json"), "w", encoding="utf-8") as f:
        json.dump(docs, f)
    with open(os.path.join(index_root, "code.json"), "w", encoding="utf-8") as f:
        json.dump(code, f)
    return {"docs": len(docs), "code": len(code)}


def _load(index_root: str, name: str) -> list:
    try:
        with open(os.path.join(index_root, f"{name}.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def has_index(index_root: str | None) -> bool:
    return bool(index_root) and os.path.isfile(os.path.join(index_root, "docs.json"))


def search(query: str, kind: str = "both", k: int = 5, index_root: str | None = None) -> str:
    root = index_root if index_root is not None else _INDEX_ROOT.get()
    if not has_index(root):
        return ("No knowledge index for this session. (Clone a repo to build "
                "one, then retry.)")
    qv = _embed(query)
    buckets = []
    if kind in ("docs", "both"):
        buckets += [("docs", c) for c in _load(root, "docs")]
    if kind in ("code", "both"):
        buckets += [("code", c) for c in _load(root, "code")]
    scored = sorted(
        ((_cosine(qv, c["vec"]), tag, c) for tag, c in buckets),
        key=lambda x: x[0], reverse=True,
    )
    hits = [s for s in scored if s[0] > 0][:k]
    if not hits:
        return f"No matches for {query!r} in the {kind} index."
    out = []
    for score, tag, c in hits:
        snippet = c["text"] if len(c["text"]) <= 800 else c["text"][:800] + " …"
        out.append(f"[{tag}] {c['file']}:{c['line']}  (score {score:.2f})\n{snippet}")
    return "\n\n---\n\n".join(out)


def search_knowledge(query: str, kind: str = "both", k: int = 5) -> str:
    return search(query, kind=kind, k=int(k) if str(k).isdigit() else 5)


registry.register(
    Tool(
        name="search_knowledge",
        description=(
            "Search the session's indexed repository BEFORE writing code. Two "
            "stores: 'docs' (how a feature should work) and 'code' (how it "
            "currently works); 'both' searches each. Returns the most relevant "
            "file chunks with their path and line. Use it to build context on a "
            "bug before editing."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, e.g. 'checkout total calculation'."},
                "kind": {"type": "string", "description": "'docs', 'code', or 'both' (default)."},
                "k": {"type": "integer", "description": "How many results (default 5)."},
            },
            "required": ["query"],
        },
        handler=search_knowledge,
        risk="safe",
    )
)
