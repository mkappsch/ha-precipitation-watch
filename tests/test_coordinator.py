"""Tests for coordinator.py.

Split into two tiers:
  1. Pure-function tests (haversine_meters) -- no HA needed, always run.
  2. Full integration tests using pytest-homeassistant-custom-component's
     `hass` fixture, which spins up a real (test) HA core so the config
     entry / coordinator / entity wiring is exercised end-to-end. These
     are skipped automatically if that dev dependency isn't installed
     (see requirements_test.txt) rather than failing your CI for
     unrelated reasons.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.precipitation_watch.coordinator import haversine_meters
from tests.conftest import DEVICE_TRACKER_ENTITY_ID, DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE

pytest_homeassistant = pytest.importorskip(
    "pytest_homeassistant_custom_component",
    reason="pip install pytest-homeassistant-custom-component to run full integration tests",
)


# --- Tier 1: pure math, always runs -----------------------------------------

def test_haversine_zero_distance_for_same_point():
    d = haversine_meters(DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE, DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE)
    assert d == pytest.approx(0.0, abs=1e-6)


def test_haversine_known_short_distance():
    # ~1km north-ish move: roughly +0.009 degrees latitude
    d = haversine_meters(47.3769, 8.5417, 47.3859, 8.5417)  # Zurich, moved ~1km north
    assert 950 < d < 1050


# --- Tier 2: full config-entry + coordinator wiring -------------------------

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.precipitation_watch.const import (  # noqa: E402
    CONF_LOOKAHEAD_HOURS,
    CONF_MODE,
    CONF_MIN_DISTANCE_M,
    CONF_NAME,
    CONF_TRACKED_ENTITY_ID,
    CONF_UPDATE_INTERVAL_MIN,
    DEFAULT_MAX_ELEVATION_DIFF_M,
    DEFAULT_SAMPLE_RADIUS_KM,
    DOMAIN,
    MODE_TRACKED,
)


@pytest.fixture
def mock_forecast_result(sample_payload):
    from custom_components.precipitation_watch.api import OpenMeteoClient

    return OpenMeteoClient._parse(DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE, sample_payload)


async def test_tracked_mode_reads_coords_from_entity_state(hass, enable_custom_integrations, mock_forecast_result):
    """Coordinator should pull lat/lon straight from the tracked entity's attributes."""
    hass.states.async_set(
        DEVICE_TRACKER_ENTITY_ID,
        "home",
        {"latitude": DEVICE_TRACKER_LATITUDE, "longitude": DEVICE_TRACKER_LONGITUDE},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODE: MODE_TRACKED,
            CONF_NAME: "Car",
            CONF_TRACKED_ENTITY_ID: DEVICE_TRACKER_ENTITY_ID,
        },
        # sample_payload's rainy hour is 3h out (see test_api.py's
        # test_max_probability_within_excludes_out_of_window_rain) -- inside
        # this window but outside the 1h default, so lookahead is widened
        # deliberately rather than relying on the default.
        options={CONF_MIN_DISTANCE_M: 200, CONF_LOOKAHEAD_HOURS: 6},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.precipitation_watch.api.OpenMeteoClient.async_get_forecast",
        AsyncMock(return_value=mock_forecast_result),
    ) as mocked_fetch:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    mocked_fetch.assert_awaited_once_with(
        DEVICE_TRACKER_LATITUDE,
        DEVICE_TRACKER_LONGITUDE,
        DEFAULT_SAMPLE_RADIUS_KM,
        DEFAULT_MAX_ELEVATION_DIFF_M,
    )

    state = hass.states.get("binary_sensor.car_precipitation_expected")
    assert state is not None
    assert state.state == "on"  # sample_payload's 85% hour is within the 6h lookahead set above


async def test_small_movement_below_threshold_does_not_trigger_refresh(hass, enable_custom_integrations, mock_forecast_result):
    """Moving less than min_distance_meters shouldn't cause a re-fetch."""
    hass.states.async_set(
        DEVICE_TRACKER_ENTITY_ID,
        "home",
        {"latitude": DEVICE_TRACKER_LATITUDE, "longitude": DEVICE_TRACKER_LONGITUDE},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODE: MODE_TRACKED,
            CONF_NAME: "Car",
            CONF_TRACKED_ENTITY_ID: DEVICE_TRACKER_ENTITY_ID,
        },
        options={CONF_MIN_DISTANCE_M: 5000},  # deliberately large threshold
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.precipitation_watch.api.OpenMeteoClient.async_get_forecast",
        AsyncMock(return_value=mock_forecast_result),
    ) as mocked_fetch:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Nudge the tracker a tiny amount (well under 5km threshold)
        hass.states.async_set(
            DEVICE_TRACKER_ENTITY_ID,
            "home",
            {"latitude": DEVICE_TRACKER_LATITUDE + 0.0005, "longitude": DEVICE_TRACKER_LONGITUDE},
        )
        await hass.async_block_till_done()

    # Only the initial fetch should have happened, not a second one from the nudge.
    assert mocked_fetch.await_count == 1


async def test_movement_refresh_throttled_by_update_interval_floor(
    hass, enable_custom_integrations, mock_forecast_result, freezer
):
    """A big-enough move should still be throttled if it happens sooner
    than update_interval after the last fetch -- async_request_refresh()
    doesn't respect update_interval on its own, so without this floor a
    continuously-moving point could fetch far more often than intended."""
    hass.states.async_set(
        DEVICE_TRACKER_ENTITY_ID,
        "home",
        {"latitude": DEVICE_TRACKER_LATITUDE, "longitude": DEVICE_TRACKER_LONGITUDE},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODE: MODE_TRACKED,
            CONF_NAME: "Car",
            CONF_TRACKED_ENTITY_ID: DEVICE_TRACKER_ENTITY_ID,
        },
        # Small distance threshold so a small move clears it easily --
        # the update_interval floor is what's under test here, not distance.
        options={CONF_MIN_DISTANCE_M: 50, CONF_UPDATE_INTERVAL_MIN: 15},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.precipitation_watch.api.OpenMeteoClient.async_get_forecast",
        AsyncMock(return_value=mock_forecast_result),
    ) as mocked_fetch:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Move well past the 50m threshold, immediately (no time elapsed).
        hass.states.async_set(
            DEVICE_TRACKER_ENTITY_ID,
            "home",
            {"latitude": DEVICE_TRACKER_LATITUDE + 0.01, "longitude": DEVICE_TRACKER_LONGITUDE},
        )
        await hass.async_block_till_done()

    # Distance threshold was cleared, but update_interval (15min) hasn't
    # elapsed yet -- the floor should have blocked the second fetch.
    assert mocked_fetch.await_count == 1


async def test_movement_refresh_allowed_once_update_interval_elapses(
    hass, enable_custom_integrations, mock_forecast_result, freezer
):
    """Once update_interval has actually passed, a qualifying move should
    trigger a fetch again -- the floor only delays, it doesn't disable."""
    hass.states.async_set(
        DEVICE_TRACKER_ENTITY_ID,
        "home",
        {"latitude": DEVICE_TRACKER_LATITUDE, "longitude": DEVICE_TRACKER_LONGITUDE},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODE: MODE_TRACKED,
            CONF_NAME: "Car",
            CONF_TRACKED_ENTITY_ID: DEVICE_TRACKER_ENTITY_ID,
        },
        options={CONF_MIN_DISTANCE_M: 50, CONF_UPDATE_INTERVAL_MIN: 15},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.precipitation_watch.api.OpenMeteoClient.async_get_forecast",
        AsyncMock(return_value=mock_forecast_result),
    ) as mocked_fetch:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        freezer.tick(timedelta(minutes=16))

        hass.states.async_set(
            DEVICE_TRACKER_ENTITY_ID,
            "home",
            {"latitude": DEVICE_TRACKER_LATITUDE + 0.01, "longitude": DEVICE_TRACKER_LONGITUDE},
        )
        await hass.async_block_till_done()

    assert mocked_fetch.await_count == 2


async def test_weather_entity_reports_current_conditions_and_forecasts(
    hass, enable_custom_integrations, mock_forecast_result
):
    """The weather platform should come up alongside sensor/binary_sensor and
    expose current conditions + both forecast types from the same coordinator
    data -- no separate fetch, no separate throttling."""
    hass.states.async_set(
        DEVICE_TRACKER_ENTITY_ID,
        "home",
        {"latitude": DEVICE_TRACKER_LATITUDE, "longitude": DEVICE_TRACKER_LONGITUDE},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODE: MODE_TRACKED,
            CONF_NAME: "Car",
            CONF_TRACKED_ENTITY_ID: DEVICE_TRACKER_ENTITY_ID,
        },
        options={CONF_MIN_DISTANCE_M: 200},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.precipitation_watch.api.OpenMeteoClient.async_get_forecast",
        AsyncMock(return_value=mock_forecast_result),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("weather.car")
    assert state is not None
    assert state.state == "sunny"  # sample_payload's `current` block: weather_code=1, is_day=1
    assert state.attributes["temperature"] == 19.5
    assert state.attributes["wind_speed"] == 12.0

    daily_response = await hass.services.async_call(
        "weather", "get_forecasts", {"entity_id": "weather.car", "type": "daily"},
        blocking=True, return_response=True,
    )
    hourly_response = await hass.services.async_call(
        "weather", "get_forecasts", {"entity_id": "weather.car", "type": "hourly"},
        blocking=True, return_response=True,
    )
    daily = daily_response["weather.car"]["forecast"]
    hourly = hourly_response["weather.car"]["forecast"]
    # The get_forecasts service response uses the display attribute names
    # (unit-converted), not the entity's internal native_* field names.
    # sample_payload defaults to hours=24 -- 1 day of daily, 24h hourly --
    # regardless of the real FORECAST_HOURS_REQUESTED/FORECAST_DAYS_REQUESTED
    # constants, since this mock bypasses the actual API call entirely.
    assert len(daily) == 1
    assert daily[0]["temperature"] == 22.0
    assert daily[0]["templow"] == 14.0
    assert len(hourly) == 24
    assert hourly[3]["condition"] == "rainy"  # sample_payload's rainy_hour_index=3, WMO code 61
    assert hourly[3]["temperature"] == pytest.approx(18.3)
