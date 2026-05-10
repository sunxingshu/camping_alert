"""
Hipcamp availability checker — Playwright-based.

Hipcamp's API endpoints return 403 (Cloudflare) or 404 to plain HTTP clients.
We use a headless Chromium browser to load their search page for coastal RV/
hookup sites within the Half Moon Bay → Bodega Bay bounding box, then:
  1. Intercept the XHR/fetch JSON responses (they use an internal REST or
     GraphQL API loaded by their React frontend).
  2. Fall back to parsing listing cards from the rendered DOM if XHR
     interception yields nothing.

Set CAMPING_DEBUG=1 to log every XHR URL and DOM card found.
"""

import logging
import os
import time
from datetime import date, timedelta

from ..campgrounds import Campground, HookupType, Platform
from ..config import Config
from ..matcher import AvailableSlot
from .base import friday_saturday_pairs

log = logging.getLogger(__name__)
DEBUG = os.getenv("CAMPING_DEBUG", "").lower() in ("1", "true", "yes")

# Coastal bounding box: Half Moon Bay (south) to Bodega Bay (north)
_SEARCH_URL = (
    "https://www.hipcamp.com/en-US/search"
    "?ne_lat=38.45&ne_lng=-122.30"
    "&sw_lat=37.35&sw_lng=-123.05"
    "&start_date={checkin}&nights=2"
    "&categories=rv_sites,car_camping"
    "&vehicle_accessible=true"
)

_HOOKUP_KEYWORDS: dict[str, HookupType] = {
    "full hookup": HookupType.FULL,
    "full hook": HookupType.FULL,
    "water, electric, sewer": HookupType.FULL,
    "water/electric/sewer": HookupType.FULL,
    "water & electric": HookupType.PARTIAL,
    "water and electric": HookupType.PARTIAL,
    "electric hookup": HookupType.ELECTRIC,
    "electric": HookupType.ELECTRIC,
}

_OCEAN_KEYWORDS = {
    "ocean", "beach", "coast", "coastal", "pacific", "sea",
    "oceanfront", "ocean view", "shoreline", "seaside",
}

_DUMP_KEYWORDS = {"dump station", "dump"}


def _infer_hookup(text: str) -> HookupType:
    lower = text.lower()
    for kw, ht in _HOOKUP_KEYWORDS.items():
        if kw in lower:
            return ht
    return HookupType.NONE


def _is_ocean(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _OCEAN_KEYWORDS)


def _has_dump(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _DUMP_KEYWORDS)


def _extract_length(text: str) -> int | None:
    import re
    # e.g. "40 ft", "40ft", "40-foot"
    m = re.search(r'(\d{2,3})\s*(?:ft|foot|feet)', text, re.I)
    if m:
        val = int(m.group(1))
        if 15 <= val <= 120:
            return val
    return None


def _make_stub(listing: dict, platform_id: str) -> Campground:
    text = " ".join([
        listing.get("name") or "",
        listing.get("description") or "",
        " ".join(listing.get("amenities") or []),
        " ".join(listing.get("tags") or []),
    ])
    return Campground(
        name=listing.get("name") or "Hipcamp site",
        park_name=listing.get("property_name") or listing.get("name") or "Hipcamp",
        city=listing.get("city") or "CA",
        platform=Platform.HIPCAMP,
        platform_id=platform_id,
        booking_url=(
            f"https://www.hipcamp.com/en-US/camping/"
            f"{listing.get('slug') or platform_id}"
        ),
        drive_minutes=0,
        ocean_view=_is_ocean(text),
        hookup_type=_infer_hookup(text),
        has_dump_station=_has_dump(text),
        has_pull_through=("pull" in text.lower()),
    )


def _parse_xhrs(payloads: list[dict], friday: date, sunday: date) -> list[AvailableSlot]:
    results: list[AvailableSlot] = []
    for payload in payloads:
        # Hipcamp uses different response shapes depending on endpoint version
        listings = (
            payload.get("campsite_listings")
            or payload.get("listings")
            or payload.get("results")
            or payload.get("data", {}).get("listings")
            or []
        )
        if not isinstance(listings, list):
            continue

        for listing in listings:
            text = " ".join([
                listing.get("name") or "",
                listing.get("description") or "",
                " ".join(listing.get("amenities") or []),
                " ".join(listing.get("tags") or []),
            ])
            if not _is_ocean(text):
                continue

            hookup = _infer_hookup(text)
            dump = _has_dump(text)
            if hookup == HookupType.NONE and not dump:
                continue

            pid = str(listing.get("id") or listing.get("listing_id") or "unknown")
            cg = _make_stub(listing, pid)
            results.append(AvailableSlot(
                campground=cg,
                site_id=pid,
                site_name=listing.get("name") or pid,
                checkin=friday,
                checkout=sunday,
                hookup_type=hookup,
                has_dump_station=dump,
                site_length_ft=_extract_length(text),
                is_pull_through=True if "pull" in text.lower() else None,
                booking_url=cg.booking_url,
            ))
    return results


def _parse_dom_cards(page, friday: date, sunday: date) -> list[AvailableSlot]:
    """
    Fallback: scrape listing cards rendered in the DOM.
    Hipcamp renders cards with class names that vary by build — we use
    broad attribute selectors and read visible text.
    """
    results: list[AvailableSlot] = []
    try:
        cards = page.locator("[data-testid*='listing'], [class*='ListingCard'], [class*='listing-card']").all()
        if DEBUG:
            log.info("[DEBUG Hipcamp] DOM cards found: %d", len(cards))
        for card in cards:
            try:
                text = card.inner_text()
                href = card.locator("a").first.get_attribute("href") or ""
                if not _is_ocean(text):
                    continue
                hookup = _infer_hookup(text)
                dump = _has_dump(text)
                if hookup == HookupType.NONE and not dump:
                    continue
                name = text.split("\n")[0].strip()[:80]
                pid = href.split("/")[-1] or name
                url = f"https://www.hipcamp.com{href}" if href.startswith("/") else href
                cg = Campground(
                    name=name, park_name=name, city="CA",
                    platform=Platform.HIPCAMP, platform_id=pid,
                    booking_url=url, drive_minutes=0,
                    ocean_view=True, hookup_type=hookup,
                    has_dump_station=dump, has_pull_through="pull" in text.lower(),
                )
                results.append(AvailableSlot(
                    campground=cg, site_id=pid, site_name=name,
                    checkin=friday, checkout=sunday,
                    hookup_type=hookup, has_dump_station=dump,
                    site_length_ft=_extract_length(text),
                    is_pull_through=True if "pull" in text.lower() else None,
                    booking_url=url,
                ))
            except Exception:
                continue
    except Exception as exc:
        log.debug("DOM card parse failed: %s", exc)
    return results


def check_hipcamp(cfg: Config) -> list[AvailableSlot]:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    pairs = friday_saturday_pairs(cfg.lookahead_weeks_min, cfg.lookahead_weeks_max)
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

        for friday, sunday in pairs:
            checkin_str = friday.isoformat()
            url = _SEARCH_URL.format(checkin=checkin_str)
            captured: list[dict] = []

            def _on_response(response):
                ct = response.headers.get("content-type", "")
                if "json" not in ct:
                    return
                if DEBUG:
                    log.info("[DEBUG Hipcamp] XHR: %s %s", response.status, response.url[:120])
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        captured.append(body)
                except Exception:
                    pass

            page.on("response", _on_response)

            try:
                page.goto(url, wait_until="networkidle", timeout=40_000)
            except PWTimeout:
                log.warning("Hipcamp timeout for %s", friday)

            time.sleep(3)
            page.remove_listener("response", _on_response)

            # Try XHR payloads first
            slots = _parse_xhrs(captured, friday, sunday)
            if not slots:
                # Fallback: scrape DOM cards
                slots = _parse_dom_cards(page, friday, sunday)

            if DEBUG:
                log.info("[DEBUG Hipcamp] %s: %d XHR payloads, %d slots", friday, len(captured), len(slots))

            results.extend(slots)
            time.sleep(3)   # polite gap

        ctx.close()
        browser.close()

    log.info("Hipcamp: %d total coastal hookup slots found", len(results))
    return results
