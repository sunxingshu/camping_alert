"""
ReserveCalifornia checker — Playwright-based.

The old calirdr.usedirect.com API is decommissioned. The current
reservecalifornia.com site is behind Cloudflare, blocking plain HTTP.
We use a headless Chromium browser that:
  1. Navigates to the park's availability page (passes JS challenge).
  2. Fills in the check-in date + 2 nights in the search form.
  3. Waits for the calendar XHR to fire and intercepts the JSON payload.
  4. Parses the payload for per-unit availability.
"""

import json
import logging
import time
from datetime import date, timedelta

from ..campgrounds import Campground, HookupType
from ..config import Config
from ..matcher import AvailableSlot
from .base import friday_saturday_pairs

log = logging.getLogger(__name__)

_BASE_URL = "https://www.reservecalifornia.com/Web/#!park/{park_id}"

# URL fragments that identify the availability XHR payload
_XHR_PATTERNS = (
    "grid", "availability", "UnitAvail", "unitavail",
    "SearchViewUnit", "Availab",
)

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


def _parse_payload(payload: dict, campground: Campground,
                   friday: date, sunday: date) -> list[AvailableSlot]:
    results: list[AvailableSlot] = []
    saturday = friday + timedelta(days=1)
    fri_str = friday.strftime("%-m/%-d/%Y")
    sat_str = saturday.strftime("%-m/%-d/%Y")

    facility = payload.get("Facility") or {}
    units: dict = facility.get("Units") or {}

    for unit_id, unit in units.items():
        slices: dict = unit.get("Slices") or {}
        fri_s = slices.get(fri_str) or {}
        sat_s = slices.get(sat_str) or {}

        fri_ok = fri_s.get("IsFree") or (fri_s.get("Status") or "").lower() in ("available", "open", "a")
        sat_ok = sat_s.get("IsFree") or (sat_s.get("Status") or "").lower() in ("available", "open", "a")
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

        site_name = unit.get("Name") or str(unit_id)
        is_pull: bool | None = (
            True if "pull" in site_name.lower()
            else False if "back" in site_name.lower()
            else None
        )

        results.append(AvailableSlot(
            campground=campground,
            site_id=str(unit_id),
            site_name=site_name,
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


def _check_one_park_all_dates(
    campground: Campground,
    pairs: list[tuple[date, date]],
) -> list[AvailableSlot]:
    """
    Open the park page once, fill in each Fri+Sat pair, capture the XHR.
    Reuses the same browser session to avoid re-loading/re-challenging Cloudflare.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    results: list[AvailableSlot] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        # Initial park page load
        url = _BASE_URL.format(park_id=campground.platform_id)
        log.debug("Loading %s", url)
        try:
            page.goto(url, wait_until="networkidle", timeout=45_000)
        except PWTimeout:
            log.warning("Timeout loading %s — continuing anyway", url)
        time.sleep(2)

        for friday, sunday in pairs:
            saturday = friday + timedelta(days=1)
            captured: list[dict] = []

            def _capture(response):
                url_r = response.url
                if any(p in url_r for p in _XHR_PATTERNS):
                    try:
                        body = response.json()
                        if isinstance(body, dict) and "Facility" in body:
                            captured.append(body)
                    except Exception:
                        pass

            page.on("response", _capture)

            # Try filling the arrival date — attempt several selector patterns
            checkin_str = friday.strftime("%m/%d/%Y")  # MM/DD/YYYY format
            filled = False
            for sel in [
                "input[placeholder*='rrival']",      # Arrival date
                "input[placeholder*='heck']",         # Check-in
                "input[placeholder*='tart']",         # Start date
                "input[ng-model*='arrival']",
                "input[ng-model*='ArrivalDate']",
                "input[id*='arrival']",
                "input[id*='Arrival']",
                "#ArrivalDate",
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=2_000):
                        loc.triple_click()
                        loc.fill(checkin_str)
                        loc.press("Tab")
                        filled = True
                        log.debug("Filled arrival via selector: %s", sel)
                        break
                except Exception:
                    continue

            if not filled:
                log.debug("Could not find arrival date input for %s", campground.name)

            # Set nights to 2 if possible
            for sel in [
                "select[ng-model*='nights']",
                "select[ng-model*='Nights']",
                "input[ng-model*='nights']",
                "#Nights", "#nights",
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=1_500):
                        loc.select_option("2") if loc.evaluate("e => e.tagName") == "SELECT" else loc.fill("2")
                        break
                except Exception:
                    continue

            # Click search / check availability button
            for sel in [
                "button:has-text('Search')",
                "button:has-text('Check Availability')",
                "button:has-text('Find')",
                "input[type='submit']",
                "a:has-text('Search')",
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=1_500):
                        btn.click()
                        log.debug("Clicked search button: %s", sel)
                        break
                except Exception:
                    continue

            # Wait for XHR to settle
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                pass
            time.sleep(2)

            page.remove_listener("response", _capture)

            if captured:
                log.debug("Captured %d XHR payload(s) for %s %s",
                          len(captured), campground.name, friday)
                for payload in captured:
                    results.extend(_parse_payload(payload, campground, friday, sunday))
            else:
                log.debug("No availability XHR captured for %s %s — site may be fully booked",
                          campground.name, friday)

            time.sleep(2)  # polite gap between searches

        ctx.close()
        browser.close()

    return results


def check(campground: Campground, cfg: Config) -> list[AvailableSlot]:
    pairs = friday_saturday_pairs(cfg.lookahead_weeks_min, cfg.lookahead_weeks_max)
    if not pairs:
        return []

    try:
        results = _check_one_park_all_dates(campground, pairs)
        log.info("ReserveCalifornia %s: %d total slots found", campground.name, len(results))
        return results
    except Exception as exc:
        log.error("ReserveCalifornia Playwright failed for %s: %s", campground.name, exc)
        return []
