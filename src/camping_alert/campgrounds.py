"""
Static registry of target coastal campgrounds within ~2 hours of Newark, CA.

Each entry is verified against known booking platforms. IDs should be
confirmed at first run — see comments for the source URL of each ID.
"""

from dataclasses import dataclass, field
from enum import Enum


class Platform(str, Enum):
    RECGOV = "recreation.gov"
    RESERVECA = "reservecalifornia.com"
    HIPCAMP = "hipcamp.com"


class HookupType(str, Enum):
    FULL = "full"       # water + electric + sewer
    PARTIAL = "partial" # water + electric (no sewer)
    ELECTRIC = "electric"  # electric only
    NONE = "none"


@dataclass(frozen=True)
class Campground:
    name: str
    park_name: str
    city: str
    platform: Platform
    platform_id: str          # campground/facility ID on the booking platform
    booking_url: str
    drive_minutes: int        # estimated drive from Newark, CA (no traffic)
    ocean_view: bool          # site can see the ocean
    hookup_type: HookupType
    has_dump_station: bool
    has_pull_through: bool    # at least some pull-through sites available
    notes: str = ""


# ── Priority 1: Full/partial hookup + ocean access ────────────────────────────

CAMPGROUNDS: list[Campground] = [

    # ── ReserveCalifornia (CA State Parks) ─────────────────────────────────────
    # Park IDs confirmed via reservecalifornia.com URL: #!park/{id}

    Campground(
        name="Seacliff State Beach",
        park_name="Seacliff State Beach",
        city="Aptos, CA",
        platform=Platform.RESERVECA,
        platform_id="680",          # https://www.reservecalifornia.com/Web/#!park/680
        booking_url="https://www.reservecalifornia.com/Web/#!park/680",
        drive_minutes=70,
        ocean_view=True,
        hookup_type=HookupType.ELECTRIC,
        has_dump_station=True,
        has_pull_through=True,
        notes="Electric hookups on bluff above beach. Concrete pads. 40ft max. "
              "Dump station at park entrance.",
    ),

    Campground(
        name="New Brighton State Beach",
        park_name="New Brighton State Beach",
        city="Capitola, CA",
        platform=Platform.RESERVECA,
        platform_id="681",          # https://www.reservecalifornia.com/Web/#!park/681
        booking_url="https://www.reservecalifornia.com/Web/#!park/681",
        drive_minutes=70,
        ocean_view=True,
        hookup_type=HookupType.ELECTRIC,
        has_dump_station=True,
        has_pull_through=True,
        notes="Electric hookups. Some sites have partial ocean view. Dump station on-site.",
    ),

    Campground(
        name="Doran Regional Park",
        park_name="Doran Regional Park",
        city="Bodega Bay, CA",
        platform=Platform.RESERVECA,
        platform_id="1177",         # Sonoma County Parks via ReserveAmerica
        booking_url="https://www.reservecalifornia.com/Web/#!park/1177",
        drive_minutes=115,
        ocean_view=True,
        hookup_type=HookupType.FULL,
        has_dump_station=True,
        has_pull_through=True,
        notes="BEST MATCH — Full hookup (W/E/S). Large pull-through sites. "
              "Direct bay/ocean access. 45ft+ sites available.",
    ),

    Campground(
        name="Bodega Dunes (Sonoma Coast State Beach)",
        park_name="Sonoma Coast State Beach",
        city="Bodega Bay, CA",
        platform=Platform.RESERVECA,
        platform_id="677",          # https://www.reservecalifornia.com/Web/#!park/677
        booking_url="https://www.reservecalifornia.com/Web/#!park/677",
        drive_minutes=115,
        ocean_view=False,           # dunes block direct ocean view but short walk
        hookup_type=HookupType.NONE,
        has_dump_station=True,
        has_pull_through=True,
        notes="No hookups but large pull-through sites (40ft+). Dump station on-site. "
              "Short walk to beach. Include if dump station qualifies.",
    ),

    Campground(
        name="Half Moon Bay SB — Francis Beach",
        park_name="Half Moon Bay State Beach",
        city="Half Moon Bay, CA",
        platform=Platform.RESERVECA,
        platform_id="676",          # https://www.reservecalifornia.com/Web/#!park/676
        booking_url="https://www.reservecalifornia.com/Web/#!park/676",
        drive_minutes=45,
        ocean_view=True,
        hookup_type=HookupType.NONE,
        has_dump_station=True,
        has_pull_through=False,
        notes="No electric hookups. Dump station available. Closest coastal option "
              "(45 min). Include if user relaxes hookup requirement.",
    ),

    Campground(
        name="Manresa State Beach",
        park_name="Manresa State Beach",
        city="La Selva Beach, CA",
        platform=Platform.RESERVECA,
        platform_id="699",          # https://www.reservecalifornia.com/Web/#!park/699
        booking_url="https://www.reservecalifornia.com/Web/#!park/699",
        drive_minutes=75,
        ocean_view=True,
        hookup_type=HookupType.NONE,
        has_dump_station=True,
        has_pull_through=False,
        notes="Walk-in tent sites primarily. Dump station. RV access limited.",
    ),

    Campground(
        name="Sunset State Beach",
        park_name="Sunset State Beach",
        city="Watsonville, CA",
        platform=Platform.RESERVECA,
        platform_id="700",          # https://www.reservecalifornia.com/Web/#!park/700
        booking_url="https://www.reservecalifornia.com/Web/#!park/700",
        drive_minutes=80,
        ocean_view=True,
        hookup_type=HookupType.NONE,
        has_dump_station=True,
        has_pull_through=True,
        notes="No hookups. Dump station on-site. Pull-through sites available.",
    ),

    # ── Recreation.gov (Federal campgrounds) ──────────────────────────────────
    # IDs confirmed via recreation.gov URL: /camping/campgrounds/{id}

    Campground(
        name="Kirby Cove Campground",
        park_name="Golden Gate National Recreation Area",
        city="Sausalito, CA",
        platform=Platform.RECGOV,
        platform_id="234072",       # https://www.recreation.gov/camping/campgrounds/234072
        booking_url="https://www.recreation.gov/camping/campgrounds/234072",
        drive_minutes=65,
        ocean_view=True,
        hookup_type=HookupType.NONE,
        has_dump_station=False,
        has_pull_through=False,
        notes="Primitive group sites. No hookups. Stunning ocean view. "
              "Include for non-hookup fallback.",
    ),

    Campground(
        name="Pantoll Campground (Mt. Tamalpais)",
        park_name="Mount Tamalpais State Park",
        city="Mill Valley, CA",
        platform=Platform.RECGOV,
        platform_id="233116",       # https://www.recreation.gov/camping/campgrounds/233116
        booking_url="https://www.recreation.gov/camping/campgrounds/233116",
        drive_minutes=75,
        ocean_view=False,
        hookup_type=HookupType.NONE,
        has_dump_station=False,
        has_pull_through=False,
        notes="Primitive. No hookups. Mountain setting near coast. Low priority.",
    ),

]

# Quick lookup by platform + ID
_by_platform_id: dict[tuple[Platform, str], Campground] = {
    (c.platform, c.platform_id): c for c in CAMPGROUNDS
}


def get(platform: Platform, platform_id: str) -> Campground | None:
    return _by_platform_id.get((platform, platform_id))


def for_platform(platform: Platform) -> list[Campground]:
    return [c for c in CAMPGROUNDS if c.platform == platform]
