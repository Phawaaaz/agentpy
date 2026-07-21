"""SQLAlchemy models for the harness's relational store (D29).

Schema (PLAN.md §2): users (with a two-tier admin/user role), sessions
keyed by user, the session's conversation snapshot, and a usage_log with
one row per model call so an admin can answer "who is spending tokens,
how many, on what" (PLAN.md §5.5). Kept deliberately small; workspaces
and long-term memory stay as files on disk.

The conversation is stored as one JSON snapshot per session (the same
shape Conversation.snapshot() has always produced) rather than exploded
into per-message rows — resuming always loads the whole history anyway,
and one column swap keeps the migration from the JSON-file store exact.
"""

import time

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLE_ADMIN = "admin"
ROLE_USER = "user"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_USER)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=time.time)


class Session(Base):
    __tablename__ = "sessions"

    # The CLI's human-visible session id (e.g. "20260715-102508") — kept as
    # the key so /save, /load, and existing session ids survive unchanged.
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=time.time)


class UsageLog(Base):
    __tablename__ = "usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # The user-supplied task text current when this call happened — the
    # "what are they using it for" half of admin monitoring.
    task: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=time.time)


class Skill(Base):
    """An admin-defined prompt preset the web UI offers users (name +
    description + the template text inserted into the composer)."""

    __tablename__ = "skills"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    template: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=time.time)


class MCPServer(Base):
    """An admin-configured MCP tool server. Persisted so the servers an admin
    wires up in the UI reconnect on startup and their tools stay available to
    every session — the same org-wide model as the .harness/mcp.json file.

    `args` and `env` are stored as JSON strings (SQLite has no array/dict
    column); the server layer decodes them into an MCPServerConfig.
    """

    __tablename__ = "mcp_servers"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    transport: Mapped[str] = mapped_column(String(16), nullable=False, default="http")
    command: Mapped[str] = mapped_column(String(512), nullable=False, default="")  # stdio
    args_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # stdio
    env_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # stdio
    url: Mapped[str] = mapped_column(String(512), nullable=False, default="")  # sse/http
    risk: Mapped[str] = mapped_column(String(16), nullable=False, default="")  # override
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=time.time)
