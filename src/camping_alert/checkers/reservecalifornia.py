"""
ReserveCalifornia (CA State Parks + some county parks) availability checker.

Uses the undocumented calirdr.usedirect.com JSON API that powers the
reservecalifornia.com frontend. No auth required for availability reads.

Key endpoints:
  POST https://calirdr.usedirect.com/rdr/rdr/search/grid
  GET  https://calirdr.usedirect.com/rdr/rdr/facility/{id}/units
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

_GRID_URL = "https://calirdr.usedirect.com/rdr/rdr/search/grid"
_UNITS_URL = "https://calirdr.usedirect.com/rdr/rdr/facility/{facility_id}/units"

_HEADERS = {
    "User-Agent": "camping-alert/0.1 (availability monitor; contact owner)",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://www.reservecalifornia.com",
    "Referer": "https://www.reservecalifornia.com/",
}

# Map ReserveCalifornia "UnitType" strings to HookupType
_HOOKUP_MAP: dict[str, HookupType] = {
    "full hookup": HookupType.FULL,
    "full hook-up": HookupType.FULL,
    "water, electric, sewer": HookupType.FULL,
    "water & electric": HookupType.PARTIAL,
    "water and electric": HookupType.PARTIAL,
    "electric": HookupType.ELECTRIC,
    "electrical": HookupType.ELECTRIC,
}

# Slot status values that mean "open"
_AVAILABLE_STATUSES = {"available", "open", "a"}


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=3, max=20))
def _post_grid(payload: dict) -> dict[str, Any]:
    resp = httpx.post(_GRID_URL, json=payload, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=3, max=20))
def _get_units(facility_id: str) -> dict[str, Any]:
    url = _UNITS_URL.format(facility_id=facility_id)
    resp = httpx.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _parse_hookup(unit: dict) -> HookupType:
    type_name = (unit.get("UnitTypeName") or "").lower()
    for key, hookup in _HOOKUP_MAP.items():
        if key in type_name:
            return hookup

    # Check amenity flags
    amenities = unit.get("Amenities") or []
    amenity_names = {(a.get("Name") or "").lower() for a in amenities}
    if "sewer" in amenity_names and "water" in amenity_names:
        return HookupType.FULL
    if "water" in amenity_names and "electric" in amenity_names:
        return HookupType.PARTIAL
    if "electric" in amenity_names or "electrical" in amenity_names:
        return HookupType.ELECTRIC

    return HookupType.NONE


def _extract_length(unit: dict) -> int | None:
    length = unit.get("MaxLength") or unit.get("VehicleLength")
    if length:
        try:
            return int(length)
        except (ValueError, TypeError):
            pass
    return None


def _is_pull_through(unit: dict) -> bool | None:
    type_name = (unit.get("UnitTypeName") or "").lower()
    site_name = (unit.get("Name") or "").lower()
    combined = type_name + " " + site_name
    if "pull" in combined:
        return True
    if "back" in combined:
        return False
    return None


def check(campground: Campground, cfg: Config) -> list[AvailableSlot]:
    pairs = friday_saturday_pairs(cfg.lookahead_weeks_min, cfg.lookahead_weeks_max)
    if not pairs:
        return []

    results: list[AvailableSlot] = []

    for friday, sunday in pairs:
        saturday = friday + timedelta(days=1)
        payload = {
            "StartDate": friday.strftime("%-m/%-d/%Y"),
            "Nights": 2,
            "PlaceId": int(campground.platform_id),
            "CountUnits": True,
            "WebOnly": True,
            "UnitTypeId": 0,
            "SleepingUnitId": 83,
            "MinVehicleLength": cfg.min_site_length_ft,
            "UnitTypesGroupIds": "",
            "InSeasonOnly": True,
            "IsADA": False,
            "HighlightedPlaceId": 0,
        }

        try:
            data = _post_grid(payload)
        except Exception as exc:
            log.warning("ReserveCalifornia grid failed for %s on %s: %s",
                        campground.name, friday, exc)
            continue

        # The response has a "Facility" key with nested "Units"
        facility = data.get("Facility") or {}
        units: dict = facility.get("Units") or {}

        for unit_id, unit in units.items():
            # Check both nights are available within the returned unit's slots
            slices: dict = unit.get("Slices") or {}
            fri_key = friday.strftime("%-m/%-d/%Y")
            sat_key = saturday.strftime("%-m/%-d/%Y")

            fri_slice = slices.get(fri_key) or {}
            sat_slice = slices.get(sat_key) or {}

            fri_avail = (fri_slice.get("IsFree") or
                         (fri_slice.get("Status") or "").lower() in _AVAILABLE_STATUSES)
            sat_avail = (sat_slice.get("IsFree") or
                         (sat_slice.get("Status") or "").lower() in _AVAILABLE_STATUSES)

            if not (fri_avail and sat_avail):
                continue

            hookup = _parse_hookup(unit)
            length = _extract_length(unit)
            is_pull = _is_pull_through(unit)
            site_name = unit.get("Name") or unit_id

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

    log.info("ReserveCalifornia %s: found %d matching Fri+Sat slots",
             campground.name, len(results))
    return results
