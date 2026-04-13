"""Tests for amazon.py — multi-order matching and entry filtering."""

import pytest

from src.amazon import _filter_amazon_entries, _match_orders_to_entry


class TestAmazonEntryFiltering:
    """Test the entry filtering logic — imports REAL function from src/amazon.py."""

    def test_amzn_mktp_matches(self):
        entries = [{"vendor": "AMZN Mktp DE*IT5HF5H85", "amount": 126.03, "is_credit": False}]
        assert len(_filter_amazon_entries(entries)) == 1

    def test_amazon_de_matches(self):
        entries = [{"vendor": "Amazon.de VD9CW3W5", "amount": 21.29, "is_credit": False}]
        assert len(_filter_amazon_entries(entries)) == 1

    def test_amazon_de_star_matches(self):
        entries = [{"vendor": "Amazon.de*BYOT94N15", "amount": 84.00, "is_credit": False}]
        assert len(_filter_amazon_entries(entries)) == 1

    def test_credit_excluded(self):
        entries = [{"vendor": "AMZN Mktp DE", "amount": 10.0, "is_credit": True}]
        assert len(_filter_amazon_entries(entries)) == 0

    def test_non_amazon_excluded(self):
        entries = [{"vendor": "ANTHROPIC", "amount": 100.0, "is_credit": False}]
        assert len(_filter_amazon_entries(entries)) == 0

    def test_multiple_amazon_entries(self):
        entries = [
            {"vendor": "AMZN Mktp DE*IT5HF5H85", "amount": 126.03, "is_credit": False},
            {"vendor": "Amazon.de VD9CW3W5", "amount": 21.29, "is_credit": False},
            {"vendor": "AMZN Mktp DE*FQ6IC9W85", "amount": 8.39, "is_credit": False},
            {"vendor": "Amazon.de*BYOT94N15", "amount": 84.00, "is_credit": False},
            {"vendor": "AMZN Mktp DE*4F6XP6TO5", "amount": 4.99, "is_credit": False},
        ]
        assert len(_filter_amazon_entries(entries)) == 5


# ─── Order Matching (real function) ────────────────────────────────

class TestExactMatch:
    """1:1 matching — one order matches one MC entry."""

    def test_exact_amount(self):
        orders = [
            {"order_id": "A", "pdf_urls": ["/a.pdf"], "amount": 21.29},
            {"order_id": "B", "pdf_urls": ["/b.pdf"], "amount": 126.03},
        ]
        result = _match_orders_to_entry(orders, 126.03, set())
        assert len(result) == 1
        assert result[0]["order_id"] == "B"

    def test_close_amount(self):
        """Diff <= 1 EUR still counts as exact match."""
        orders = [
            {"order_id": "A", "pdf_urls": ["/a.pdf"], "amount": 125.50},
        ]
        result = _match_orders_to_entry(orders, 126.03, set())
        assert len(result) == 1
        assert result[0]["order_id"] == "A"

    def test_used_orders_skipped(self):
        orders = [
            {"order_id": "A", "pdf_urls": ["/a.pdf"], "amount": 126.03},
            {"order_id": "B", "pdf_urls": ["/b.pdf"], "amount": 84.00},
        ]
        result = _match_orders_to_entry(orders, 126.03, {"A"})
        assert len(result) == 1
        assert result[0]["order_id"] == "B"

    def test_closest_wins(self):
        orders = [
            {"order_id": "A", "pdf_urls": ["/a.pdf"], "amount": 100.0},
            {"order_id": "B", "pdf_urls": ["/b.pdf"], "amount": 125.50},
            {"order_id": "C", "pdf_urls": ["/c.pdf"], "amount": 200.0},
        ]
        result = _match_orders_to_entry(orders, 126.03, set())
        assert len(result) == 1
        assert result[0]["order_id"] == "B"


class TestComboMatch:
    """N:1 matching — multiple orders sum to one MC entry."""

    def test_two_orders_sum_to_mc_amount(self):
        """THE ACTUAL BUG: 3.39 + 122.64 = 126.03 EUR."""
        orders = [
            {"order_id": "302-778", "pdf_urls": ["/a.pdf"], "amount": 3.39},
            {"order_id": "302-519", "pdf_urls": ["/b1.pdf", "/b2.pdf"], "amount": 122.64},
            {"order_id": "305-xxx", "pdf_urls": ["/c.pdf"], "amount": 84.00},
        ]
        result = _match_orders_to_entry(orders, 126.03, set())
        assert len(result) == 2
        ids = {r["order_id"] for r in result}
        assert "302-778" in ids
        assert "302-519" in ids

    def test_combo_not_used_when_exact_exists(self):
        """If exact match exists, don't use combo."""
        orders = [
            {"order_id": "A", "pdf_urls": ["/a.pdf"], "amount": 126.03},
            {"order_id": "B", "pdf_urls": ["/b.pdf"], "amount": 3.39},
            {"order_id": "C", "pdf_urls": ["/c.pdf"], "amount": 122.64},
        ]
        result = _match_orders_to_entry(orders, 126.03, set())
        assert len(result) == 1
        assert result[0]["order_id"] == "A"

    def test_combo_with_small_diff(self):
        """Combo sum within 1 EUR tolerance."""
        orders = [
            {"order_id": "A", "pdf_urls": ["/a.pdf"], "amount": 50.00},
            {"order_id": "B", "pdf_urls": ["/b.pdf"], "amount": 76.50},
        ]
        # 50 + 76.50 = 126.50, target 126.03, diff = 0.47
        result = _match_orders_to_entry(orders, 126.03, set())
        assert len(result) == 2

    def test_combo_too_far(self):
        """Combo sum > 1 EUR away should NOT match as combo."""
        orders = [
            {"order_id": "A", "pdf_urls": ["/a.pdf"], "amount": 50.00},
            {"order_id": "B", "pdf_urls": ["/b.pdf"], "amount": 50.00},
        ]
        # 50 + 50 = 100, target 126.03, diff = 26.03 — too far
        result = _match_orders_to_entry(orders, 126.03, set())
        # Should fallback to single best match, not combo
        assert len(result) == 1

    def test_combo_respects_used_orders(self):
        orders = [
            {"order_id": "A", "pdf_urls": ["/a.pdf"], "amount": 3.39},
            {"order_id": "B", "pdf_urls": ["/b.pdf"], "amount": 122.64},
        ]
        # A is used, combo can't form
        result = _match_orders_to_entry(orders, 126.03, {"A"})
        assert len(result) == 1
        assert result[0]["order_id"] == "B"


class TestMultipleInvoicesPerOrder:
    """One order can have multiple invoices (Marketplace sellers)."""

    def test_order_with_two_invoices(self):
        orders = [
            {"order_id": "302-519", "pdf_urls": ["/invoice1.pdf", "/invoice2.pdf"], "amount": 122.64},
        ]
        result = _match_orders_to_entry(orders, 122.64, set())
        assert len(result) == 1
        assert len(result[0]["pdf_urls"]) == 2

    def test_combo_with_multi_invoice_order(self):
        """Combo match where one order has 2 invoices."""
        orders = [
            {"order_id": "A", "pdf_urls": ["/a.pdf"], "amount": 3.39},
            {"order_id": "B", "pdf_urls": ["/b1.pdf", "/b2.pdf"], "amount": 122.64},
        ]
        result = _match_orders_to_entry(orders, 126.03, set())
        assert len(result) == 2
        total_pdfs = sum(len(r["pdf_urls"]) for r in result)
        assert total_pdfs == 3  # 1 from A + 2 from B


class TestFallback:
    """When nothing matches well."""

    def test_no_amount_info(self):
        orders = [
            {"order_id": "A", "pdf_urls": ["/a.pdf"], "amount": None},
        ]
        result = _match_orders_to_entry(orders, 126.03, set())
        assert len(result) == 1
        assert result[0]["order_id"] == "A"

    def test_empty_orders(self):
        result = _match_orders_to_entry([], 126.03, set())
        assert result == []

    def test_all_used(self):
        orders = [
            {"order_id": "A", "pdf_urls": ["/a.pdf"], "amount": 126.03},
        ]
        result = _match_orders_to_entry(orders, 126.03, {"A"})
        assert result == []


class TestReturnFormat:
    """Verify the return type contract: list[tuple[dict, Path]]."""

    def test_tuple_unpacking(self):
        from pathlib import Path
        from src.result import RunResult

        results = [
            ({"vendor": "AMZN Mktp", "amount": 126.03, "category": "other", "date": "12.03.26", "is_credit": False, "_id": "p2_12"},
             Path("/tmp/Amazon_123_invoice.pdf")),
            ({"vendor": "Amazon.de", "amount": 21.29, "category": "other", "date": "16.03.26", "is_credit": False, "_id": "p3_3"},
             Path("/tmp/Amazon_456_invoice.pdf")),
        ]

        run_result = RunResult()
        entries = [r[0] for r in results]
        run_result.add_entries(entries)

        for entry, filepath in results:
            run_result.mark_matched(entry, [filepath], source="amazon.de")

        assert len(run_result.matched) == 2
        assert run_result.matched[0].source == "amazon.de"
        assert len(run_result.all_files) == 2
