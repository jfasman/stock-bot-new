from datetime import date

from stockbot.ops.wash_sale import Lot, Sale, detect_wash_sales


def test_wash_sale_detected_for_loss_with_30d_repurchase():
    original = Lot(ticker="AAPL", quantity=10, cost_basis=200, acquired=date(2025, 5, 1))
    replacement = Lot(ticker="AAPL", quantity=10, cost_basis=190, acquired=date(2025, 6, 5))
    sale = Sale(
        ticker="AAPL",
        quantity=10,
        proceeds_per_share=180,
        sold=date(2025, 6, 1),
        matched_lot=original,
    )
    events = detect_wash_sales([original, replacement], [sale])
    assert len(events) == 1
    assert events[0].disallowed_loss == (200 - 180) * 10


def test_no_wash_sale_when_repurchase_outside_window():
    original = Lot(ticker="AAPL", quantity=10, cost_basis=200, acquired=date(2025, 5, 1))
    replacement = Lot(ticker="AAPL", quantity=10, cost_basis=190, acquired=date(2025, 8, 1))
    sale = Sale(ticker="AAPL", quantity=10, proceeds_per_share=180, sold=date(2025, 6, 1),
                matched_lot=original)
    assert detect_wash_sales([original, replacement], [sale]) == []


def test_gains_never_trigger_wash_sale():
    original = Lot(ticker="AAPL", quantity=10, cost_basis=100, acquired=date(2025, 1, 1))
    replacement = Lot(ticker="AAPL", quantity=10, cost_basis=210, acquired=date(2025, 2, 1))
    sale = Sale(ticker="AAPL", quantity=10, proceeds_per_share=150, sold=date(2025, 2, 5),
                matched_lot=original)
    assert detect_wash_sales([original, replacement], [sale]) == []
