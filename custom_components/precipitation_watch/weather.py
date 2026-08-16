"""Weather platform for Precipitation Watch: current conditions + forecast for a watched point."""
from __future__ import annotations

from homeassistant.components.weather import (
    ATTR_CONDITION_CLEAR_NIGHT,
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_FOG,
    ATTR_CONDITION_LIGHTNING,
    ATTR_CONDITION_LIGHTNING_RAINY,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SUNNY,
    Forecast,
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPrecipitationDepth, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DailyPoint, HourlyPoint
from .const import ATTRIBUTION, CONF_NAME, DOMAIN
from .coordinator import PrecipitationCoordinator

# WMO weather interpretation codes (the set Open-Meteo returns as weather_code)
# mapped to Home Assistant's condition strings.
# https://open-meteo.com/en/docs -- "WMO Weather interpretation codes (WW)"
WMO_CONDITION_MAP: dict[int, str] = {
    0: ATTR_CONDITION_SUNNY,  # clear sky
    1: ATTR_CONDITION_SUNNY,  # mainly clear
    2: ATTR_CONDITION_PARTLYCLOUDY,
    3: ATTR_CONDITION_CLOUDY,  # overcast
    45: ATTR_CONDITION_FOG,
    48: ATTR_CONDITION_FOG,  # depositing rime fog
    51: ATTR_CONDITION_RAINY,  # drizzle: light
    53: ATTR_CONDITION_RAINY,  # drizzle: moderate
    55: ATTR_CONDITION_RAINY,  # drizzle: dense
    56: ATTR_CONDITION_RAINY,  # freezing drizzle: light
    57: ATTR_CONDITION_RAINY,  # freezing drizzle: dense
    61: ATTR_CONDITION_RAINY,  # rain: slight
    63: ATTR_CONDITION_RAINY,  # rain: moderate
    65: ATTR_CONDITION_POURING,  # rain: heavy
    66: ATTR_CONDITION_RAINY,  # freezing rain: light
    67: ATTR_CONDITION_POURING,  # freezing rain: heavy
    71: ATTR_CONDITION_SNOWY,  # snow fall: slight
    73: ATTR_CONDITION_SNOWY,  # snow fall: moderate
    75: ATTR_CONDITION_SNOWY,  # snow fall: heavy
    77: ATTR_CONDITION_SNOWY,  # snow grains
    80: ATTR_CONDITION_RAINY,  # rain showers: slight
    81: ATTR_CONDITION_RAINY,  # rain showers: moderate
    82: ATTR_CONDITION_POURING,  # rain showers: violent
    85: ATTR_CONDITION_SNOWY,  # snow showers: slight
    86: ATTR_CONDITION_SNOWY,  # snow showers: heavy
    95: ATTR_CONDITION_LIGHTNING,  # thunderstorm
    96: ATTR_CONDITION_LIGHTNING_RAINY,  # thunderstorm with slight hail
    99: ATTR_CONDITION_LIGHTNING_RAINY,  # thunderstorm with heavy hail
}


def condition_from_wmo(code: int | None, is_day: bool | None) -> str | None:
    """Map an Open-Meteo/WMO weather code (+ day/night) to an HA condition string."""
    if code is None:
        return None
    condition = WMO_CONDITION_MAP.get(code)
    if condition == ATTR_CONDITION_SUNNY and is_day is False:
        return ATTR_CONDITION_CLEAR_NIGHT
    return condition


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PrecipitationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PrecipitationWeather(coordinator, entry)])


class PrecipitationWeather(CoordinatorEntity[PrecipitationCoordinator], WeatherEntity):
    """Current conditions + hourly/daily forecast for a watched point."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = None
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_supported_features = WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY

    def __init__(self, coordinator: PrecipitationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer="Open-Meteo (MeteoSwiss ICON model)",
        )

    @property
    def condition(self) -> str | None:
        if not self.coordinator.data:
            return None
        return condition_from_wmo(self.coordinator.data.current_weather_code, self.coordinator.data.current_is_day)

    @property
    def native_temperature(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.current_temperature_c

    @property
    def native_wind_speed(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.current_wind_speed_kmh

    @property
    def wind_bearing(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.current_wind_direction_deg

    async def async_forecast_daily(self) -> list[Forecast] | None:
        if not self.coordinator.data:
            return None
        return [_daily_to_forecast(point) for point in self.coordinator.data.daily]

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        if not self.coordinator.data:
            return None
        return [_hourly_to_forecast(point) for point in self.coordinator.data.hourly]


def _hourly_to_forecast(point: HourlyPoint) -> Forecast:
    return Forecast(
        datetime=point.time.isoformat(),
        condition=condition_from_wmo(point.weather_code, point.is_day),
        native_temperature=point.temperature_c,
        native_precipitation=point.precipitation_mm,
        precipitation_probability=point.precipitation_probability,
        native_wind_speed=point.wind_speed_kmh,
        wind_bearing=point.wind_direction_deg,
    )


def _daily_to_forecast(point: DailyPoint) -> Forecast:
    return Forecast(
        datetime=point.date.isoformat(),
        # Daily summaries always use the daytime icon variant, not clear-night.
        condition=condition_from_wmo(point.weather_code, True),
        native_temperature=point.temperature_max_c,
        native_templow=point.temperature_min_c,
        native_precipitation=point.precipitation_mm,
        precipitation_probability=point.precipitation_probability_max,
        native_wind_speed=point.wind_speed_max_kmh,
        wind_bearing=point.wind_direction_dominant_deg,
    )
