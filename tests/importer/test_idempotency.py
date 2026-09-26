"""Order and repeats do not matter (plan 5.3.4, 7.2, AC5).

**Idempotent** means doing the same thing twice has the same effect as doing it once. Here: any
sequence of imports (each staged, then accepted), with repeats, whose files together form a set S
of the eleven golden files (the CSV export, the nine PDF documents and the H1 account statement)
ends with the same accepted ledger and the same open review items as one import of S.

Both sides are compared in a canonical form without ids, occurrence numbers, content hashes and
timestamps (the harness's `canonical()`): every accepted and held transaction with its fields and
its sources, the lot book, and the open review items. Hypothesis draws S and the sequence. The
pipeline gets a text extractor that caches by file hash, so each PDF goes through pdfplumber once
per test run, and the registries live in memory.
"""

from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from playground.importer.pipeline import InputFile

GOLDEN_INPUTS = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "inputs"
FILES: dict[str, InputFile] = {
    path.name: InputFile(name=path.name, data=path.read_bytes())
    for path in sorted(
        [
            GOLDEN_INPUTS / "tr_transactions_2024.csv",
            *(GOLDEN_INPUTS / "pdf").glob("*.pdf"),
            GOLDEN_INPUTS / "statement" / "kontoauszug_2024_h1.pdf",
        ]
    )
}
STATEMENT = "kontoauszug_2024_h1.pdf"
ROUND_ONE = sorted(name for name in FILES if name != STATEMENT)

# One import of S, computed once per set S in a test run.
_REFERENCE: dict[frozenset[str], tuple] = {}


def reference(harness_factory, chosen: frozenset[str]) -> tuple:
    if chosen not in _REFERENCE:
        harness = harness_factory(in_memory=True)
        harness.run(*(FILES[name] for name in sorted(chosen)))
        _REFERENCE[chosen] = harness.canonical()
    return _REFERENCE[chosen]


def run_sequence(harness_factory, sequence: list[list[str]]) -> tuple:
    harness = harness_factory(in_memory=True)
    for batch in sequence:
        harness.run(*(FILES[name] for name in batch))
    return harness.canonical()


@st.composite
def import_plans(draw: st.DrawFn) -> tuple[frozenset[str], list[list[str]]]:
    """A set S of golden files and a sequence of imports, with repeats, whose files together form S."""
    chosen = draw(st.sets(st.sampled_from(sorted(FILES)), min_size=1))
    members = sorted(chosen)
    count = draw(st.integers(min_value=1, max_value=4))
    batches = [draw(st.sets(st.sampled_from(members), min_size=1)) for _ in range(count)]
    covered = set().union(*batches)
    for name in members:
        if name not in covered:
            batches[draw(st.integers(min_value=0, max_value=count - 1))].add(name)
    return frozenset(chosen), [list(draw(st.permutations(sorted(batch)))) for batch in batches]


def test_the_golden_rounds_give_the_same_result_as_one_import_of_all_eleven_files(harness_factory) -> None:
    assert len(FILES) == 11
    rounds = run_sequence(harness_factory, [ROUND_ONE, [*ROUND_ONE, STATEMENT]])
    assert rounds == reference(harness_factory, frozenset(FILES))


def test_the_statement_first_gives_the_same_result(harness_factory) -> None:
    # Every trade line is held back first (no quantity), then completed by the CSV and the documents.
    sequence = [[STATEMENT], ["tr_transactions_2024.csv"], [name for name in ROUND_ONE if name.endswith(".pdf")]]
    assert run_sequence(harness_factory, sequence) == reference(harness_factory, frozenset(FILES))


def test_each_file_on_its_own_in_reverse_order_gives_the_same_result(harness_factory) -> None:
    sequence = [[name] for name in sorted(FILES, reverse=True)]
    assert run_sequence(harness_factory, sequence) == reference(harness_factory, frozenset(FILES))


def test_importing_everything_again_adds_no_transaction_and_no_review_item(harness_factory) -> None:
    harness = harness_factory(in_memory=True)
    harness.run(*FILES.values())
    before = harness.canonical()
    items_before = len(harness.items())

    summary = harness.run(*FILES.values())

    assert summary.counts["new"] == 0
    assert summary.counts["review_new"] == 0
    assert summary.counts["duplicate_files"] == 11
    assert harness.canonical() == before
    assert len(harness.items()) == items_before


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(plan=import_plans())
def test_any_sequence_of_imports_gives_the_same_ledger_as_one_import(harness_factory, plan) -> None:
    chosen, sequence = plan
    assert run_sequence(harness_factory, sequence) == reference(harness_factory, chosen)
