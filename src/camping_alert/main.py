"""
Camping alert scheduler.

Usage:
  python -m camping_alert.main          # run continuously
  python -m camping_alert.main --once   # single check then exit (good for cron)
"""

import argparse
import logging
import time
from datetime import datetime, timezone

import schedule

from . import config as cfg_module
from .campgrounds import Platform, for_platform
from .checkers import hipcamp as hipcamp_checker
from .checkers import recgov, reservecalifornia
from .db import SlotDB
from .matcher import filter_slots
from .notifier import send_alert, send_heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def run_check(cfg, db: SlotDB) -> int:
    """Run one full check across all platforms. Returns count of new alerts sent."""
    now = datetime.now(timezone.utc)
    log.info("=== Check started at %s ===", now.isoformat())
    new_alert_count = 0

    # ── Recreation.gov ─────────────────────────────────────────────────────────
    for cg in for_platform(Platform.RECGOV):
        log.info("Checking RecGov: %s", cg.name)
        try:
            slots = recgov.check(cg, cfg)
            matched = filter_slots(slots, cfg)
            for slot in matched:
                if db.is_new(slot.slot_id):
                    log.info("NEW SLOT: %s @ %s on %s", slot.site_name, cg.name, slot.checkin)
                    send_alert(slot, cfg)
                    db.mark_seen(
                        slot.slot_id,
                        cg.park_name,
                        slot.site_name,
                        slot.checkin.isoformat(),
                        slot.checkout.isoformat(),
                        slot.booking_url,
                    )
                    new_alert_count += 1
        except Exception as exc:
            log.error("RecGov check failed for %s: %s", cg.name, exc)

    # ── ReserveCalifornia ──────────────────────────────────────────────────────
    for cg in for_platform(Platform.RESERVECA):
        log.info("Checking ReserveCalifornia: %s", cg.name)
        try:
            slots = reservecalifornia.check(cg, cfg)
            matched = filter_slots(slots, cfg)
            for slot in matched:
                if db.is_new(slot.slot_id):
                    log.info("NEW SLOT: %s @ %s on %s", slot.site_name, cg.name, slot.checkin)
                    send_alert(slot, cfg)
                    db.mark_seen(
                        slot.slot_id,
                        cg.park_name,
                        slot.site_name,
                        slot.checkin.isoformat(),
                        slot.checkout.isoformat(),
                        slot.booking_url,
                    )
                    new_alert_count += 1
        except Exception as exc:
            log.error("ReserveCalifornia check failed for %s: %s", cg.name, exc)

    # ── Hipcamp ────────────────────────────────────────────────────────────────
    log.info("Checking Hipcamp (coastal bbox search)...")
    try:
        slots = hipcamp_checker.check_hipcamp(cfg)
        matched = filter_slots(slots, cfg)
        for slot in matched:
            if db.is_new(slot.slot_id):
                log.info("NEW SLOT (Hipcamp): %s on %s", slot.site_name, slot.checkin)
                send_alert(slot, cfg)
                db.mark_seen(
                    slot.slot_id,
                    slot.campground.park_name,
                    slot.site_name,
                    slot.checkin.isoformat(),
                    slot.checkout.isoformat(),
                    slot.booking_url,
                )
                new_alert_count += 1
    except Exception as exc:
        log.error("Hipcamp check failed: %s", exc)

    # ── Weekly heartbeat on Sundays ────────────────────────────────────────────
    import zoneinfo
    now_pt = datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))
    if now_pt.weekday() == 6:  # Sunday
        _maybe_send_heartbeat(cfg, db)

    # ── Prune stale seen slots that are now in the past ────────────────────────
    _prune_past_slots(db)

    log.info("=== Check complete. %d new alerts sent. ===", new_alert_count)
    return new_alert_count


def _maybe_send_heartbeat(cfg, db: SlotDB) -> None:
    """Send heartbeat at most once per Sunday (keyed by ISO week in DB)."""
    from datetime import date
    from .campgrounds import CAMPGROUNDS
    week_key = f"__heartbeat__{date.today().isocalendar().week}"
    if db.is_new(week_key):
        names = [f"{c.name} ({c.city})" for c in CAMPGROUNDS]
        try:
            send_heartbeat(cfg, names, cfg.lookahead_weeks_max)
            db.mark_seen(week_key, "system", "heartbeat",
                         date.today().isoformat(), date.today().isoformat(), "")
        except Exception as exc:
            log.error("Heartbeat failed: %s", exc)


def _prune_past_slots(db: SlotDB) -> None:
    """Remove seen-slot records whose checkin date has passed."""
    from datetime import date
    today = date.today().isoformat()
    for slot_id in list(db.all_seen_ids()):
        # slot_id format: "{platform_id}:{site_id}:{checkin_date}"
        parts = slot_id.split(":")
        if len(parts) >= 3:
            checkin = parts[-1]
            if checkin < today:
                db.remove(slot_id)


def _is_active_hour(cfg) -> bool:
    """Return True if current Pacific time is within configured active hours."""
    import zoneinfo
    now_pt = datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))
    return cfg.active_hours_start <= now_pt.hour < cfg.active_hours_end


def _interval_minutes(cfg) -> int:
    """Return check interval: shorter on Thu/Fri (peak release days)."""
    from datetime import date
    weekday = date.today().weekday()
    if weekday in (3, 4):  # Thursday=3, Friday=4
        return cfg.peak_interval_minutes
    return cfg.check_interval_minutes


def _run_test(cfg) -> None:
    """Send one fake alert email to verify credentials and SMTP pipeline."""
    from datetime import date, timedelta
    from .campgrounds import Campground, HookupType, Platform
    from .matcher import AvailableSlot

    log.info("TEST MODE — sending test email to %s", cfg.alert_to_email)

    friday = date.today() + timedelta((4 - date.today().weekday()) % 7 or 7)
    fake_cg = Campground(
        name="Doran Regional Park — Site 42 (TEST)",
        park_name="Doran Regional Park",
        city="Bodega Bay, CA",
        platform=Platform.RESERVECA,
        platform_id="1177",
        booking_url="https://www.reservecalifornia.com/Web/#!park/1177",
        drive_minutes=115,
        ocean_view=True,
        hookup_type=HookupType.FULL,
        has_dump_station=True,
        has_pull_through=True,
        notes="THIS IS A TEST EMAIL — not a real availability alert.",
    )
    fake_slot = AvailableSlot(
        campground=fake_cg,
        site_id="TEST-42",
        site_name="Site 42 — Pull-Through (TEST)",
        checkin=friday,
        checkout=friday + timedelta(days=2),
        hookup_type=HookupType.FULL,
        has_dump_station=True,
        site_length_ft=45,
        is_pull_through=True,
        booking_url="https://www.reservecalifornia.com/Web/#!park/1177",
    )

    send_alert(fake_slot, cfg)
    log.info("Test email sent successfully.")


def _run_heartbeat(cfg) -> None:
    """Send a weekly status email listing monitored campgrounds."""
    from .campgrounds import CAMPGROUNDS
    names = [f"{c.name} ({c.city})" for c in CAMPGROUNDS]
    send_heartbeat(cfg, names, cfg.lookahead_weeks_max)
    log.info("Heartbeat sent.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Coastal camping availability alert")
    parser.add_argument("--once", action="store_true",
                        help="Run a single check then exit")
    parser.add_argument("--test", action="store_true",
                        help="Send a test email to verify credentials, then exit (no scan)")
    parser.add_argument("--heartbeat", action="store_true",
                        help="Send weekly status email and exit (no scan)")
    args = parser.parse_args()

    cfg = cfg_module.load()

    log.info("Camping alert started. Email alerts -> %s", cfg.alert_to_email)
    log.info("Look-ahead: %d–%d weeks | Check interval: %d min (peak: %d min)",
             cfg.lookahead_weeks_min, cfg.lookahead_weeks_max,
             cfg.check_interval_minutes, cfg.peak_interval_minutes)

    if args.test:
        _run_test(cfg)
        return

    if args.heartbeat:
        _run_heartbeat(cfg)
        return

    db = SlotDB()

    if args.once:
        run_check(cfg, db)
        db.close()
        return

    # Continuous scheduler mode
    def _job():
        if not _is_active_hour(cfg):
            log.debug("Outside active hours (%d–%d), skipping check.",
                      cfg.active_hours_start, cfg.active_hours_end)
            return
        run_check(cfg, db)

    # Schedule at current interval; re-evaluate on each wakeup
    schedule.every(_interval_minutes(cfg)).minutes.do(_job)
    _job()  # Run immediately at startup

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
            # Re-schedule if interval changed (Thu/Fri vs other days)
            new_interval = _interval_minutes(cfg)
            current_jobs = schedule.get_jobs()
            if current_jobs:
                current_interval = current_jobs[0].interval
                if current_interval != new_interval:
                    schedule.clear()
                    schedule.every(new_interval).minutes.do(_job)
                    log.info("Interval changed to %d min", new_interval)
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
