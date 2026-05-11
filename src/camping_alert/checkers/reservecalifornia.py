"""
ReserveCalifornia checker — curl_cffi TLS impersonation.

Cloudflare Bot Management blocked headless Playwright. curl_cffi impersonates
Chrome's TLS fingerprint, bypassing bot detection without JS execution.

The old ASPX endpoint is gone. We discover the live API base URL from the
SPA's configuration file (assets/env.json or similar), then query the
availability endpoint directly.

Set CAMPING_DEBUG=1 to log HTTP status codes and response previews.
"""

import logging
import os
import re
from datetime import date, timedelta

from ..campgrounds import Campground, HookupType
from ..config import Config
from ..matcher import AvailableSlot
from .base import friday_saturday_pairs

log = logging.getLogger(__name__)
DEBUG = os.getenv("CAMPING_DEBUG", "").lower() in ("1", "true", "yes")

_BASE = "https://www.reservecalifornia.com"

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
                f"{_BASE}/Web/#!park/"
                f"{campground.platform_id}/unit/{unit_id}"
            ),
        ))

    return results


def _discover_api_base(session) -> str | None:
    """
    Try to discover the API base URL from SPA config files and HTML.
    Angular SPAs commonly expose /assets/env.json or /config.json with the
    API base URL, or embed it in inline <script> tags.
    """
    # Common SPA config file paths
    for path in ("/assets/env.json", "/config.json", "/app-config.json",
                 "/assets/config.json", "/environment.json"):
        try:
            r = session.get(_BASE + path, timeout=10)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                data = r.json()
                if isinstance(data, dict):
                    for key in ("apiUrl", "apiBaseUrl", "baseUrl", "api_url",
                                "API_URL", "serviceUrl", "webApiUrl"):
                        val = data.get(key)
                        if val and isinstance(val, str):
                            log.info("ReserveCalifornia: API base found in %s: %s", path, val)
                            return val.rstrip("/")
        except Exception:
            pass

    # Scan main page HTML for inline config / API URL hints
    try:
        r = session.get(_BASE + "/", timeout=20)
        if DEBUG:
            log.info("[DEBUG ReserveCA] root page -> HTTP %s (len %d)", r.status_code, len(r.text))
        if r.status_code == 200:
            for pattern in (
                r'(?:apiUrl|apiBaseUrl|baseUrl|serviceUrl)\s*[=:]\s*["\']([^"\']{10,120})["\']',
                r'https?://[a-zA-Z0-9._-]+(?:reservecalifornia|usedirect)[a-zA-Z0-9._/-]*/api[a-zA-Z0-9._/-]*',
            ):
                matches = re.findall(pattern, r.text, re.I)
                if matches:
                    url = matches[0].rstrip("/")
                    log.info("ReserveCalifornia: API base found in page HTML: %s", url)
                    return url
    except Exception:
        pass

    return None


def _availability_endpoints(api_base: str | None, facility_id: str) -> list[tuple[str, dict]]:
    """
    Return a list of (url, params) pairs to try for availability.
    Ordered by likelihood of success on the current platform.
    """
    endpoints = []

    if api_base:
        endpoints += [
            (f"{api_base}/Facilities/SearchViewUnitAvailabity", {}),
            (f"{api_base}/availability/campground/{facility_id}", {}),
        ]

    # Classic Active Network ASPX endpoint (may still work on some deployments)
    endpoints += [
        (f"{_BASE}/CaliforniaWebHome/Facilities/SearchViewUnitAvailabity.aspx", {}),
        (f"{_BASE}/api/Facilities/SearchViewUnitAvailabity", {}),
        (f"{_BASE}/api/availability/campground/{facility_id}", {}),
    ]

    return endpoints


def _check_one_park(campground: Campground, pairs: list[tuple[date, date]]) -> list[AvailableSlot]:
    from curl_cffi import requests as curl_req

    session = curl_req.Session(impersonate="chrome124")
    results: list[AvailableSlot] = []
    park_url = f"{_BASE}/Web/#!park/{campground.platform_id}"

    # Try several park page URLs — site moved from /Web/#!park/{id} to root
    for candidate_url in [
        park_url,
        f"{_BASE}/#!park/{campground.platform_id}",
        f"{_BASE}/",
        _BASE,
    ]:
        try:
            r = session.get(candidate_url, timeout=25)
            if DEBUG:
                log.info("[DEBUG ReserveCA] park page %s -> HTTP %s (len %d) url=%s",
                         campground.platform_id, r.status_code, len(r.text), candidate_url)
            if r.status_code == 200:
                park_url = candidate_url
                break
        except Exception as exc:
            log.warning("ReserveCalifornia base page failed for %s at %s: %s",
                        campground.name, candidate_url, exc)

    # Try to learn the real API base URL from SPA config/HTML
    api_base = _discover_api_base(session)
    if DEBUG:
        log.info("[DEBUG ReserveCA] discovered api_base: %s", api_base)

    endpoints = _availability_endpoints(api_base, campground.platform_id)

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

        for url, extra_params in endpoints:
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
                **extra_params,
            }
            try:
                resp = session.get(url, params=params, headers=api_headers, timeout=25)
                ct = resp.headers.get("content-type", "")
                if DEBUG:
                    log.info("[DEBUG ReserveCA] %s %s -> HTTP %s ct=%s",
                             campground.platform_id, friday, resp.status_code, ct[:40])
                if resp.status_code != 200 or "json" not in ct:
                    if DEBUG and resp.status_code != 200:
                        log.info("[DEBUG ReserveCA] body preview: %s", resp.text[:200])
                    continue
                body = resp.json()
                if _looks_like_availability(body):
                    slots = _parse_payload(body, campground, friday, sunday)
                    results.extend(slots)
                    found_for_pair = True
                    if DEBUG:
                        log.info("[DEBUG ReserveCA] %s %s: %d slots from %s",
                                 campground.platform_id, friday, len(slots), url)
                elif DEBUG:
                    log.info("[DEBUG ReserveCA] JSON but not availability shape: %s",
                             str(body)[:200])
            except Exception as exc:
                log.debug("ReserveCalifornia %s failed for %s %s: %s",
                          url, campground.name, friday, exc)

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
