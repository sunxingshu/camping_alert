"""Tests for the shared checker utilities."""

from datetime import date, timedelta

from camping_alert.checkers.base import friday_saturday_pairs


def test_all_results_are_fridays():
    pairs = friday_saturday_pairs(2, 12)
    for friday, _ in pairs:
        assert friday.weekday() == 4, f"{friday} is not a Friday"


def test_checkout_is_sunday():
    pairs = friday_saturday_pairs(2, 4)
    for friday, sunday in pairs:
        assert sunday == friday + timedelta(days=2)


def test_window_respected():
    today = date.today()
    pairs = friday_saturday_pairs(2, 4)
    for friday, _ in pairs:
        weeks_out = (friday - today).days / 7
        assert 2 <= weeks_out <= 4 + 1  # +1 to allow for partial weeks


def test_returns_multiple_weekends():
    pairs = friday_saturday_pairs(2, 12)
    # 10-week window should have roughly 10 Fridays
    assert len(pairs) >= 8
