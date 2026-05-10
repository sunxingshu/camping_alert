"""
Hipcamp availability checker — Playwright-based.

XHR debug revealed two working endpoints:
  1. /_next/data/{buildId}/en-US/search.json?{bbox+date params}  ← primary
  2. /graphql/search                                              ← fallback

We intercept these in a headless browser that passes Cloudflare's
JS challenge, then parse listings for ocean-adjacent hookup sites.
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
_EXECUTABLE = os.getenv("PLAYWRIGHT_EXECUTABLE_PATH") or None

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
    m = re.search(r'(\d{2,3})\s*(?:ft|foot|feet)', text, re.I)
    if m:
        val = int(m.group(1))
        if 15 <= val <= 120:
            return val
    return None


def _listing_text(listing: dict) -> str:
    return " ".join(filter(None, [
        listing.get("name") or "",
        listing.get("description") or "",
        " ".join(listing.get("amenities") or []),
        " ".join(listing.get("tags") or []),
        listing.get("city") or "",
    ]))


def _make_stub(listing: dict) -> Campground:
    pid = str(listing.get("id") or listing.get("listing_id") or "unknown")
    slug = listing.get("slug") or listing.get("url_slug") or pid
    text = _listing_text(listing)
    return Campground(
        name=listing.get("name") or "Hipcamp site",
        park_name=listing.get("property_name") or listing.get("name") or "Hipcamp",
        city=listing.get("city") or "CA",
        platform=Platform.HIPCAMP,
        platform_id=pid,
        booking_url=f"https://www.hipcamp.com/en-US/camping/{slug}",
        drive_minutes=0,
        ocean_view=_is_ocean(text),
        hookup_type=_infer_hookup(text),
        has_dump_station=_has_dump(text),
        has_pull_through=("pull" in text.lower()),
    )


def _slot_from_listing(listing: dict, friday: date, sunday: date) -> AvailableSlot | None:
    if not isinstance(listing, dict):
        return None
    text = _listing_text(listing)
    if not _is_ocean(text):
        return None
    hookup = _infer_hookup(text)
    dump = _has_dump(text)
    if hookup == HookupType.NONE and not dump:
        return None
    cg = _make_stub(listing)
    pid = cg.platform_id
    return AvailableSlot(
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
    )


def _extract_listings(payload: dict) -> list[dict]:
    """Pull a flat list of listing dicts from any known Hipcamp response shape."""
    candidates = [
        # Next.js _next/data shape
        payload.get("pageProps", {}).get("initialListings"),
        payload.get("pageProps", {}).get("listings"),
        payload.get("pageProps", {}).get("searchResults"),
        # Direct REST shape
        payload.get("campsite_listings"),
        payload.get("listings"),
        payload.get("results"),
        # GraphQL shape
        payload.get("data", {}).get("listings"),
        payload.get("data", {}).get("searchListings"),
        payload.get("data", {}).get("campsiteListings"),
    ]
    for lst in candidates:
        if isinstance(lst, list) and lst:
            return lst
    return []


def check_hipcamp(cfg: Config) -> list[AvailableSlot]:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    pairs = friday_saturday_pairs(cfg.lookahead_weeks_min, cfg.lookahead_weeks_max)
    results: list[AvailableSlot] = []
    seen_ids: set[str] = set()

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

        for friday, sunday in pairs:
            checkin_str = friday.isoformat()
            url = _SEARCH_URL.format(checkin=checkin_str)
            captured_payloads: list[dict] = []

            def _on_response(response):
                ru = response.url
                ct = response.headers.get("content-type", "")
                if "json" not in ct:
                    return
                # Only capture Hipcamp's own data endpoints
                if "hipcamp.com" not in ru and "api.hipcamp" not in ru:
                    return
                if DEBUG:
                    log.info("[DEBUG Hipcamp] XHR: %s %s", response.status, ru[:120])
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        captured_payloads.append(body)
                except Exception:
                    pass

            page.on("response", _on_response)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=35_000)
                # Give JS time to fire the data fetches
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                log.debug("Hipcamp page load partial timeout for %s — continuing", friday)

            time.sleep(2)
            page.remove_listener("response", _on_response)

            if DEBUG:
                log.info("[DEBUG Hipcamp] %s: captured %d payloads", friday, len(captured_payloads))

            for payload in captured_payloads:
                for listing in _extract_listings(payload):
                    slot = _slot_from_listing(listing, friday, sunday)
                    if slot and slot.slot_id not in seen_ids:
                        seen_ids.add(slot.slot_id)
                        results.append(slot)

            time.sleep(3)

        ctx.close()
        browser.close()

    log.info("Hipcamp: %d total coastal hookup slots found", len(results))
    return results
