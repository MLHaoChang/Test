"""Tests for money and quantity conventions (core/money.py, plan 3.3, 5.1, 7.2)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from playground.core.money import allocate, eur2, q8, to_eur


class TestQ8:
    def test_rounds_half_even_down(self) -> None:
        assert q8(Decimal("1.000000005")) == Decimal("1.00000000")  # ties to even (0)

    def test_rounds_half_even_up(self) -> None:
        assert q8(Decimal("1.000000015")) == Decimal("1.00000002")  # ties to even (2)

    def test_keeps_exact_values_unchanged(self) -> None:
        assert q8(Decimal("123.45678901")) == Decimal("123.45678901")


class TestEur2:
    def test_rounds_half_up(self) -> None:
        assert eur2(Decimal("1.005")) == Decimal("1.01")

    def test_rounds_down_below_half(self) -> None:
        assert eur2(Decimal("1.004")) == Decimal("1.00")

    def test_negative_half_up_rounds_away_from_zero(self) -> None:
        assert eur2(Decimal("-1.005")) == Decimal("-1.01")


class TestToEur:
    def test_eur_passthrough_ignores_rate(self) -> None:
        assert to_eur(Decimal("100.00"), "EUR", None) == Decimal("100.00")

    def test_usd_conversion_uses_ecb_convention(self) -> None:
        # 1.0800 USD per EUR (ECB / Trade Republic convention): 108.00 USD -> 100 EUR.
        assert to_eur(Decimal("108.00"), "USD", Decimal("1.08")) == Decimal("100")

    def test_gbx_divided_by_100_before_rate_is_applied(self) -> None:
        # 8000 pence = 80.00 GBP; at 0.80 GBP per EUR that is 100.00 EUR.
        assert to_eur(Decimal("8000"), "GBX", Decimal("0.80")) == Decimal("100.00")

    def test_missing_rate_for_foreign_currency_raises(self) -> None:
        with pytest.raises(ValueError, match="rate"):
            to_eur(Decimal("100"), "USD", None)

    def test_zero_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            to_eur(Decimal("100"), "USD", Decimal("0"))

    def test_negative_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            to_eur(Decimal("100"), "USD", Decimal("-1"))


class TestAllocate:
    def test_two_way_split_matches_golden_example(self) -> None:
        # T12 in the golden portfolio (plan 1.2): proceeds 2,099.00 split by quantity 10:2
        # gives 1,749.16666667 and 349.83333333 exactly.
        pieces = allocate(Decimal("2099.00"), [Decimal("10"), Decimal("2")])
        assert pieces == [Decimal("1749.16666667"), Decimal("349.83333333")]
        assert sum(pieces) == Decimal("2099.00")

    def test_single_weight_returns_total_unchanged(self) -> None:
        assert allocate(Decimal("123.45"), [Decimal("1")]) == [Decimal("123.45")]

    def test_pieces_always_sum_to_total_with_uneven_thirds(self) -> None:
        pieces = allocate(Decimal("100.00"), [Decimal("1"), Decimal("1"), Decimal("1")])
        assert sum(pieces) == Decimal("100.00")

    def test_zero_weight_gets_zero_piece(self) -> None:
        pieces = allocate(Decimal("100.00"), [Decimal("1"), Decimal("0")])
        assert pieces == [Decimal("100.00000000"), Decimal("0E-8")]

    def test_rejects_empty_weights(self) -> None:
        with pytest.raises(ValueError):
            allocate(Decimal("100"), [])

    def test_rejects_negative_weight(self) -> None:
        with pytest.raises(ValueError):
            allocate(Decimal("100"), [Decimal("1"), Decimal("-1")])

    def test_rejects_all_zero_weights(self) -> None:
        with pytest.raises(ValueError):
            allocate(Decimal("100"), [Decimal("0"), Decimal("0")])

    @given(
        total_cents=st.integers(min_value=-10_000_000, max_value=10_000_000),
        weights=st.lists(st.integers(min_value=0, max_value=100_000), min_size=1, max_size=12),
    )
    def test_property_sums_exactly_and_stays_close_to_exact_share(
        self, total_cents: int, weights: list[int]
    ) -> None:
        if sum(weights) == 0:
            weights = [*weights[:-1], 1]
        decimal_weights = [Decimal(w) for w in weights]
        total = Decimal(total_cents) / Decimal(100)

        pieces = allocate(total, decimal_weights)

        # The pieces always sum to exactly the total (never a cent lost or gained to rounding).
        assert sum(pieces) == total

        # Each piece stays within a small, bounded distance of its exact (unquantised) share:
        # each of the (up to) len(weights) quantisation steps can move the total by at most
        # half a unit in the last place (0.5E-8), so that is the tolerance budget per piece.
        weight_total = sum(decimal_weights, start=Decimal(0))
        tolerance = Decimal("0.5E-8") * len(decimal_weights)
        for weight, piece in zip(decimal_weights, pieces, strict=True):
            exact_share = total * weight / weight_total
            assert abs(piece - exact_share) <= tolerance
