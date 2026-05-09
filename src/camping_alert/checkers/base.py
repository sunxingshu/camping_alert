"""Shared helpers for all platform checkers."""

import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)


def friday_saturday_pairs(weeks_min: int, weeks_max: int) -> list[tuple[date, date]]:
    """
    Return (friday, sunday) pairs for each Friday+Saturday combo
    between weeks_min and weeks_max from today.
    Sunday is the checkout date after Saturday night.
    """
    today = date.today()
    pairs: list[tuple[date, date]] = []

    # Walk day by day, collect Fridays in the window
    start = today + timedelta(weeks=weeks_min)
    end = today + timedelta(weeks=weeks_max)
    cursor = start
    while cursor <= end:
        if cursor.weekday() == 4:  # Friday
            pairs.append((cursor, cursor + timedelta(days=2)))
        cursor += timedelta(days=1)

    return pairs
