"""
ReserveCalifornia checker — curl_cffi TLS impersonation.

Cloudflare Bot Management blocks headless Playwright browsers (empty page,
0 XHR captured on GitHub Actions). curl_cffi impersonates Chrome's TLS
fingerprint at the network level, which bypasses Cloudflare's bot detection
without requiring JS execution.

Availability endpoint discovered from the reservecalifornia.com AngularJS app:
  GET /CaliforniaWebHome/Facilities/SearchViewUnitAvailabity.aspx
      ?facility_id=677&start_date=05/16/2025&nights=2&...

Set CAMPING_DEBUG=1 to log HTTP status codes and response previews.
"""

import logging
import os
from datetime import date, timedelta

from ..campgrounds import Campground, HookupType
from ..config import Config
from ..matcher import AvailableSlot
from .base import friday_saturday_pairs

log = logging.getLogger(__name__)
DEBUG = os.getenv("CAMPING_DEBUG", "").lower() in ("1", "true", "yes")

_BASE = "https://www.reservecalifornia.com"

# Candidate availability endpoints, tried in order.
_AVAIL_ENDPOINTS = [
    f"{_BASE}/CaliforniaWebHome/Facilities/SearchViewUnitAvailabity.aspx",
    f"{_BASE}/CaliforniaWebHome/Facilities/AdvanceSearchResults.aspx",
]

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
    from curl_cffi import requests as curl_req

    session = curl_req.Session(impersonate="chrome124")
    results: list[AvailableSlot] = []
    park_url = f"{_BASE}/Web/#!park/{campground.platform_id}"

    # Load the park page first to establish Cloudflare clearance cookies.
    try:
        r = session.get(park_url, timeout=25)
        if DEBUG:
            log.info("[DEBUG ReserveCA] base page %s -> HTTP %s", campground.platform_id, r.status_code)
    except Exception as exc:
        log.warning("ReserveCalifornia base page failed for %s: %s", campground.name, exc)

    api_headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": park_url,
        "Origin": _BASE,
    }

    for friday, sunday in pairs:
        checkin_str = friday.strftime("%m/%d/%Y")
        found_for_pair = False

        for endpoint in _AVAIL_ENDPOINTS:
            if found_for_pair:
                break
            params = {
                "facility_id": campground.platform_id,
                "start_date": checkin_str,
                "nights": 2,
                "unit_type_id": 0,
                "web_only": "true",
                "is_ada": "false",
                "in_season_only": "true",
            }
            try:
                resp = session.get(endpoint, params=params, headers=api_headers, timeout=25)
                if DEBUG:
                    log.info("[DEBUG ReserveCA] %s %s -> HTTP %s",
                             campground.platform_id, friday, resp.status_code)
                if resp.status_code != 200:
                    if DEBUG:
                        log.info("[DEBUG ReserveCA] body preview: %s", resp.text[:300])
                    continue
                body = resp.json()
                if _looks_like_availability(body):
                    slots = _parse_payload(body, campground, friday, sunday)
                    results.extend(slots)
                    found_for_pair = True
                    if DEBUG:
                        log.info("[DEBUG ReserveCA] %s %s: %d slots parsed from %s",
                                 campground.platform_id, friday, len(slots), endpoint)
                elif DEBUG:
                    log.info("[DEBUG ReserveCA] response not availability shape: %s",
                             str(body)[:200])
            except Exception as exc:
                log.debug("ReserveCalifornia endpoint %s failed for %s: %s",
                          endpoint, campground.name, exc)

    # Deduplicate
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
        log.error("ReserveCalifornia curl_cffi failed for %s: %s", campground.name, exc)
        return []
