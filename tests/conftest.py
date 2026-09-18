"""Shared fixtures: an isolated SQLite database and a seeded API client.

Every test gets its own database file, so a test that ingests a file or plans a
week cannot leak state into the next one. The seeding path is the same one the
application uses at startup, so the fixtures exercise production behaviour
rather than a test-only shortcut.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import configure, init_db, session_scope
from app.services.case import ensure_case_system
from app.services.plan import sync_rules


@pytest.fixture(autouse=True)
def isolated_evidence_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep evaluation reports out of the committed evidence folder.

    A test run produces synthetic evaluation reports; if they landed in
    ``docs/evidence/`` they would sit next to real evidence and be impossible to
    tell apart afterwards.
    """
    monkeypatch.setenv("AQUAOPS_EVIDENCE_DIR", str(tmp_path / "evidence"))


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'aquaops-test.db'}"


@pytest.fixture()
def seeded(db_url: str) -> Iterator[Session]:
    """A configured database with the case system and rule catalogue loaded."""
    os.environ["AQUAOPS_DB_URL"] = db_url
    configure(db_url)
    init_db()
    session = session_scope()
    ensure_case_system(session)
    sync_rules(session)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_url: str) -> Iterator[TestClient]:
    """A TestClient wired to a fresh, empty database.

    The application's own startup hook seeds it, which is exactly the path a
    first `uvicorn app.main:app` takes on a fresh clone.
    """
    os.environ["AQUAOPS_DB_URL"] = db_url
    configure(db_url)
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
