"""Engine construction for the relational store (D29).

One connection string (HARNESS_DB_URL) picks the backend: the default
`sqlite:///.harness/harness.db` needs zero setup; point it at
`postgresql+psycopg://...` and users/sessions/usage live in Postgres with
no code change. Tables are created on first connect -- the schema is small
and additive, so create_all is the whole "migration system" for now.
"""

import os

from sqlalchemy import Engine, create_engine

from .models import Base

_SQLITE_PREFIX = "sqlite:///"


def make_engine(db_url: str) -> Engine:
    """Create the engine and ensure the schema exists. For a file-backed
    SQLite URL the parent directory is created first (SQLite won't)."""
    if db_url.startswith(_SQLITE_PREFIX):
        path = db_url[len(_SQLITE_PREFIX):]
        directory = os.path.dirname(path)
        if directory and path != ":memory:":
            os.makedirs(directory, exist_ok=True)
    from sqlalchemy.pool import NullPool
    if db_url.startswith(_SQLITE_PREFIX):
        engine = create_engine(db_url, poolclass=NullPool)
    else:
        engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    return engine
