"""Sensor platform for Precipitation Watch."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import InvalidWindowsError, parse_display_windows
from .const import (
    ATTRIBUTION,
    CONF_DISPLAY_WINDOWS_HOURS,
    CONF_LOOKAHEAD_HOURS,
    CONF_NAME,
    DEFAULT_DISPLAY_WINDOWS_HOURS,
    DEFAULT_LOOKAHEAD_HOURS,
    DOMAIN,
    MAX_DISPLAY_WINDOWS,
)
from .coordinator import PrecipitationCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PrecipitationCoordinator = hass.data[DOMAIN][entry.entry_id]

    raw_windows = entry.options.get(CONF_DISPLAY_WINDOWS_HOURS, DEFAULT_DISPLAY_WINDOWS_HOURS)
    try:
        windows = parse_display_windows(raw_windows, MAX_DISPLAY_WINDOWS)
    except InvalidWindowsError as err:
        # Shouldn't happen if set via the options flow (validated there), but
        # don't let a bad value (e.g. hand-edited storage) block setup.
        _LOGGER.warning(
            "%s: invalid display_windows_hours %r (%s), falling back to default",
            entry.title,
            raw_windows,
            err,
        )
        windows = parse_display_windows(DEFAULT_DISPLAY_WINDOWS_HOURS, MAX_DISPLAY_WINDOWS)

    entities: list[SensorEntity] = []
    for hours in windows:
        entities.append(PrecipitationProbabilityWindowSensor(coordinator, entry, hours))
        entities.append(PrecipitationAmountWindowSensor(coordinator, entry, hours))

    entities.append(NextPrecipitationTimeSensor(coordinator, entry))
    entities.append(CurrentPrecipitationSensor(coordinator, entry))

    async_add_entities(entities)


class _BaseSensor(CoordinatorEntity[PrecipitationCoordinator], SensorEntity):
    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: PrecipitationCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer="Open-Meteo (MeteoSwiss ICON model)",
        )

    @property
    def _lookahead_hours(self) -> int:
        return self._entry.options.get(CONF_LOOKAHEAD_HOURS, DEFAULT_LOOKAHEAD_HOURS)

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        attrs = {
            "latitude": self.coordinator.data.latitude,
            "longitude": self.coordinator.data.longitude,
            "fetched_at": self.coordinator.data.fetched_at.isoformat(),
            "lookahead_hours": self._lookahead_hours,
        }
        if self.coordinator.data.sample_points:
            attrs["sample_points"] = [
                {
                    "latitude": lat,
                    "longitude": lon,
                    "elevation_m": elev,
                    "max_probability_1h": prob,
                    "included": included,
                }
                for lat, lon, elev, prob, included in self.coordinator.data.sample_points
            ]
        return attrs


class PrecipitationProbabilityWindowSensor(_BaseSensor):
    """Max precipitation probability within a specific configured window, e.g. '3h'.

    One instance is created per entry in the display_windows_hours option,
    so you can compare e.g. next-1h vs next-6h probability side by side.
    Independent of the alert binary_sensor, which always uses the single
    lookahead_hours option.
    """

    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:weather-pouring"

    def __init__(self, coordinator: PrecipitationCoordinator, entry: ConfigEntry, hours: int) -> None:
        self._hours = hours
        super().__init__(coordinator, entry, f"precipitation_probability_{hours}h")
        self._attr_translation_key = None
        self._attr_name = f"Precipitation probability ({hours}h)"

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.max_probability_within(self._hours)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["window_hours"] = self._hours
        return attrs


class PrecipitationAmountWindowSensor(_BaseSensor):
    """Summed precipitation (mm) within a specific configured window, e.g. '3h'."""

    _attr_native_unit_of_measurement = "mm"
    _attr_icon = "mdi:weather-rainy"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: PrecipitationCoordinator, entry: ConfigEntry, hours: int) -> None:
        self._hours = hours
        super().__init__(coordinator, entry, f"precipitation_amount_{hours}h")
        self._attr_translation_key = None
        self._attr_name = f"Precipitation amount ({hours}h)"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.total_precipitation_within(self._hours)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["window_hours"] = self._hours
        return attrs


class NextPrecipitationTimeSensor(_BaseSensor):
    _attr_translation_key = "next_precipitation_time"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-alert-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PrecipitationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "next_precipitation_time")

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        from .const import CONF_PROBABILITY_THRESHOLD, DEFAULT_PROBABILITY_THRESHOLD

        threshold = self._entry.options.get(CONF_PROBABILITY_THRESHOLD, DEFAULT_PROBABILITY_THRESHOLD)
        return self.coordinator.data.next_time_above_threshold(self._lookahead_hours, threshold)


class CurrentPrecipitationSensor(_BaseSensor):
    """Near-real-time observed/nowcast-blended precipitation, distinct from the hourly forecast.

    This answers "is it raining right now" -- the forecast-probability
    sensors answer "will it rain in the next N hours". The two can and do
    disagree for fast-forming, highly localized convective cells, since the
    hourly forecast is bucketed and won't always catch a cell that formed
    after the model last ran.
    """

    _attr_translation_key = "current_precipitation"
    _attr_native_unit_of_measurement = "mm"
    _attr_icon = "mdi:weather-rainy"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: PrecipitationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "current_precipitation")

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.current_precipitation_mm

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        if self.coordinator.data:
            attrs["rain_mm"] = self.coordinator.data.current_rain_mm
            attrs["showers_mm"] = self.coordinator.data.current_showers_mm
        return attrs
