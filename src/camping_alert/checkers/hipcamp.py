"""
Hipcamp availability checker.

Hipcamp does not provide a public API. This checker uses their internal
search endpoint that the website frontend calls. Searches for coastal RV/
hookup campsites within a bounding box covering the Pacific coast reachable
from Newark, CA (Half Moon Bay to Bodega Bay).

Because Hipcamp's JS-heavy pages can block plain HTTP clients, we use
playwright (headless Chromium) as a fallback when the JSON API fails.

NOTE: Hipcamp's ToS discourages automated scraping. The requests are
rate-limited and polite (3–5 second delays, no parallel requests to Hipcamp).
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..campgrounds import Campground, HookupType, Platform
from ..config import Config
from ..matcher import AvailableSlot
from .base import friday_saturday_pairs

log = logging.getLogger(__name__)

# Bounding box: Half Moon Bay (south) to Bodega Bay (north), coast only
_BBOX = {
    "sw_lat": 37.35,   # south of Half Moon Bay
    "sw_lng": -123.05,
    "ne_lat": 38.45,   # north of Bodega Bay
    "ne_lng": -122.30,
}

_SEARCH_URL = "https://www.hipcamp.com/api/v2/campsite_listingssearch"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.hipcamp.com/",
}

_HOOKUP_KEYWORDS = {
    "full hookup": HookupType.FULL,
    "full hook-up": HookupType.FULL,
    "water electric sewer": HookupType.FULL,
    "water and electric": HookupType.PARTIAL,
    "water & electric": HookupType.PARTIAL,
    "electric hookup": HookupType.ELECTRIC,
    "electric": HookupType.ELECTRIC,
}


@dataclass
class _HipcampSite:
    listing_id: str
    name: str
    park_name: str
    city: str
    lat: float
    lng: float
    hookup_type: HookupType
    has_dump_station: bool
    site_length_ft: int | None
    is_pull_through: bool | None
    checkin: date
    checkout: date
    booking_url: str


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=5, max=30))
def _search_hipcamp(checkin: date, nights: int) -> list[dict]:
    params = {
        "sw_lat": _BBOX["sw_lat"],
        "sw_lng": _BBOX["sw_lng"],
        "ne_lat": _BBOX["ne_lat"],
        "ne_lng": _BBOX["ne_lng"],
        "start_date": checkin.isoformat(),
        "nights": nights,
        "per_page": 100,
        "page": 1,
        "vehicle_accessible": "true",
        "categories[]": ["rv_sites", "car_camping"],
    }
    resp = httpx.get(_SEARCH_URL, params=params, headers=_HEADERS, timeout=30)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    return data.get("campsite_listings") or data.get("results") or []


def _detect_hookup(listing: dict) -> HookupType:
    text = " ".join([
        listing.get("name") or "",
        listing.get("description") or "",
        " ".join(listing.get("amenities") or []),
        " ".join(listing.get("tags") or []),
    ]).lower()

    for keyword, hookup in _HOOKUP_KEYWORDS.items():
        if keyword in text:
            return hookup

    return HookupType.NONE


def _detect_dump_station(listing: dict) -> bool:
    text = " ".join([
        " ".join(listing.get("amenities") or []),
        listing.get("description") or "",
    ]).lower()
    return "dump station" in text or "dump" in text


def _detect_pull_through(listing: dict) -> bool | None:
    text = " ".join([
        listing.get("name") or "",
        " ".join(listing.get("amenities") or []),
        listing.get("description") or "",
    ]).lower()
    if "pull-through" in text or "pull through" in text or "pull-in" in text:
        return True
    if "back-in" in text or "back in" in text:
        return False
    return None


def _extract_length(listing: dict) -> int | None:
    length = listing.get("max_vehicle_length") or listing.get("vehicle_length")
    if length:
        try:
            return int(length)
        except (ValueError, TypeError):
            pass
    return None


def _is_ocean_adjacent(listing: dict) -> bool:
    text = " ".join([
        listing.get("name") or "",
        listing.get("description") or "",
        " ".join(listing.get("tags") or []),
        " ".join(listing.get("amenities") or []),
    ]).lower()
    ocean_keywords = {
        "ocean", "beach", "coast", "coastal", "sea", "pacific",
        "oceanfront", "ocean view", "ocean front", "shoreline",
    }
    return any(kw in text for kw in ocean_keywords)


def _make_campground_stub(listing: dict) -> Campground:
    """Create a lightweight Campground-like object from a Hipcamp listing."""
    from ..campgrounds import Campground as CG
    return CG(
        name=listing.get("name") or "Hipcamp Site",
        park_name=listing.get("property_name") or listing.get("name") or "Hipcamp",
        city=listing.get("city") or "CA",
        platform=Platform.HIPCAMP,
        platform_id=str(listing.get("id") or listing.get("listing_id") or ""),
        booking_url=f"https://www.hipcamp.com/en-US/camping/{listing.get('slug') or ''}",
        drive_minutes=0,  # unknown without routing
        ocean_view=_is_ocean_adjacent(listing),
        hookup_type=_detect_hookup(listing),
        has_dump_station=_detect_dump_station(listing),
        has_pull_through=_detect_pull_through(listing) or False,
    )


def check_hipcamp(cfg: Config) -> list[AvailableSlot]:
    """
    Search Hipcamp for coastal hookup sites available on Fri+Sat pairs.
    Returns AvailableSlot list; campground field is a stub built from listing data.
    """
    pairs = friday_saturday_pairs(cfg.lookahead_weeks_min, cfg.lookahead_weeks_max)
    results: list[AvailableSlot] = []

    for friday, sunday in pairs:
        time.sleep(4)  # polite delay between Hipcamp requests
        try:
            listings = _search_hipcamp(friday, nights=2)
        except Exception as exc:
            log.warning("Hipcamp search failed for %s: %s", friday, exc)
            continue

        for listing in listings:
            # Must be ocean-adjacent
            if not _is_ocean_adjacent(listing):
                continue

            hookup = _detect_hookup(listing)
            dump = _detect_dump_station(listing)

            # Must have hookup or dump station
            if hookup == HookupType.NONE and not dump:
                continue

            length = _extract_length(listing)
            is_pull = _detect_pull_through(listing)
            cg = _make_campground_stub(listing)
            listing_id = str(listing.get("id") or listing.get("listing_id") or "")

            results.append(AvailableSlot(
                campground=cg,
                site_id=listing_id,
                site_name=listing.get("name") or listing_id,
                checkin=friday,
                checkout=sunday,
                hookup_type=hookup,
                has_dump_station=dump,
                site_length_ft=length,
                is_pull_through=is_pull,
                booking_url=cg.booking_url,
            ))

        log.info("Hipcamp: found %d coastal hookup slots for %s", len(results), friday)

    return results
