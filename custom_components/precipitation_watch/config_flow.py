"""Config flow for Precipitation Watch."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import InvalidWindowsError, parse_display_windows
from .const import (
    CONF_DISPLAY_WINDOWS_HOURS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_LOOKAHEAD_HOURS,
    CONF_MAX_ELEVATION_DIFF_M,
    CONF_MIN_DISTANCE_M,
    CONF_MODE,
    CONF_MOVEMENT_SETTLE_SECONDS,
    CONF_NAME,
    CONF_PROBABILITY_THRESHOLD,
    CONF_SAMPLE_RADIUS_KM,
    CONF_TRACKED_ENTITY_ID,
    CONF_UPDATE_INTERVAL_MIN,
    DEFAULT_DISPLAY_WINDOWS_HOURS,
    DEFAULT_LOOKAHEAD_HOURS,
    DEFAULT_MAX_ELEVATION_DIFF_M,
    DEFAULT_MIN_DISTANCE_M,
    DEFAULT_MOVEMENT_SETTLE_SECONDS,
    DEFAULT_PROBABILITY_THRESHOLD,
    DEFAULT_SAMPLE_RADIUS_KM,
    DEFAULT_UPDATE_INTERVAL_MIN,
    DOMAIN,
    MAX_DISPLAY_WINDOWS,
    MODE_FIXED,
    MODE_TRACKED,
)


class PrecipitationWatchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of a watched point."""

    VERSION = 1

    def __init__(self) -> None:
        self._mode: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """First step: pick fixed coordinates or an entity to track."""
        if user_input is not None:
            self._mode = user_input[CONF_MODE]
            if self._mode == MODE_FIXED:
                return await self.async_step_fixed()
            return await self.async_step_tracked()

        schema = vol.Schema(
            {
                vol.Required(CONF_MODE, default=MODE_FIXED): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[MODE_FIXED, MODE_TRACKED],
                        translation_key="mode",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_fixed(self, user_input: dict[str, Any] | None = None):
        """Static latitude/longitude, e.g. a garden or cabin."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(
                f"fixed_{user_input[CONF_LATITUDE]:.5f}_{user_input[CONF_LONGITUDE]:.5f}"
            )
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    CONF_MODE: MODE_FIXED,
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_LATITUDE: user_input[CONF_LATITUDE],
                    CONF_LONGITUDE: user_input[CONF_LONGITUDE],
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_LATITUDE, default=self.hass.config.latitude): vol.Coerce(float),
                vol.Required(CONF_LONGITUDE, default=self.hass.config.longitude): vol.Coerce(float),
            }
        )
        return self.async_show_form(step_id="fixed", data_schema=schema, errors=errors)

    async def async_step_tracked(self, user_input: dict[str, Any] | None = None):
        """Bind to any entity exposing latitude/longitude attributes."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input[CONF_TRACKED_ENTITY_ID]
            state = self.hass.states.get(entity_id)
            if state is None:
                errors["base"] = "entity_not_found"
            elif "latitude" not in state.attributes or "longitude" not in state.attributes:
                errors["base"] = "entity_missing_coordinates"
            else:
                await self.async_set_unique_id(f"tracked_{entity_id}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_MODE: MODE_TRACKED,
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_TRACKED_ENTITY_ID: entity_id,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_TRACKED_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["device_tracker", "person"])
                ),
            }
        )
        return self.async_show_form(step_id="tracked", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PrecipitationWatchOptionsFlow()


class PrecipitationWatchOptionsFlow(config_entries.OptionsFlow):
    """Throttling and alert-threshold knobs, generic across both modes.

    No custom __init__ here: on current HA core, OptionsFlow.config_entry is
    a read-only property the flow manager injects automatically after
    construction. Manually assigning self.config_entry (the old pre-2024.12
    pattern) raises AttributeError: property has no setter.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parse_display_windows(user_input[CONF_DISPLAY_WINDOWS_HOURS], MAX_DISPLAY_WINDOWS)
            except InvalidWindowsError as err:
                errors[CONF_DISPLAY_WINDOWS_HOURS] = "invalid_windows"
                self._last_windows_error = str(err)
            else:
                return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL_MIN,
                    default=current.get(CONF_UPDATE_INTERVAL_MIN, DEFAULT_UPDATE_INTERVAL_MIN),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=180)),
                vol.Required(
                    CONF_MIN_DISTANCE_M,
                    default=current.get(CONF_MIN_DISTANCE_M, DEFAULT_MIN_DISTANCE_M),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=50000)),
                vol.Required(
                    CONF_MOVEMENT_SETTLE_SECONDS,
                    default=current.get(CONF_MOVEMENT_SETTLE_SECONDS, DEFAULT_MOVEMENT_SETTLE_SECONDS),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=1800)),
                vol.Required(
                    CONF_PROBABILITY_THRESHOLD,
                    default=current.get(CONF_PROBABILITY_THRESHOLD, DEFAULT_PROBABILITY_THRESHOLD),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
                vol.Required(
                    CONF_LOOKAHEAD_HOURS,
                    default=current.get(CONF_LOOKAHEAD_HOURS, DEFAULT_LOOKAHEAD_HOURS),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=24)),
                vol.Required(
                    CONF_DISPLAY_WINDOWS_HOURS,
                    default=current.get(CONF_DISPLAY_WINDOWS_HOURS, DEFAULT_DISPLAY_WINDOWS_HOURS),
                ): str,
                vol.Required(
                    CONF_SAMPLE_RADIUS_KM,
                    default=current.get(CONF_SAMPLE_RADIUS_KM, DEFAULT_SAMPLE_RADIUS_KM),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=20)),
                vol.Required(
                    CONF_MAX_ELEVATION_DIFF_M,
                    default=current.get(CONF_MAX_ELEVATION_DIFF_M, DEFAULT_MAX_ELEVATION_DIFF_M),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=3000)),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"windows_error": getattr(self, "_last_windows_error", "")},
        )
