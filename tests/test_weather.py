"""Tests for weather.py: WMO condition mapping and forecast conversion.

Pure functions, no HA test harness required.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.precipitation_watch.api import DailyPoint, HourlyPoint
from custom_components.precipitation_watch.weather import (
    WMO_CONDITION_MAP,
    _daily_to_forecast,
    _hourly_to_forecast,
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
