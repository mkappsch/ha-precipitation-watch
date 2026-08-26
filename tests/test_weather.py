"""Tests for weather.py: WMO condition mapping and forecast conversion.

Pure functions, no HA test harness required.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from custom_components.precipitation_watch.api import DailyPoint, HourlyPoint
from custom_components.precipitation_watch.weather import (
    WMO_CONDITION_MAP,
    _block_to_forecast,
    _current_block_condition,
    _daily_to_forecast,
    _group_into_day_night_blocks,
    _hourly_to_forecast,
    _pick_block_condition,
    _pick_daily_condition,
    condition_from_wmo,
)


@pytest.mark.parametrize(
    ("code", "is_day", "expected"),
    [
        (0, True, "sunny"),
        (0, False, "clear-night"),
        (1, False, "clear-night"),  # "mainly clear" also gets the night swap
        (2, True, "partlycloudy"),
        (2, False, "partlycloudy"),  # non-clear codes are day/night-invariant
        (3, True, "cloudy"),
        (45, True, "fog"),
        (61, True, "rainy"),
        (65, True, "pouring"),
        (71, True, "snowy"),
        (95, True, "lightning"),
        (96, True, "lightning-rainy"),
    ],
)
def test_condition_from_wmo_known_codes(code, is_day, expected):
    assert condition_from_wmo(code, is_day) == expected


def test_condition_from_wmo_none_code_returns_none():
    assert condition_from_wmo(None, True) is None


def test_condition_from_wmo_unmapped_code_returns_none():
    # Not a real WMO code -- shouldn't crash, just no mapping.
    assert condition_from_wmo(12345, True) is None


def test_every_wmo_code_maps_to_a_real_ha_condition():
    """Guards against typos in the map: every value must be a condition HA
    actually knows about, not just an arbitrary string."""
    from homeassistant.components.weather import (
        ATTR_CONDITION_CLEAR_NIGHT,
        ATTR_CONDITION_CLOUDY,
        ATTR_CONDITION_EXCEPTIONAL,
        ATTR_CONDITION_FOG,
        ATTR_CONDITION_HAIL,
        ATTR_CONDITION_LIGHTNING,
        ATTR_CONDITION_LIGHTNING_RAINY,
        ATTR_CONDITION_PARTLYCLOUDY,
        ATTR_CONDITION_POURING,
        ATTR_CONDITION_RAINY,
        ATTR_CONDITION_SNOWY,
        ATTR_CONDITION_SNOWY_RAINY,
        ATTR_CONDITION_SUNNY,
        ATTR_CONDITION_WINDY,
        ATTR_CONDITION_WINDY_VARIANT,
    )

    valid_conditions = {
        ATTR_CONDITION_CLEAR_NIGHT, ATTR_CONDITION_CLOUDY, ATTR_CONDITION_EXCEPTIONAL,
        ATTR_CONDITION_FOG, ATTR_CONDITION_HAIL, ATTR_CONDITION_LIGHTNING,
        ATTR_CONDITION_LIGHTNING_RAINY, ATTR_CONDITION_PARTLYCLOUDY, ATTR_CONDITION_POURING,
        ATTR_CONDITION_RAINY, ATTR_CONDITION_SNOWY, ATTR_CONDITION_SNOWY_RAINY,
        ATTR_CONDITION_SUNNY, ATTR_CONDITION_WINDY, ATTR_CONDITION_WINDY_VARIANT,
    }
    for code, condition in WMO_CONDITION_MAP.items():
        assert condition in valid_conditions, f"WMO code {code} maps to unknown condition {condition!r}"


def test_hourly_to_forecast_maps_fields():
    point = HourlyPoint(
        time=datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc),
        precipitation_probability=80,
        precipitation_mm=1.5,
        temperature_c=21.0,
        wind_speed_kmh=15.0,
        wind_direction_deg=210.0,
        weather_code=61,
        is_day=True,
    )
    forecast = _hourly_to_forecast(point)
    assert forecast["datetime"] == "2026-08-17T14:00:00+00:00"
    assert forecast["condition"] == "rainy"
    assert forecast["native_temperature"] == 21.0
    assert forecast["native_precipitation"] == 1.5
    assert forecast["precipitation_probability"] == 80
    assert forecast["native_wind_speed"] == 15.0
    assert forecast["wind_bearing"] == 210.0


def test_daily_to_forecast_always_uses_day_condition_never_clear_night():
    point = DailyPoint(
        date=datetime(2026, 8, 17, tzinfo=timezone.utc),
        weather_code=0,  # clear sky -- would be "clear-night" for an hourly point at night
        temperature_max_c=25.0,
        temperature_min_c=12.0,
        precipitation_mm=0.0,
        precipitation_probability_max=5,
        wind_speed_max_kmh=20.0,
        wind_direction_dominant_deg=150.0,
    )
    forecast = _daily_to_forecast(point)
    assert forecast["condition"] == "sunny"
    assert forecast["native_temperature"] == 25.0
    assert forecast["native_templow"] == 12.0


# --- _pick_block_condition -----------------------------------------------


def test_pick_block_condition_rain_and_snow_gets_the_real_combo():
    conditions = ["rainy"] * 6 + ["snowy"] * 4
    assert _pick_block_condition(conditions) == "snowy-rainy"


def test_pick_block_condition_rain_and_thunder_gets_the_real_combo():
    conditions = ["rainy"] * 8 + ["lightning"] * 2
    assert _pick_block_condition(conditions) == "lightning-rainy"


def test_pick_block_condition_single_hour_blip_gets_filtered():
    # 1 of 12 hours (~8%) cloudy, rest sunny -- below the 30% threshold, a
    # single blip shouldn't be able to relabel the whole block.
    conditions = ["sunny"] * 11 + ["cloudy"]
    assert _pick_block_condition(conditions) == "sunny"


def test_pick_block_condition_escalates_a_genuinely_mixed_block():
    # 6 of 14 hours (~43%) cloudy -- a substantial minority, not a blip.
    conditions = ["sunny"] * 8 + ["cloudy"] * 6
    assert _pick_block_condition(conditions) == "cloudy"


def test_pick_block_condition_ties_resolve_toward_more_severe():
    conditions = ["sunny"] * 5 + ["cloudy"] * 5
    assert _pick_block_condition(conditions) == "cloudy"


def test_pick_block_condition_empty_list_returns_none():
    assert _pick_block_condition([]) is None


# --- _group_into_day_night_blocks -----------------------------------------


def _hp(hour_str: str, is_day: bool) -> HourlyPoint:
    return HourlyPoint(
        time=datetime.fromisoformat(hour_str).replace(tzinfo=timezone.utc),
        precipitation_probability=0,
        precipitation_mm=0.0,
        weather_code=1,
        is_day=is_day,
    )


def test_group_into_day_night_blocks_splits_by_date_and_daytime():
    points = [
        _hp("2026-08-17T22:00", False),
        _hp("2026-08-17T23:00", False),
        _hp("2026-08-18T00:00", False),
        _hp("2026-08-18T06:00", False),
        _hp("2026-08-18T07:00", True),
        _hp("2026-08-18T20:00", True),
        _hp("2026-08-18T21:00", False),
    ]
    blocks = _group_into_day_night_blocks(points)
    keys = [(d, is_day) for d, is_day, _ in blocks]
    # Pre-dawn Aug 18 hours (00:00, 06:00) join Aug 17's night, not Aug 18's --
    # "tonight" spans from one date's dusk through the next date's dawn.
    assert keys == [
        (date(2026, 8, 17), False),
        (date(2026, 8, 18), True),
        (date(2026, 8, 18), False),
    ]
    aug17_night = blocks[0][2]
    assert len(aug17_night) == 4  # 22:00, 23:00, 00:00, 06:00
    aug18_day = blocks[1][2]
    assert len(aug18_day) == 2  # 07:00, 20:00


def test_group_into_day_night_blocks_leading_night_hours_use_prior_date():
    # Data starting mid-night, before any daytime has been seen at all.
    points = [_hp("2026-08-18T02:00", False), _hp("2026-08-18T03:00", False)]
    blocks = _group_into_day_night_blocks(points)
    assert blocks[0][0] == date(2026, 8, 17)  # attributed to the previous night
    assert blocks[0][1] is False


# --- _block_to_forecast -----------------------------------------------


def test_block_to_forecast_day_uses_max_temp_night_uses_min_temp():
    points = [
        HourlyPoint(
            time=datetime(2026, 8, 18, 8, tzinfo=timezone.utc),
            precipitation_probability=0, precipitation_mm=0.0,
            temperature_c=15.0, weather_code=1, is_day=True,
        ),
        HourlyPoint(
            time=datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
            precipitation_probability=0, precipitation_mm=0.0,
            temperature_c=25.0, weather_code=1, is_day=True,
        ),
    ]
    day_forecast = _block_to_forecast(date(2026, 8, 18), True, points)
    assert day_forecast["native_temperature"] == 25.0
    assert day_forecast["is_daytime"] is True

    night_forecast = _block_to_forecast(date(2026, 8, 18), False, points)
    assert night_forecast["native_temperature"] == 15.0
    assert night_forecast["is_daytime"] is False


def test_block_to_forecast_aggregates_precipitation_and_probability():
    points = [
        HourlyPoint(
            time=datetime(2026, 8, 18, 8, tzinfo=timezone.utc),
            precipitation_probability=20, precipitation_mm=1.0, weather_code=61, is_day=True,
        ),
        HourlyPoint(
            time=datetime(2026, 8, 18, 9, tzinfo=timezone.utc),
            precipitation_probability=80, precipitation_mm=2.5, weather_code=63, is_day=True,
        ),
    ]
    forecast = _block_to_forecast(date(2026, 8, 18), True, points)
    assert forecast["native_precipitation"] == 3.5  # summed, like a daily total
    assert forecast["precipitation_probability"] == 80  # max, like Open-Meteo's own daily field
    assert forecast["condition"] == "rainy"
    assert forecast["datetime"] == "2026-08-18T08:00:00+00:00"  # first hour in the block


# --- _pick_daily_condition / _daily_to_forecast with hourly override ------


def _hourly_series(day: str, codes: list[int], is_day: bool = True) -> list[HourlyPoint]:
    return [
        HourlyPoint(
            time=datetime.fromisoformat(f"{day}T{hour:02d}:00").replace(tzinfo=timezone.utc),
            precipitation_probability=0, precipitation_mm=0.0, weather_code=code, is_day=is_day,
        )
        for hour, code in enumerate(codes)
    ]


def test_pick_daily_condition_uses_the_day_hourly_majority():
    # 15 clear, 5 rainy (25%, below the 30% escalation threshold) across
    # one calendar date -- same rule as the day/night blocks, just over the
    # full day instead of half of it.
    codes = [1] * 15 + [61] * 5
    hourly = _hourly_series("2026-08-18", codes)
    assert _pick_daily_condition(date(2026, 8, 18), hourly) == "sunny"


def test_pick_daily_condition_ignores_hours_from_other_dates():
    hourly = _hourly_series("2026-08-18", [61] * 5) + _hourly_series("2026-08-19", [1] * 5)
    assert _pick_daily_condition(date(2026, 8, 18), hourly) == "rainy"
    assert _pick_daily_condition(date(2026, 8, 19), hourly) == "sunny"


def test_daily_to_forecast_prefers_hourly_majority_over_open_meteos_raw_code():
    """The real behavior change this whole feature was about: Open-Meteo's
    own daily code says rainy (its "most severe hour wins" pick), but the
    day was actually 75% clear -- our own hourly-derived condition should
    win, not the stale raw one."""
    point = DailyPoint(
        date=datetime(2026, 8, 18, tzinfo=timezone.utc),
        weather_code=61,  # Open-Meteo's own pick: rainy
        temperature_max_c=22.0, temperature_min_c=12.0,
        precipitation_mm=1.0, precipitation_probability_max=20,
        wind_speed_max_kmh=10.0, wind_direction_dominant_deg=180.0,
    )
    hourly = _hourly_series("2026-08-18", [1] * 15 + [61] * 5)  # 75% clear
    forecast = _daily_to_forecast(point, hourly)
    assert forecast["condition"] == "sunny"
    # Everything else still comes from Open-Meteo's own daily aggregates,
    # unaffected -- only the condition changed.
    assert forecast["native_temperature"] == 22.0
    assert forecast["native_templow"] == 12.0


def test_daily_to_forecast_falls_back_without_matching_hourly_coverage():
    point = DailyPoint(
        date=datetime(2026, 8, 25, tzinfo=timezone.utc),
        weather_code=3, temperature_max_c=20.0, temperature_min_c=10.0,
        precipitation_mm=0.0, precipitation_probability_max=0,
        wind_speed_max_kmh=5.0, wind_direction_dominant_deg=90.0,
    )
    # hourly only covers Aug 18 -- nothing for Aug 25 (e.g. a date past the
    # end of our 7-day hourly window).
    hourly = _hourly_series("2026-08-18", [1] * 5)
    forecast = _daily_to_forecast(point, hourly)
    assert forecast["condition"] == "cloudy"  # falls back to Open-Meteo's own code=3


def test_daily_to_forecast_without_hourly_points_uses_open_meteos_code():
    point = DailyPoint(
        date=datetime(2026, 8, 18, tzinfo=timezone.utc),
        weather_code=61, temperature_max_c=20.0, temperature_min_c=10.0,
        precipitation_mm=1.0, precipitation_probability_max=50,
        wind_speed_max_kmh=5.0, wind_direction_dominant_deg=90.0,
    )
    assert _daily_to_forecast(point)["condition"] == "rainy"


# --- _current_block_condition ----------------------------------------------


def test_current_block_condition_uses_the_first_blocks_majority():
    # First block = today's daytime hours; a single blip shouldn't flip it.
    hourly = _hourly_series("2026-08-18", [1] * 11 + [61])
    assert _current_block_condition(hourly) == "sunny"


def test_current_block_condition_reflects_a_genuine_current_rain_stretch():
    hourly = _hourly_series("2026-08-18", [61] * 8 + [1] * 4)
    assert _current_block_condition(hourly) == "rainy"


def test_current_block_condition_empty_hourly_returns_none():
    assert _current_block_condition([]) is None
