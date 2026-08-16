"""Coordinator for a single watched point (fixed coordinates or a tracked entity).

Generic by design: it doesn't know or care whether the tracked entity is a
car, a person, or a delivery drone. It just knows how to (a) figure out
"where is the point right now" and (b) decide, using plain time/distance
throttles, whether that's worth a fresh API call.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant, callback, Event, EventStateChangedData
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiError, ForecastResult, OpenMeteoClient
from .const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MAX_ELEVATION_DIFF_M,
    CONF_MIN_DISTANCE_M,
    CONF_MODE,
    CONF_SAMPLE_RADIUS_KM,
    CONF_TRACKED_ENTITY_ID,
    CONF_UPDATE_INTERVAL_MIN,
    DEFAULT_MAX_ELEVATION_DIFF_M,
    DEFAULT_MIN_DISTANCE_M,
    DEFAULT_SAMPLE_RADIUS_KM,
    DEFAULT_UPDATE_INTERVAL_MIN,
    DOMAIN,
    MODE_FIXED,
    MODE_TRACKED,
)

_LOGGER = logging.getLogger(__name__)


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in meters."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class LocationUnavailable(HomeAssistantError):
    """The current coordinates for this watched point can't be determined right now."""


class PrecipitationCoordinator(DataUpdateCoordinator[ForecastResult]):
    """Fetches and throttles precipitation forecasts for one watched point."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.entry = entry
        self._client = OpenMeteoClient(session=_get_session(hass))
        self._mode: str = entry.data[CONF_MODE]
        self._tracked_entity_id: str | None = entry.data.get(CONF_TRACKED_ENTITY_ID)
        self._last_fetch_coords: tuple[float, float] | None = None
        self._last_fetch_time: datetime | None = None
        self._unsub_state_listener = None

        interval_min = entry.options.get(CONF_UPDATE_INTERVAL_MIN, DEFAULT_UPDATE_INTERVAL_MIN)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=interval_min),
        )

    async def async_setup(self) -> None:
        """Wire up entity tracking (if applicable) and do the first fetch."""
        if self._mode == MODE_TRACKED:
            self._unsub_state_listener = async_track_state_change_event(
                self.hass, [self._tracked_entity_id], self._handle_tracked_entity_change
            )
        await self.async_config_entry_first_refresh()

    @callback
    def async_unload(self) -> None:
        if self._unsub_state_listener is not None:
            self._unsub_state_listener()
            self._unsub_state_listener = None

    @callback
    def _handle_tracked_entity_change(self, event: Event[EventStateChangedData]) -> None:
        """React to the tracked entity moving.

        Requests a refresh only if it clears both the distance throttle and
        a time floor matching update_interval. The time floor matters
        because async_request_refresh() does *not* respect update_interval
        on its own -- only Home Assistant's own ~10s internal refresh
        debounce would otherwise limit how often this fires, so a point
        that's continuously moving (e.g. driving) could cross
        min_distance_meters every few seconds and trigger far more fetches
        than the periodic baseline. Flooring by update_interval caps
        movement-triggered fetches at roughly double that baseline instead.
        """
        new_state = event.data["new_state"]
        if new_state is None:
            return
        try:
            lat, lon = self._extract_coords_from_state(new_state)
        except LocationUnavailable:
            return

        min_distance = self.entry.options.get(CONF_MIN_DISTANCE_M, DEFAULT_MIN_DISTANCE_M)
        if self._last_fetch_coords is not None:
            moved = haversine_meters(*self._last_fetch_coords, lat, lon)
            if moved < min_distance:
                _LOGGER.debug(
                    "%s moved %.0fm (< %.0fm threshold), skipping refresh",
                    self._tracked_entity_id,
                    moved,
                    min_distance,
                )
                return
            _LOGGER.debug(
                "%s moved %.0fm (>= %.0fm threshold)",
                self._tracked_entity_id,
                moved,
                min_distance,
            )

        if self._last_fetch_time is not None:
            elapsed = datetime.now(timezone.utc) - self._last_fetch_time
            if elapsed < self.update_interval:
                _LOGGER.debug(
                    "%s: only %.0fs since the last fetch (< %.0fs update_interval floor), "
                    "skipping movement-triggered refresh",
                    self._tracked_entity_id,
                    elapsed.total_seconds(),
                    self.update_interval.total_seconds(),
                )
                return

        self.hass.async_create_task(self.async_request_refresh())

    def _extract_coords_from_state(self, state) -> tuple[float, float]:
        lat = state.attributes.get(CONF_LATITUDE)
        lon = state.attributes.get(CONF_LONGITUDE)
        if lat is None or lon is None:
            raise LocationUnavailable(
                f"{state.entity_id} has no latitude/longitude attributes right now"
            )
        return float(lat), float(lon)

    def _current_coords(self) -> tuple[float, float]:
        if self._mode == MODE_FIXED:
            return self.entry.data[CONF_LATITUDE], self.entry.data[CONF_LONGITUDE]

        state = self.hass.states.get(self._tracked_entity_id)
        if state is None:
            raise LocationUnavailable(f"{self._tracked_entity_id} does not exist")
        return self._extract_coords_from_state(state)

    async def _async_update_data(self) -> ForecastResult:
        try:
            lat, lon = self._current_coords()
        except LocationUnavailable as err:
            _LOGGER.warning("%s: cannot determine coordinates: %s", self.name, err)
            raise UpdateFailed(str(err)) from err

        _LOGGER.debug("%s: fetching forecast for (%.5f, %.5f)", self.name, lat, lon)
        sample_radius_km = self.entry.options.get(CONF_SAMPLE_RADIUS_KM, DEFAULT_SAMPLE_RADIUS_KM)
        max_elevation_diff_m = self.entry.options.get(CONF_MAX_ELEVATION_DIFF_M, DEFAULT_MAX_ELEVATION_DIFF_M)
        try:
            result = await self._client.async_get_forecast(lat, lon, sample_radius_km, max_elevation_diff_m)
        except ApiError as err:
            _LOGGER.warning("%s: fetch failed for (%.5f, %.5f): %s", self.name, lat, lon, err)
            raise UpdateFailed(str(err)) from err

        _LOGGER.info(
            "%s: fetched forecast for (%.5f, %.5f) - max probability next %dh: %s%%",
            self.name,
            lat,
            lon,
            self.entry.options.get("lookahead_hours", 1),
            result.max_probability_within(self.entry.options.get("lookahead_hours", 1)),
        )
        self._last_fetch_coords = (lat, lon)
        self._last_fetch_time = datetime.now(timezone.utc)
        return result


def _get_session(hass: HomeAssistant):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return async_get_clientsession(hass)
