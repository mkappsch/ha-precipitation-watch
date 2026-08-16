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
