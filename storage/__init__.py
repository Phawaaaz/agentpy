"""Storage — the relational persistence layer (D29): users, sessions, and
per-model-call usage accounting behind one HARNESS_DB_URL connection string.
SQLite by default (zero ops, one file under .harness/), Postgres by changing
the URL — no code change. Replaces the JSON-file SessionStore/UserStore
(D12/D22) behind the same public interfaces.
"""
