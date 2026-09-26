"""Shared fixtures for the Trade Republic PDF tests (plan 5.3.2, 6.2).

The text fixtures under `tests/fixtures/tr/text/<parser id>/<case>.txt` are the source of truth.
`tests/fixtures/tr/make_pdfs.py` writes one PDF per text fixture (a few go to the golden and
idempotency directories instead). The tests load that script as a module to get its job list,
so the PDF layer test covers every PDF it writes.

Test modules must not import from this file (pytest may load another `conftest` module under
the same name). Instead, a test asks for:

- `text_fixture`: parametrised over every `.txt` fixture (id `<directory>/<case>`);
- `pdf_job`: parametrised over every job of `make_pdfs.pdf_jobs()` (id: the PDF path);
- `make_pdfs`: the generator module itself.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
TEXT_DIR = FIXTURES_DIR / "tr" / "text"
MAKE_PDFS_PATH = FIXTURES_DIR / "tr" / "make_pdfs.py"


def _load_make_pdfs() -> ModuleType:
    """Load the generator script as a module named `make_pdfs` (once per test run)."""
    if "make_pdfs" in sys.modules:
        return sys.modules["make_pdfs"]
    spec = importlib.util.spec_from_file_location("make_pdfs", MAKE_PDFS_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before running it, so dataclasses in the script can resolve their module.
    sys.modules["make_pdfs"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules["make_pdfs"]
        raise
    return module


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise `text_fixture` and `pdf_job` arguments over every fixture and every PDF job."""
    if "text_fixture" in metafunc.fixturenames:
        paths = sorted(TEXT_DIR.glob("*/*.txt"))
        ids = [path.relative_to(TEXT_DIR).with_suffix("").as_posix() for path in paths]
        metafunc.parametrize("text_fixture", paths, ids=ids)
    if "pdf_job" in metafunc.fixturenames:
        jobs = _load_make_pdfs().pdf_jobs()
        ids = [job.target.relative_to(FIXTURES_DIR).as_posix() for job in jobs]
        metafunc.parametrize("pdf_job", jobs, ids=ids)


@pytest.fixture(scope="session")
def make_pdfs() -> ModuleType:
    """The fixture PDF generator script, loaded as a module."""
    return _load_make_pdfs()
