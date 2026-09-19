"""Persistence layer: SQLAlchemy engine, session factory, and schema bootstrap.

SQLite is the single-town deployment profile (`docs/data-model.md` §0): one
file, easy to back up, no server to run on modest municipal hardware. The same
ORM layer runs on PostgreSQL for multi-town deployments by changing
``AQUAOPS_DB_URL`` — that portability is NFR "Scalability" in the Unit 3
specification, and nothing above this module knows which engine is in use.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "aquaops.db"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


class Base(DeclarativeBase):
    """Declarative base for every ORM entity in `app.models`."""


def database_url() -> str:
    """Resolve the connection string, defaulting to the local SQLite file."""
    return os.environ.get("AQUAOPS_DB_URL", f"sqlite:///{DEFAULT_DB_PATH}")


def configure(url: str | None = None) -> Engine:
    """Create (or replace) the engine and session factory.

    Tests call this with a temporary SQLite path so each test module gets an
    isolated database without touching the developer's working file.
    """
    global _engine, _session_factory
    target = url or database_url()
    connect_args = {"check_same_thread": False} if target.startswith("sqlite") else {}
    _engine = create_engine(target, connect_args=connect_args, future=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    return _engine if _engine is not None else configure()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is closed after the request."""
    if _session_factory is None:
        configure()
    assert _session_factory is not None  # narrowed for type checkers
    with _session_factory() as session:
        yield session


def session_scope() -> Session:
    """A session for scripts and startup hooks (caller closes it)."""
    if _session_factory is None:
        configure()
    assert _session_factory is not None
    return _session_factory()


def init_db() -> None:
    """Create any missing tables. Idempotent, so startup can always call it."""
    from app import models  # noqa: F401  (import registers the mappers)

    Base.metadata.create_all(get_engine())
