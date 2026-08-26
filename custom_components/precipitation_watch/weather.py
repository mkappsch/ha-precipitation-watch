"""Weather platform for Precipitation Watch: current conditions + forecast for a watched point."""
from __future__ import annotations

from collections import Counter
from datetime import date as date_, timedelta

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
    ATTR_CONDITION_SNOWY_RAINY,
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


# Share of a block's hours a condition needs to "win" the block, most severe
# first -- see _pick_block_condition. Picked to escalate a genuinely mixed
# block (e.g. 40% partly cloudy) without letting a single fluky hour (under
# ~1/8 of a ~12h block) dominate the whole thing the way Open-Meteo's own
# daily.weather_code ("most severe hour of the day, however brief") does.
_ESCALATION_SHARE_THRESHOLD = 0.3
_SEVERITY_MOST_SEVERE_FIRST = [
    ATTR_CONDITION_LIGHTNING_RAINY,
    ATTR_CONDITION_LIGHTNING,
    ATTR_CONDITION_SNOWY_RAINY,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_FOG,
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_SUNNY,
    ATTR_CONDITION_CLEAR_NIGHT,
]
_RAIN_LIKE = {ATTR_CONDITION_RAINY, ATTR_CONDITION_POURING}
_THUNDER_LIKE = {ATTR_CONDITION_LIGHTNING, ATTR_CONDITION_LIGHTNING_RAINY}


def _pick_block_condition(conditions: list[str]) -> str | None:
    """Pick one representative HA condition for a block of hours.

    Real combos (rain+snow, rain+thunder) get HA's actual combo condition --
    not fabricated, both already exist in HA's own vocabulary. Otherwise,
    the most severe condition that covers at least _ESCALATION_SHARE_THRESHOLD
    of the block wins, falling back to whatever's most common if nothing
    clears that bar. Checking most-severe-first also serves as the tie-break:
    a 50/50 split resolves toward the more severe condition.
    """
    if not conditions:
        return None

    has_rain = any(c in _RAIN_LIKE for c in conditions)
    has_snow = ATTR_CONDITION_SNOWY in conditions
    has_thunder = any(c in _THUNDER_LIKE for c in conditions)
    if has_rain and has_snow:
        return ATTR_CONDITION_SNOWY_RAINY
    if has_rain and has_thunder:
        return ATTR_CONDITION_LIGHTNING_RAINY

    counts = Counter(conditions)
    total = len(conditions)
    for condition in _SEVERITY_MOST_SEVERE_FIRST:
        if counts.get(condition, 0) / total >= _ESCALATION_SHARE_THRESHOLD:
            return condition
    return counts.most_common(1)[0][0]


def _group_into_day_night_blocks(points: list[HourlyPoint]) -> list[tuple[date_, bool, list[HourlyPoint]]]:
    """Group hourly points into (date, is_daytime) blocks.

    A "day" block is one date's is_day=1 hours. A "night" block runs from
    that date's dusk through the *next* date's dawn (the "tonight"
    convention forecasters use) -- not calendar-midnight-aligned, so a
    single overnight rain shower doesn't get split across two entries.
    """
    blocks: dict[tuple[date_, bool], list[HourlyPoint]] = {}
    order: list[tuple[date_, bool]] = []
    last_day_date: date_ | None = None

    for point in points:
        point_date = point.time.date()
        if point.is_day:
            last_day_date = point_date
            key = (point_date, True)
        else:
            if last_day_date is not None:
                night_date = last_day_date
            else:
                # No daytime seen yet (data starts mid-night) -- use the
                # hour to guess pre-dawn (still last date's night) vs.
                # post-dusk (this date's night, we just haven't reached its
                # daytime hours in the data yet).
                night_date = point_date if point.time.hour >= 12 else point_date - timedelta(days=1)
            key = (night_date, False)
        if key not in blocks:
            blocks[key] = []
            order.append(key)
        blocks[key].append(point)

    return [(d, is_day, blocks[(d, is_day)]) for d, is_day in order]


def _conditions_for_points(points: list[HourlyPoint]) -> list[str]:
    return [c for p in points if (c := condition_from_wmo(p.weather_code, p.is_day)) is not None]


def _pick_daily_condition(target_date: date_, hourly_points: list[HourlyPoint]) -> str | None:
    """Same picking logic as the day/night blocks, but over one full
    calendar day (both halves combined) -- for the daily forecast's single
    icon, which otherwise has no day/night split to fall back on."""
    conditions = _conditions_for_points([p for p in hourly_points if p.time.date() == target_date])
    return _pick_block_condition(conditions)


def _current_block_condition(hourly_points: list[HourlyPoint]) -> str | None:
    """The picked condition for whichever day/night block "right now" falls
    into. Hourly data starts at the current hour, so that's always the
    first block -- using it (instead of Open-Meteo's raw current.weather_code)
    means the headline state can't disagree with the twice-daily forecast
    card sitting right below it for the same stretch of time."""
    blocks = _group_into_day_night_blocks(hourly_points)
    if not blocks:
        return None
    _, _, points = blocks[0]
    return _pick_block_condition(_conditions_for_points(points))


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
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_DAILY
        | WeatherEntityFeature.FORECAST_HOURLY
        | WeatherEntityFeature.FORECAST_TWICE_DAILY
    )

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
        picked = _current_block_condition(self.coordinator.data.hourly)
        if picked is not None:
            return picked
        # Fallback if there's no hourly data to derive a block from.
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
        hourly = self.coordinator.data.hourly
        return [_daily_to_forecast(point, hourly) for point in self.coordinator.data.daily]

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        if not self.coordinator.data:
            return None
        return [_hourly_to_forecast(point) for point in self.coordinator.data.hourly]

    async def async_forecast_twice_daily(self) -> list[Forecast] | None:
        if not self.coordinator.data:
            return None
        blocks = _group_into_day_night_blocks(self.coordinator.data.hourly)
        return [_block_to_forecast(block_date, is_daytime, points) for block_date, is_daytime, points in blocks]


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


def _daily_to_forecast(point: DailyPoint, hourly_points: list[HourlyPoint] | None = None) -> Forecast:
    condition = _pick_daily_condition(point.date.date(), hourly_points) if hourly_points else None
    if condition is None:
        # No matching hourly coverage for this date (e.g. the last day or
        # two of the 7-day daily range can fall outside our 7-day hourly
        # window's exact boundaries) -- fall back to Open-Meteo's own daily
        # code. Daily summaries always use the daytime icon variant either
        # way, never clear-night.
        condition = condition_from_wmo(point.weather_code, True)
    return Forecast(
        datetime=point.date.isoformat(),
        condition=condition,
        native_temperature=point.temperature_max_c,
        native_templow=point.temperature_min_c,
        native_precipitation=point.precipitation_mm,
        precipitation_probability=point.precipitation_probability_max,
        native_wind_speed=point.wind_speed_max_kmh,
        wind_bearing=point.wind_direction_dominant_deg,
    )


def _block_to_forecast(block_date: date_, is_daytime: bool, points: list[HourlyPoint]) -> Forecast:
    conditions = _conditions_for_points(points)
    temps = [p.temperature_c for p in points if p.temperature_c is not None]
    precip_amounts = [p.precipitation_mm for p in points if p.precipitation_mm is not None]
    probabilities = [p.precipitation_probability for p in points if p.precipitation_probability is not None]

    wind_speed = None
    wind_bearing = None
    windy_points = [p for p in points if p.wind_speed_kmh is not None]
    if windy_points:
        windiest = max(windy_points, key=lambda p: p.wind_speed_kmh)
        wind_speed = windiest.wind_speed_kmh
        wind_bearing = windiest.wind_direction_deg

    return Forecast(
        datetime=points[0].time.isoformat(),
        is_daytime=is_daytime,
        condition=_pick_block_condition(conditions),
        # Day block: the period's high. Night block: the period's low --
        # each entry represents one half of the day, not a hi/lo pair the
        # way a single daily entry does.
        native_temperature=(max(temps) if is_daytime else min(temps)) if temps else None,
        native_precipitation=round(sum(precip_amounts), 2) if precip_amounts else None,
        precipitation_probability=max(probabilities) if probabilities else None,
        native_wind_speed=wind_speed,
        wind_bearing=wind_bearing,
    )
