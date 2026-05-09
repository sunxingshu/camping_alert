"""Tests for the SlotDB deduplication store."""

import tempfile
from pathlib import Path

import pytest

from camping_alert.db import SlotDB


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = SlotDB(Path(tmpdir) / "test.db")
        yield d
        d.close()


def test_new_slot_is_new(db):
    assert db.is_new("park1:site1:2026-06-05")


def test_seen_slot_not_new(db):
    db.mark_seen("park1:site1:2026-06-05", "Park", "Site 1",
                 "2026-06-05", "2026-06-07", "https://example.com")
    assert not db.is_new("park1:site1:2026-06-05")


def test_remove_allows_re_alert(db):
    slot_id = "park1:site1:2026-06-05"
    db.mark_seen(slot_id, "Park", "Site 1", "2026-06-05", "2026-06-07", "https://example.com")
    db.remove(slot_id)
    assert db.is_new(slot_id)


def test_all_seen_ids(db):
    db.mark_seen("a:1:2026-06-05", "P", "S1", "2026-06-05", "2026-06-07", "https://x.com")
    db.mark_seen("b:2:2026-06-12", "P", "S2", "2026-06-12", "2026-06-14", "https://x.com")
    assert db.all_seen_ids() == {"a:1:2026-06-05", "b:2:2026-06-12"}


def test_replace_on_duplicate(db):
    slot_id = "park1:site1:2026-06-05"
    db.mark_seen(slot_id, "Park", "Site 1", "2026-06-05", "2026-06-07", "https://a.com")
    db.mark_seen(slot_id, "Park", "Site 1", "2026-06-05", "2026-06-07", "https://b.com")
    assert not db.is_new(slot_id)
    assert len(db.all_seen_ids()) == 1
