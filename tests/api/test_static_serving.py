"""Tests for serving the built import page (plan 5.8, 5.10, WP12): `create_app` mounts `web/dist`
at `/` only when that directory exists, and only after every API route, so none of them are ever
shadowed by it (module docstring of `api/app.py`).

Every test here passes its own `web_dist_dir` (a `tmp_path`), never the real repository's
`web/dist`: whether that happens to exist depends on whether `(cd web && npm run build)` has been
run in this working tree, and this suite must pass either way.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from playground.api.app import create_app
from playground.config import Settings

OPENAPI_PATHS_GOLDEN = Path(__file__).parent / "openapi_paths.json"


def _client(tmp_path: Path, *, web_dist_dir: Path) -> TestClient:
    data_dir = tmp_path / "data"
    app = create_app(Settings(data_dir=data_dir), web_dist_dir=web_dist_dir)
    return TestClient(app)


def test_root_is_not_found_when_web_dist_does_not_exist(tmp_path: Path) -> None:
    client = _client(tmp_path, web_dist_dir=tmp_path / "no-such-dist")

    response = client.get("/")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"


def test_api_routes_still_work_when_web_dist_does_not_exist(tmp_path: Path) -> None:
    client = _client(tmp_path, web_dist_dir=tmp_path / "no-such-dist")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def _write_built_page(dist_dir: Path) -> None:
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<!doctype html><title>Portfolio import</title>", encoding="utf-8")
    (dist_dir / "assets").mkdir()
    (dist_dir / "assets" / "app.js").write_text("console.log('built');", encoding="utf-8")


def test_root_serves_the_built_page_when_web_dist_exists(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _write_built_page(dist_dir)
    client = _client(tmp_path, web_dist_dir=dist_dir)

    response = client.get("/")

    assert response.status_code == 200
    assert "Portfolio import" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_built_assets_are_served_under_their_own_path(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _write_built_page(dist_dir)
    client = _client(tmp_path, web_dist_dir=dist_dir)

    response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert "console.log" in response.text


def test_an_api_route_is_never_shadowed_by_the_mounted_page(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _write_built_page(dist_dir)
    client = _client(tmp_path, web_dist_dir=dist_dir)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": response.json()["version"],
        "real_orders": False,
        "portfolio_read_only": True,
    }


def test_an_unknown_path_is_still_the_plain_error_envelope(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _write_built_page(dist_dir)
    client = _client(tmp_path, web_dist_dir=dist_dir)

    response = client.get("/this-path-does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert set(body["error"]) == {"code", "message"}


def test_mounting_the_page_adds_no_path_to_the_openapi_contract(tmp_path: Path) -> None:
    """A `Mount` is not a `fastapi.routing.APIRoute` (module docstring): `/openapi.json` must list
    exactly the API paths of the golden file, whether or not the page is mounted (WP11's contract
    test already checks this without a mount; this is the same check with one in place)."""
    dist_dir = tmp_path / "dist"
    _write_built_page(dist_dir)
    client = _client(tmp_path, web_dist_dir=dist_dir)

    paths = sorted(client.get("/openapi.json").json()["paths"])

    golden = json.loads(OPENAPI_PATHS_GOLDEN.read_text(encoding="utf-8"))
    assert paths == golden


@pytest.mark.parametrize("path", ["/health", "/portfolio/holdings", "/portfolio/value"])
def test_get_endpoints_win_over_the_mount_even_though_it_matches_every_path(tmp_path: Path, path: str) -> None:
    """`Mount("/", ...)` matches any path as a prefix (Starlette tries routes in the order they
    were added and stops at the first full match), so this only holds because the mount is added
    last, after every API route (module docstring)."""
    dist_dir = tmp_path / "dist"
    _write_built_page(dist_dir)
    client = _client(tmp_path, web_dist_dir=dist_dir)

    response = client.get(path)

    # Never the static 404 page, and never an HTML content type: whatever the status, it is one of
    # this API's own JSON responses (a plain 400 "not_initialised" for the portfolio routes here,
    # since this data directory was never `pg init`-ed; plan 5.8's error envelope either way).
    assert response.headers["content-type"].startswith("application/json")
