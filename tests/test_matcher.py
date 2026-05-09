"""Tests for the golden-filter matcher logic."""

from datetime import date

import pytest

from camping_alert.campgrounds import Campground, HookupType, Platform
from camping_alert.config import Config
from camping_alert.matcher import AvailableSlot, filter_slots, matches


@pytest.fixture
def base_cfg() -> Config:
    return Config(
        alert_from_email="a@example.com",
        alert_from_password="secret",
        alert_to_email="b@example.com",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        lookahead_weeks_min=2,
        lookahead_weeks_max=12,
        check_interval_minutes=15,
        peak_interval_minutes=5,
        active_hours_start=6,
        active_hours_end=23,
        min_site_length_ft=25,
        require_hookup=True,
        require_dump_station=True,
    )


@pytest.fixture
def ocean_cg() -> Campground:
    return Campground(
        name="Test Beach",
        park_name="Test State Beach",
        city="Testville, CA",
        platform=Platform.RESERVECA,
        platform_id="999",
        booking_url="https://example.com",
        drive_minutes=60,
        ocean_view=True,
        hookup_type=HookupType.ELECTRIC,
        has_dump_station=True,
        has_pull_through=True,
    )


def _make_slot(cg, hookup=HookupType.ELECTRIC, dump=True, length=30, pull=True):
    return AvailableSlot(
        campground=cg,
        site_id="1",
        site_name="Site 1",
        checkin=date(2026, 6, 5),
        checkout=date(2026, 6, 7),
        hookup_type=hookup,
        has_dump_station=dump,
        site_length_ft=length,
        is_pull_through=pull,
        booking_url="https://example.com/book/1",
    )


class TestMatchesHookup:
    def test_electric_hookup_passes(self, ocean_cg, base_cfg):
        slot = _make_slot(ocean_cg, hookup=HookupType.ELECTRIC)
        assert matches(slot, base_cfg)

    def test_full_hookup_passes(self, ocean_cg, base_cfg):
        slot = _make_slot(ocean_cg, hookup=HookupType.FULL)
        assert matches(slot, base_cfg)

    def test_no_hookup_fails_when_required(self, ocean_cg, base_cfg):
        slot = _make_slot(ocean_cg, hookup=HookupType.NONE)
        assert not matches(slot, base_cfg)

    def test_no_hookup_passes_when_not_required(self, ocean_cg, base_cfg):
        cfg = base_cfg.__class__(**{**base_cfg.__dict__, "require_hookup": False})
        slot = _make_slot(ocean_cg, hookup=HookupType.NONE, dump=True)
        assert matches(slot, cfg)


class TestMatchesDumpStation:
    def test_no_dump_fails_when_required(self, ocean_cg, base_cfg):
        slot = _make_slot(ocean_cg, dump=False)
        assert not matches(slot, base_cfg)

    def test_no_dump_passes_when_not_required(self, ocean_cg, base_cfg):
        cfg = base_cfg.__class__(**{**base_cfg.__dict__, "require_dump_station": False})
        slot = _make_slot(ocean_cg, dump=False)
        assert matches(slot, cfg)


class TestMatchesLength:
    def test_exact_minimum_passes(self, ocean_cg, base_cfg):
        slot = _make_slot(ocean_cg, length=25)
        assert matches(slot, base_cfg)

    def test_short_site_fails(self, ocean_cg, base_cfg):
        slot = _make_slot(ocean_cg, length=20)
        assert not matches(slot, base_cfg)

    def test_unknown_length_passes(self, ocean_cg, base_cfg):
        slot = _make_slot(ocean_cg, length=None)
        assert matches(slot, base_cfg)


class TestFilterSlots:
    def test_empty_input(self, base_cfg):
        assert filter_slots([], base_cfg) == []

    def test_filters_out_bad_slots(self, ocean_cg, base_cfg):
        good = _make_slot(ocean_cg, hookup=HookupType.FULL, length=40)
        bad = _make_slot(ocean_cg, hookup=HookupType.NONE, dump=False)
        result = filter_slots([good, bad], base_cfg)
        assert result == [good]
