"""Binary sensor platform for Precipitation Watch."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTRIBUTION,
    CONF_LOOKAHEAD_HOURS,
    CONF_NAME,
    CONF_PROBABILITY_THRESHOLD,
    DEFAULT_LOOKAHEAD_HOURS,
    DEFAULT_PROBABILITY_THRESHOLD,
    DOMAIN,
)
from .coordinator import PrecipitationCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PrecipitationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PrecipitationExpectedBinarySensor(coordinator, entry)])


class PrecipitationExpectedBinarySensor(CoordinatorEntity[PrecipitationCoordinator], BinarySensorEntity):
    """On when forecast probability crosses the configured threshold within the lookahead window."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_translation_key = "precipitation_expected"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_icon = "mdi:weather-lightning-rainy"

    def __init__(self, coordinator: PrecipitationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_precipitation_expected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer="Open-Meteo (MeteoSwiss ICON model)",
        )

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        threshold = self._entry.options.get(CONF_PROBABILITY_THRESHOLD, DEFAULT_PROBABILITY_THRESHOLD)
        lookahead = self._entry.options.get(CONF_LOOKAHEAD_HOURS, DEFAULT_LOOKAHEAD_HOURS)
        max_prob = self.coordinator.data.max_probability_within(lookahead)
        if max_prob is None:
            return None
        return max_prob >= threshold

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        threshold = self._entry.options.get(CONF_PROBABILITY_THRESHOLD, DEFAULT_PROBABILITY_THRESHOLD)
        lookahead = self._entry.options.get(CONF_LOOKAHEAD_HOURS, DEFAULT_LOOKAHEAD_HOURS)
        next_time = self.coordinator.data.next_time_above_threshold(lookahead, threshold)
        return {
            "threshold_percent": threshold,
            "lookahead_hours": lookahead,
            "next_time_above_threshold": next_time.isoformat() if next_time else None,
            "latitude": self.coordinator.data.latitude,
            "longitude": self.coordinator.data.longitude,
        }
