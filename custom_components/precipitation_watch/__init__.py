"""Precipitation Watch integration: precipitation forecasts for fixed or tracked points."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN, PLATFORMS
from .coordinator import PrecipitationCoordinator

SERVICE_REFRESH = "refresh"
_REFRESH_SCHEMA = vol.Schema(
    {vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string])}
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a watched point from a config entry."""
    coordinator = PrecipitationCoordinator(hass, entry)
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        hass.services.async_register(
            DOMAIN, SERVICE_REFRESH, _async_handle_refresh, schema=_REFRESH_SCHEMA
        )

    return True


async def _async_handle_refresh(call: ServiceCall) -> None:
    """Force an immediate refresh for the watched point(s) behind the targeted device(s)."""
    hass = call.hass
    device_registry = dr.async_get(hass)
    coordinators: list[PrecipitationCoordinator] = []

    for device_id in call.data["device_id"]:
        device = device_registry.async_get(device_id)
        if device is None:
            raise ServiceValidationError(f"Unknown device_id: {device_id}")
        for entry_id in device.config_entries:
            coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
            if coordinator is not None:
                coordinators.append(coordinator)

    if not coordinators:
        raise ServiceValidationError(
            "No Point Weather Watch device found for the given target."
        )

    for coordinator in coordinators:
        await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a watched point."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: PrecipitationCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator.async_unload()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options (thresholds etc.) change."""
    await hass.config_entries.async_reload(entry.entry_id)
