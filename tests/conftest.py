"""Pytest configuration and fixtures."""

import tempfile
from datetime import date
from pathlib import Path

import pytest

from playground.config import Settings
from playground.core.clock import FixedClock

#: plan 7.1: "tests/conftest.py gives each test ... a ReplayHttpClient on tests/fixtures/http".
HTTP_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "http"
#: plan 7.1: "... and a FixedClock on 2024-12-31" -- the same day the golden portfolio (1.2) ends on.
GOLDEN_TODAY = date(2024, 12, 31)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add `--update-goldens` (plan 7.1): rewrite golden files instead of comparing with them.

    Any rewrite must be reviewed in the git diff before it is committed.
    """
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="Rewrite golden files from the current output instead of comparing with them.",
    )


@pytest.fixture
def update_goldens(request: pytest.FixtureRequest) -> bool:
    """True when the run was started with `--update-goldens`."""
    return bool(request.config.getoption("--update-goldens"))


@pytest.fixture
def temp_data_dir() -> Path:
    """Provide a temporary data directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def settings(temp_data_dir: Path) -> Settings:
    """Provide test settings."""
    return Settings(data_dir=temp_data_dir, today=GOLDEN_TODAY)


@pytest.fixture
def fixed_clock() -> FixedClock:
    """A clock stopped on 2024-12-31, the day the golden portfolio (1.2) ends on."""
    return FixedClock(GOLDEN_TODAY)


@pytest.fixture
def replay_http_client():
    """An `HttpClient` serving the committed fixtures under `tests/fixtures/http` (plan 5.4, 7.1).

    Every marketdata client test builds its client from this (or from `ReplayHttpClient(some_
    other_dir)` for a test-specific fixture directory), never from `NetworkHttpClient`, so the
    whole suite runs with real sockets disabled.

    Imported lazily, like `tests/importer/conftest.py` does for the importer pipeline: WP9 adds
    `playground.http`, and a plain top-of-file import here would fail every test in the suite at
    collection time (this file is loaded first for all of them) on any commit before it exists,
    rather than failing only the tests that actually use this fixture.
    """
    from playground.http.replay import ReplayHttpClient

    return ReplayHttpClient(HTTP_FIXTURES_DIR)
