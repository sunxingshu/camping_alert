"""SQLite-backed store for seen availability slots (deduplication)."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_PATH = Path(__file__).parents[3] / "seen_slots.db"


class SlotDB:
    def __init__(self, path: Path = _DEFAULT_PATH) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._init()

    def _init(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_slots (
                id            TEXT PRIMARY KEY,
                park_name     TEXT NOT NULL,
                site_name     TEXT NOT NULL,
                checkin_date  TEXT NOT NULL,
                checkout_date TEXT NOT NULL,
                alerted_at    TEXT NOT NULL,
                booking_url   TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def is_new(self, slot_id: str) -> bool:
        """Return True if this slot has never been alerted."""
        row = self._conn.execute(
            "SELECT 1 FROM seen_slots WHERE id = ?", (slot_id,)
        ).fetchone()
        return row is None

    def mark_seen(
        self,
        slot_id: str,
        park_name: str,
        site_name: str,
        checkin_date: str,
        checkout_date: str,
        booking_url: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO seen_slots
                (id, park_name, site_name, checkin_date, checkout_date, alerted_at, booking_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (slot_id, park_name, site_name, checkin_date, checkout_date, now, booking_url),
        )
        self._conn.commit()

    def remove(self, slot_id: str) -> None:
        """Remove a slot so it can be re-alerted if it reappears."""
        self._conn.execute("DELETE FROM seen_slots WHERE id = ?", (slot_id,))
        self._conn.commit()

    def all_seen_ids(self) -> set[str]:
        rows = self._conn.execute("SELECT id FROM seen_slots").fetchall()
        return {r[0] for r in rows}

    def close(self) -> None:
        self._conn.close()
