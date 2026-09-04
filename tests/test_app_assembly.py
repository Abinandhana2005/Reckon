"""How the process is put together: which routes exist, and what serves the UI."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.db.base import get_db
from tests.support import (  # noqa: F401
    _shared_database,
    db_factory,
)


def paths(application) -> set[str]:
    return {route.path for route in application.routes if hasattr(route, "methods")}


def test_the_demo_controls_can_be_switched_off_for_a_deployment():
    with_dev = paths(create_app(dev_endpoints=True))
    without_dev = paths(create_app(dev_endpoints=False))

    assert "/api/dev/simulate" in with_dev
    assert not any(path.startswith("/api/dev") for path in without_dev)


def test_switching_off_the_demo_controls_leaves_the_product_intact():
    without_dev = paths(create_app(dev_endpoints=False))

    assert {"/api/brief", "/api/brief/ack", "/api/watchlist", "/health"} <= without_dev


def test_the_api_is_served_without_a_frontend_present(tmp_path, db_factory):
    """The backend has to run before the frontend exists, and after it is removed."""
    application = create_app(web_dist=tmp_path / "absent")
    application.dependency_overrides[get_db] = _session_from(db_factory)

    body = _client(application).get("/health").json()

    assert body["status"] == "ok"
    assert body["frontend"] is False


def test_a_built_frontend_is_served_from_the_same_origin(tmp_path, db_factory):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<title>Reckon</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    application = create_app(web_dist=dist)
    application.dependency_overrides[get_db] = _session_from(db_factory)

    client = _client(application)

    assert client.get("/health").json()["frontend"] is True
    assert "Reckon" in client.get("/").text
    assert "console.log" in client.get("/assets/app.js").text
    # A deep link has to survive a refresh, so unknown paths fall back to the
    # app shell rather than 404.
    assert "Reckon" in client.get("/symbols/NWTC").text


def test_the_api_still_wins_over_the_frontend_catch_all(tmp_path, db_factory):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>Reckon</title>", encoding="utf-8")

    application = create_app(web_dist=dist)
    application.dependency_overrides[get_db] = _session_from(db_factory)

    # Would return the HTML shell if the catch-all had been registered first.
    assert _client(application).get("/api/brief").status_code == 401


def _client(application) -> TestClient:
    # Built without the context manager on purpose: entering it runs the startup
    # hook, which would create and seed the real configured database.
    return TestClient(application)


def _session_from(factory):
    def override():
        with factory() as session:
            yield session

    return override
