"""Fixtures for the backend tests.

Kept out of conftest.py so the domain tests that were already passing keep the
exact environment they were written against.

Market data is seeded once per test session and shared, because it is read-only
reference data. Isolation between tests comes from each one getting its own
guest user, which is also how the product isolates real users.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.db.base import Base, get_db
from app.db.seed import seed


def _memory_engine():
    # StaticPool keeps every connection on the same in-memory database; the
    # default pool would hand each one its own empty one.
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _build(seeded: bool = True):
    engine = _memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    if seeded:
        with factory() as db:
            seed(db)
    return engine, factory


@pytest.fixture(scope="session")
def _shared_database():
    return _build()


@pytest.fixture
def db_factory(_shared_database) -> sessionmaker:
    return _shared_database[1]


@pytest.fixture
def db(db_factory) -> Iterator[Session]:
    with db_factory() as session:
        yield session


@pytest.fixture
def fresh_database():
    """An isolated database, for tests that mutate market data."""
    return _build()[1]


def _client_for(factory: sessionmaker) -> TestClient:
    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    # Constructed without the context manager on purpose: entering it would run
    # the startup hook, which seeds the real configured database.
    return TestClient(app)


@pytest.fixture
def client(db_factory) -> Iterator[TestClient]:
    test_client = _client_for(db_factory)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def fresh_client(fresh_database) -> Iterator[TestClient]:
    test_client = _client_for(fresh_database)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


def session_headers(client: TestClient) -> dict[str, str]:
    token = client.post("/api/session").json()["session_token"]
    return {"X-Session-Token": token}


def watch_all(client: TestClient, headers: dict[str, str], sessions_ago: int = 1) -> list[str]:
    symbols = [s["symbol"] for s in client.get("/api/symbols").json()["symbols"]]
    for symbol in symbols:
        client.post(
            "/api/watchlist",
            json={"symbol": symbol, "anchor_sessions_ago": sessions_ago},
            headers=headers,
        )
    return symbols
