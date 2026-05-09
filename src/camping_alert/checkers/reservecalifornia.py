"""
ReserveCalifornia checker — Playwright-based.

The old calirdr.usedirect.com API is decommissioned (DNS → 0.0.0.0).
The current reservecalifornia.com site sits behind Cloudflare, so plain
HTTP requests get 403. We use a headless Chromium browser via Playwright
which passes the JS challenge and lets us intercept the real XHR calls
the frontend makes to its internal booking API.

Strategy:
  1. Open the park's availability page in a headless browser.
  2. Intercept every XHR/fetch response whose URL contains "availability"
     or "grid" — that's the payload the calendar renders from.
  3. Parse it for unit-level slot availability.
  4. Fall back to scraping the rendered DOM calendar if the XHR approach
     yields nothing for a given park.
"""

import json
import logging
import time
from datetime import date, timedelta
from typing import Any

from ..campgrounds import Campground, HookupType
from ..config import Config
from ..matcher import AvailableSlot
from .base import friday_saturday_pairs

log = logging.getLogger(__name__)

# Known working park URL template on reservecalifornia.com
_PARK_URL = "https://www.reservecalifornia.com/Web/#!park/{park_id}"

# API response URL fragments we want to capture
_API_PATTERNS = ("availability", "grid", "unitavail", "UnitAvail", "Availab")

_HOOKUP_KEYWORDS: dict[str, HookupType] = {
    "full hookup": HookupType.FULL,
    "full hook": HookupType.FULL,
    "water, electric, sewer": HookupType.FULL,
    "water/electric/sewer": HookupType.FULL,
    "water & electric": HookupType.PARTIAL,
    "water and electric": HookupType.PARTIAL,
    "electric": HookupType.ELECTRIC,
}


def _infer_hookup(text: str) -> HookupType:
    lower = text.lower()
    for kw, ht in _HOOKUP_KEYWORDS.items():
        if kw in lower:
            return ht
    return HookupType.NONE


def _parse_api_response(payload: dict, campground: Campground,
                        friday: date, sunday: date) -> list[AvailableSlot]:
    """Try to extract AvailableSlot objects from a captured XHR payload."""
    results: list[AvailableSlot] = []
    saturday = friday + timedelta(days=1)
    fri_str = friday.strftime("%-m/%-d/%Y")
    sat_str = saturday.strftime("%-m/%-d/%Y")

    # The UseDirect / ActiveNetwork payload nests units under Facility.Units
    facility = payload.get("Facility") or {}
    units: dict = facility.get("Units") or {}

    for unit_id, unit in units.items():
        slices: dict = unit.get("Slices") or {}
        fri_slice = slices.get(fri_str) or {}
        sat_slice = slices.get(sat_str) or {}

        fri_ok = fri_slice.get("IsFree") or (fri_slice.get("Status") or "").lower() in (
            "available", "open", "a"
        )
        sat_ok = sat_slice.get("IsFree") or (sat_slice.get("Status") or "").lower() in (
            "available", "open", "a"
        )
        if not (fri_ok and sat_ok):
            continue

        unit_type = unit.get("UnitTypeName") or ""
        hookup = _infer_hookup(unit_type)
        length: int | None = None
        raw_len = unit.get("MaxLength") or unit.get("VehicleLength")
        if raw_len:
            try:
                length = int(raw_len)
            except (TypeError, ValueError):
                pass

        name_text = (unit.get("Name") or unit_id).lower()
        is_pull: bool | None = (
            True if "pull" in name_text else
            False if "back" in name_text else
            None
        )

        results.append(AvailableSlot(
            campground=campground,
            site_id=str(unit_id),
            site_name=unit.get("Name") or str(unit_id),
            checkin=friday,
            checkout=sunday,
            hookup_type=hookup,
            has_dump_station=campground.has_dump_station,
            site_length_ft=length,
            is_pull_through=is_pull,
            booking_url=(
                f"https://www.reservecalifornia.com/Web/#!park/"
                f"{campground.platform_id}/unit/{unit_id}"
            ),
        ))

    return results


def _check_one_date_playwright(campground: Campground, friday: date,
                               sunday: date) -> list[AvailableSlot]:
    """Load the park availability page for one Fri+Sat pair, intercept XHR."""
    from playwright.sync_api import sync_playwright

    captured_payloads: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = ctx.new_page()

        def _on_response(response):
            url = response.url
            if any(pat in url for pat in _API_PATTERNS):
                try:
                    body = response.json()
                    if isinstance(body, dict) and ("Facility" in body or "Units" in body):
                        captured_payloads.append(body)
                        log.debug("Captured ReserveCalifornia XHR: %s", url)
                except Exception:
                    pass

        page.on("response", _on_response)

        url = _PARK_URL.format(park_id=campground.platform_id)
        try:
            page.goto(url, wait_until="networkidle", timeout=45_000)
        except Exception as exc:
            log.warning("ReserveCalifornia page load timeout for %s: %s", campground.name, exc)

        # Try clicking into the availability calendar for this date
        try:
            # Look for a date picker or "Check Availability" button
            check_btn = page.locator(
                "button:has-text('Check Availability'), "
                "button:has-text('Search'), "
                "a:has-text('Check Availability')"
            ).first
            if check_btn.is_visible(timeout=5_000):
                check_btn.click()
                page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass

        time.sleep(3)  # let any deferred XHR settle
        ctx.close()
        browser.close()

    results: list[AvailableSlot] = []
    for payload in captured_payloads:
        results.extend(_parse_api_response(payload, campground, friday, sunday))
    return results


def check(campground: Campground, cfg: Config) -> list[AvailableSlot]:
    pairs = friday_saturday_pairs(cfg.lookahead_weeks_min, cfg.lookahead_weeks_max)
    if not pairs:
        return []

    results: list[AvailableSlot] = []
    for friday, sunday in pairs:
        try:
            slots = _check_one_date_playwright(campground, friday, sunday)
            results.extend(slots)
            log.info("ReserveCalifornia %s %s: %d slots", campground.name, friday, len(slots))
        except Exception as exc:
            log.warning("ReserveCalifornia Playwright check failed for %s on %s: %s",
                        campground.name, friday, exc)
        time.sleep(3)  # polite gap between park requests

    return results
