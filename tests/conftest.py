"""Shared test fixtures.

No live network calls anywhere: the Open-Meteo response below is a
hand-built stand-in matching the real API's documented shape
(hourly.time / hourly.precipitation_probability / hourly.precipitation),
so tests stay fast and deterministic. Swap in a real recorded response
here later if you want a stronger contract test.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

# Your real device_tracker fixture, used across the tracked-mode tests.
DEVICE_TRACKER_ENTITY_ID = "device_tracker.xyz"
DEVICE_TRACKER_LATITUDE = 48.3123412389
DEVICE_TRACKER_LONGITUDE = 9.1235912356


def build_open_meteo_payload(
    start: datetime,
    hours: int = 24,
    rainy_hour_index: int | None = 3,
    current_precipitation_mm: float | None = 0.0,
):
    """Build a fake-but-shaped-correctly Open-Meteo JSON response.

    If rainy_hour_index is given, that hour gets a high probability/amount
    so tests can assert alerting behavior deterministically. current_precipitation_mm
    simulates the separate `current` block (observed/nowcast "right now" value,
    as opposed to the hourly forecast).
    """
    times = []
    probs = []
    amounts = []
    codes = []
    for i in range(hours):
        t = start + timedelta(hours=i)
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
        if rainy_hour_index is not None and i == rainy_hour_index:
            probs.append(85)
            amounts.append(2.4)
            codes.append(61)  # WMO: rain, slight
        else:
            probs.append(5)
            amounts.append(0.0)
            codes.append(1)  # WMO: mainly clear

    daily_days = max(1, hours // 24)
    daily_times = [(start + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(daily_days)]

    return {
        "latitude": DEVICE_TRACKER_LATITUDE,
        "longitude": DEVICE_TRACKER_LONGITUDE,
        "elevation": 450.0,
        "current": {
            "precipitation": current_precipitation_mm,
            "rain": current_precipitation_mm,
            "showers": 0.0,
            "temperature_2m": 19.5,
            "wind_speed_10m": 12.0,
            "wind_direction_10m": 200.0,
            "weather_code": 1,
            "is_day": 1,
        },
        "hourly": {
            "time": times,
            "precipitation_probability": probs,
            "precipitation": amounts,
            "temperature_2m": [18.0 + 0.1 * i for i in range(hours)],
            "wind_speed_10m": [10.0] * hours,
            "wind_direction_10m": [180.0] * hours,
            "weather_code": codes,
            "is_day": [1] * hours,
        },
        "daily": {
            "time": daily_times,
            "weather_code": [1] * daily_days,
            "temperature_2m_max": [22.0] * daily_days,
            "temperature_2m_min": [14.0] * daily_days,
            "precipitation_sum": [0.0] * daily_days,
            "precipitation_probability_max": [10] * daily_days,
            "wind_speed_10m_max": [15.0] * daily_days,
            "wind_direction_10m_dominant": [190.0] * daily_days,
        },
    }


@pytest.fixture
def now_utc():
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def sample_payload(now_utc):
    return build_open_meteo_payload(now_utc, hours=24, rainy_hour_index=3)


@pytest.fixture
def dry_payload(now_utc):
    return build_open_meteo_payload(now_utc, hours=24, rainy_hour_index=None)


@pytest.fixture
def actively_raining_but_forecast_missed_payload(now_utc):
    """The exact real-world case that motivated this sensor:

    hourly forecast shows 0% (the convective cell wasn't captured), but
    `current` shows active observed precipitation right now.
    """
    return build_open_meteo_payload(now_utc, hours=24, rainy_hour_index=None, current_precipitation_mm=3.2)
