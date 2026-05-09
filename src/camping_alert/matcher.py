"""Apply the 'golden filter' to raw availability results from checkers."""

from dataclasses import dataclass
from datetime import date

from .campgrounds import Campground, HookupType
from .config import Config


@dataclass
class AvailableSlot:
    """One campsite available for a specific Fri+Sat pair."""
    campground: Campground
    site_id: str
    site_name: str
    checkin: date       # Friday
    checkout: date      # Sunday (after Saturday night)
    hookup_type: HookupType
    has_dump_station: bool
    site_length_ft: int | None   # None = unknown / not published
    is_pull_through: bool | None # None = unknown
    booking_url: str

    @property
    def slot_id(self) -> str:
        return f"{self.campground.platform_id}:{self.site_id}:{self.checkin.isoformat()}"

    @property
    def length_confirmed(self) -> bool:
        return self.site_length_ft is not None


def matches(slot: AvailableSlot, cfg: Config) -> bool:
    """
    Return True when the slot satisfies every configured requirement.

    Rules (all must pass):
    1. Both nights available is enforced by checkers upstream.
    2. Ocean view — always required (only ocean campgrounds are in the list).
    3. Hookup: electric or full hookup present, OR (if REQUIRE_HOOKUP is false)
       at least a dump station is on-site.
    4. Dump station required when REQUIRE_DUMP_STATION is true.
    5. Site length >= MIN_SITE_LENGTH_FT when length is known.
    """

    # Hookup filter
    has_hookup = slot.hookup_type in (
        HookupType.FULL, HookupType.PARTIAL, HookupType.ELECTRIC
    )
    if cfg.require_hookup and not has_hookup:
        return False

    # Dump station filter (fallback for partial-hookup sites)
    if cfg.require_dump_station and not slot.has_dump_station:
        return False

    # If no hookup AND no dump station, always skip
    if not has_hookup and not slot.has_dump_station:
        return False

    # Length filter (only applied when data is available)
    if slot.site_length_ft is not None and slot.site_length_ft < cfg.min_site_length_ft:
        return False

    return True


def filter_slots(slots: list[AvailableSlot], cfg: Config) -> list[AvailableSlot]:
    return [s for s in slots if matches(s, cfg)]
