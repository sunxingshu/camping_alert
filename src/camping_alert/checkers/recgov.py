"""
Recreation.gov availability checker.

Uses the public (no-key) availability API:
  GET https://www.recreation.gov/api/camps/availability/campground/{id}/month
      ?start_date=YYYY-MM-01T00:00:00.000Z

Returns per-site, per-date availability. We check two consecutive months
when a Fri+Sat pair straddles a month boundary.
"""

import logging
from datetime import date, timedelta
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..campgrounds import Campground, HookupType
from ..config import Config
from ..matcher import AvailableSlot
from .base import friday_saturday_pairs

log = logging.getLogger(__name__)

_BASE = "https://www.recreation.gov/api/camps/availability/campground"
_HEADERS = {
    "User-Agent": "camping-alert/0.1 (availability monitor; contact owner)",
    "Accept": "application/json",
}

# Recreation.gov availability strings that mean "open"
_AVAILABLE = {"Available"}

# Map RecGov campsite_type strings to our HookupType
_HOOKUP_MAP: dict[str, HookupType] = {
    "FULL HOOKUP": HookupType.FULL,
    "FULL HOOK UP": HookupType.FULL,
    "STANDARD ELECTRIC": HookupType.ELECTRIC,
    "ELECTRIC": HookupType.ELECTRIC,
    "WATER AND ELECTRIC": HookupType.PARTIAL,
    "PARTIAL HOOKUP": HookupType.PARTIAL,
}


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=16))
def _fetch_month(campground_id: str, month_start: date) -> dict[str, Any]:
    url = (
        f"{_BASE}/{campground_id}/month"
        f"?start_date={month_start.strftime('%Y-%m-01')}T00%3A00%3A00.000Z"
    )
    resp = httpx.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _months_needed(pairs: list[tuple[date, date]]) -> set[date]:
    months: set[date] = set()
    for friday, sunday in pairs:
        months.add(friday.replace(day=1))
        months.add((friday + timedelta(days=1)).replace(day=1))  # Saturday month
    return months


def _parse_hookup(campsite_type: str, amenities: dict) -> HookupType:
    upper = campsite_type.upper().strip()
    for key, hookup in _HOOKUP_MAP.items():
        if key in upper:
            return hookup
    # Fallback: check amenities dict if present
    if amenities.get("electricity") or amenities.get("electric"):
        return HookupType.ELECTRIC
    return HookupType.NONE


def check(campground: Campground, cfg: Config) -> list[AvailableSlot]:
    pairs = friday_saturday_pairs(cfg.lookahead_weeks_min, cfg.lookahead_weeks_max)
    if not pairs:
        return []

    # Fetch all needed months
    month_data: dict[date, dict] = {}
    for month_start in _months_needed(pairs):
        try:
            data = _fetch_month(campground.platform_id, month_start)
            month_data[month_start] = data.get("campsites", {})
            log.debug("RecGov %s: fetched %d sites for %s",
                      campground.platform_id, len(month_data[month_start]), month_start)
        except Exception as exc:
            log.warning("RecGov fetch failed for %s month %s: %s",
                        campground.platform_id, month_start, exc)

    results: list[AvailableSlot] = []

    for site_id, site_info in _combined_sites(month_data).items():
        availabilities: dict[str, str] = site_info.get("availabilities", {})

        for friday, sunday in pairs:
            saturday = friday + timedelta(days=1)
            fri_key = f"{friday.isoformat()}T00:00:00Z"
            sat_key = f"{saturday.isoformat()}T00:00:00Z"

            if (
                availabilities.get(fri_key) in _AVAILABLE
                and availabilities.get(sat_key) in _AVAILABLE
            ):
                hookup = _parse_hookup(
                    site_info.get("campsite_type", ""),
                    site_info.get("amenities", {}),
                )
                # RecGov doesn't always publish length; try loop name heuristic
                length = _extract_length(site_info)
                is_pull = _is_pull_through(site_info)

                results.append(AvailableSlot(
                    campground=campground,
                    site_id=site_id,
                    site_name=site_info.get("site", site_id),
                    checkin=friday,
                    checkout=sunday,
                    hookup_type=hookup,
                    has_dump_station=campground.has_dump_station,
                    site_length_ft=length,
                    is_pull_through=is_pull,
                    booking_url=(
                        f"https://www.recreation.gov/camping/campsites/{site_id}"
                    ),
                ))

    return results


def _combined_sites(month_data: dict[date, dict]) -> dict[str, Any]:
    """Merge site data from multiple months into one dict."""
    combined: dict[str, Any] = {}
    for sites in month_data.values():
        for site_id, info in sites.items():
            if site_id not in combined:
                combined[site_id] = dict(info)
            else:
                # Merge availabilities from both months
                combined[site_id]["availabilities"].update(
                    info.get("availabilities", {})
                )
    return combined


def _extract_length(site_info: dict) -> int | None:
    """Try to extract site length from description or type string."""
    for field in ("type_of_use", "campsite_type", "loop", "site"):
        text = str(site_info.get(field, ""))
        for token in text.split():
            token = token.strip("ft'\"")
            if token.isdigit():
                val = int(token)
                if 15 <= val <= 100:
                    return val
    return None


def _is_pull_through(site_info: dict) -> bool | None:
    text = " ".join(str(v) for v in site_info.values()).upper()
    if "PULL" in text or "PULL-THROUGH" in text or "PULL THROUGH" in text:
        return True
    if "BACK-IN" in text or "BACK IN" in text or "BACK UP" in text:
        return False
    return None
