"""Load and validate runtime configuration from .env / environment."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".env", override=False)


@dataclass(frozen=True)
class Config:
    alert_from_email: str
    alert_from_password: str
    alert_to_email: str
    smtp_host: str
    smtp_port: int

    lookahead_weeks_min: int
    lookahead_weeks_max: int

    check_interval_minutes: int
    peak_interval_minutes: int
    active_hours_start: int
    active_hours_end: int

    min_site_length_ft: int
    require_hookup: bool
    require_dump_station: bool


def load() -> Config:
    def _require(key: str) -> str:
        val = os.getenv(key, "").strip()
        if not val:
            raise RuntimeError(f"Missing required env var: {key}")
        return val

    def _int(key: str, default: int) -> int:
        return int(os.getenv(key, str(default)))

    def _bool(key: str, default: bool) -> bool:
        return os.getenv(key, str(default)).lower() in ("1", "true", "yes")

    return Config(
        alert_from_email=_require("ALERT_FROM_EMAIL"),
        alert_from_password=_require("ALERT_FROM_PASSWORD"),
        alert_to_email=_require("ALERT_TO_EMAIL"),
        smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=_int("SMTP_PORT", 587),
        lookahead_weeks_min=_int("LOOKAHEAD_WEEKS_MIN", 2),
        lookahead_weeks_max=_int("LOOKAHEAD_WEEKS_MAX", 12),
        check_interval_minutes=_int("CHECK_INTERVAL_MINUTES", 15),
        peak_interval_minutes=_int("PEAK_INTERVAL_MINUTES", 5),
        active_hours_start=_int("ACTIVE_HOURS_START", 6),
        active_hours_end=_int("ACTIVE_HOURS_END", 23),
        min_site_length_ft=_int("MIN_SITE_LENGTH_FT", 25),
        require_hookup=_bool("REQUIRE_HOOKUP", True),
        require_dump_station=_bool("REQUIRE_DUMP_STATION", True),
    )
