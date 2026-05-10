"""
ReserveCalifornia checker — Playwright-based.

The old calirdr.usedirect.com API is decommissioned. The current
reservecalifornia.com site is Cloudflare-protected. We use a headless
Chromium browser that passes the JS challenge, then intercepts ALL
JSON XHR/fetch responses to find availability payloads.

Set env var CAMPING_DEBUG=1 to log every XHR URL captured.
"""

import json
import logging
import os
import time
from datetime import date, timedelta

from ..campgrounds import Campground, HookupType
from ..config import Config
from ..matcher import AvailableSlot
from .base import friday_saturday_pairs

log = logging.getLogger(__name__)
DEBUG = os.getenv("CAMPING_DEBUG", "").lower() in ("1", "true", "yes")
_EXECUTABLE = os.getenv("PLAYWRIGHT_EXECUTABLE_PATH") or None

_BASE_URL = "https://www.reservecalifornia.com/Web/#!park/{park_id}"

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


def _looks_like_availability(body: dict) -> bool:
    """Return True if JSON body looks like an availability payload."""
    return (
        "Facility" in body
        or "Units" in body
        or "units" in body
        or "availability" in str(body)[:200].lower()
        or "Slices" in str(body)[:200]
    )


def _parse_payload(payload: dict, campground: Campground,
                   friday: date, sunday: date) -> list[AvailableSlot]:
    results: list[AvailableSlot] = []
    saturday = friday + timedelta(days=1)
    fri_str = friday.strftime("%-m/%-d/%Y")
    sat_str = saturday.strftime("%-m/%-d/%Y")

    facility = payload.get("Facility") or {}
    units: dict = facility.get("Units") or payload.get("Units") or {}

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
        try:
            raw_len = unit.get("MaxLength") or unit.get("VehicleLength")
            if raw_len:
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


def _check_one_park(campground: Campground, pairs: list[tuple[date, date]]) -> list[AvailableSlot]:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    results: list[AvailableSlot] = []

    with sync_playwright() as p:
        launch_kwargs: dict = dict(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--ignore-certificate-errors"],
        )
        if _EXECUTABLE:
            launch_kwargs["executable_path"] = _EXECUTABLE
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()

        # ── Capture ALL JSON XHR/fetch responses ──────────────────────────────
        # We log every URL in debug mode so we can discover the right patterns.
        all_payloads: list[tuple[str, dict]] = []   # (url, body)

        def _capture(response):
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            if DEBUG:
                log.info("[DEBUG] XHR: %s  %s", response.status, response.url[:120])
            try:
                body = response.json()
                if isinstance(body, dict):
                    all_payloads.append((response.url, body))
            except Exception:
                pass

        page.on("response", _capture)

        url = _BASE_URL.format(park_id=campground.platform_id)
        log.debug("Loading %s", url)
        try:
            page.goto(url, wait_until="networkidle", timeout=45_000)
        except PWTimeout:
            log.warning("Timeout loading park page for %s", campground.name)

        if DEBUG:
            log.info("[DEBUG] Page title: %r", page.title())
            log.info("[DEBUG] Page URL: %s", page.url)
            # Save screenshot so we can see what Cloudflare/the site actually renders
            try:
                shot_path = f"/tmp/reserveca_debug_{campground.platform_id}.png"
                page.screenshot(path=shot_path, full_page=False)
                log.info("[DEBUG] Screenshot saved: %s", shot_path)
            except Exception as ex:
                log.info("[DEBUG] Screenshot failed: %s", ex)
            # Log first 500 chars of page content
            try:
                content_preview = page.content()[:500].replace("\n", " ")
                log.info("[DEBUG] Page content preview: %s", content_preview)
            except Exception:
                pass

        time.sleep(2)

        # ── Try to trigger a date search for each Fri+Sat pair ────────────────
        for friday, sunday in pairs:
            checkin_str = friday.strftime("%m/%d/%Y")
            pair_payloads: list[dict] = []
            snap = len(all_payloads)

            # Try filling arrival date with several selector patterns
            for sel in [
                "input[placeholder*='rrival']",
                "input[placeholder*='heck-in']",
                "input[placeholder*='tart']",
                "input[ng-model*='arrival']",
                "input[ng-model*='Arrival']",
                "input[ng-model*='ArrivalDate']",
                "#ArrivalDate",
                "input[id*='arrival' i]",
                "input[type='date']",
                "input[type='text']:first-of-type",
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=1_500):
                        loc.triple_click()
                        loc.fill(checkin_str)
                        loc.press("Tab")
                        if DEBUG:
                            log.info("[DEBUG] Filled arrival with selector: %s", sel)
                        break
                except Exception:
                    continue

            # Try clicking Search / Check Availability
            for sel in [
                "button:has-text('Search')",
                "button:has-text('Check Availability')",
                "button:has-text('Find')",
                "[ng-click*='search' i]",
                "[ng-click*='Search']",
                "input[type='submit']",
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=1_500):
                        btn.click()
                        if DEBUG:
                            log.info("[DEBUG] Clicked: %s", sel)
                        break
                except Exception:
                    continue

            # Wait for new XHR to settle
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except PWTimeout:
                pass
            time.sleep(2)

            # Collect new payloads captured since the snap
            new_payloads = [body for _, body in all_payloads[snap:]]
            avail_payloads = [b for b in new_payloads if _looks_like_availability(b)]

            if DEBUG:
                log.info("[DEBUG] %s %s: %d new XHR, %d look like availability",
                         campground.name, friday, len(new_payloads), len(avail_payloads))

            for payload in avail_payloads:
                results.extend(_parse_payload(payload, campground, friday, sunday))

            time.sleep(1)

        # ── Also try parsing any availability payload captured on initial load ─
        initial_avail = [b for _, b in all_payloads if _looks_like_availability(b)]
        if DEBUG:
            log.info("[DEBUG] Total XHR captured: %d  Availability-like: %d",
                     len(all_payloads), len(initial_avail))
        for payload in initial_avail:
            for friday, sunday in pairs:
                slots = _parse_payload(payload, campground, friday, sunday)
                results.extend(slots)

        ctx.close()
        browser.close()

    # Deduplicate by slot_id
    seen: set[str] = set()
    unique: list[AvailableSlot] = []
    for s in results:
        if s.slot_id not in seen:
            seen.add(s.slot_id)
            unique.append(s)
    return unique


def check(campground: Campground, cfg: Config) -> list[AvailableSlot]:
    pairs = friday_saturday_pairs(cfg.lookahead_weeks_min, cfg.lookahead_weeks_max)
    if not pairs:
        return []
    try:
        results = _check_one_park(campground, pairs)
        log.info("ReserveCalifornia %s: %d total slots found", campground.name, len(results))
        return results
    except Exception as exc:
        log.error("ReserveCalifornia Playwright failed for %s: %s", campground.name, exc)
        return []
