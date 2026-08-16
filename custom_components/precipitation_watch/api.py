"""Minimal async client for Open-Meteo's forecast API.

Deliberately dumb: takes coordinates, returns a parsed ForecastResult.
No knowledge of Home Assistant entities, config entries, or throttling
lives here -- that's the coordinator's job. Keeping this layer pure
makes it trivial to unit test with recorded JSON fixtures instead of
live HTTP calls.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

import aiohttp
import async_timeout

from .const import (
    API_BASE_URL,
    API_TIMEOUT_SECONDS,
    FORECAST_DAYS_REQUESTED,
    FORECAST_HOURS_REQUESTED,
)

_LOGGER = logging.getLogger(__name__)


class ApiError(Exception):
    """Raised for any failure talking to the weather API."""


@dataclass
class HourlyPoint:
    """One hourly forecast entry."""

    time: datetime
    precipitation_probability: int | None  # percent, 0-100
    precipitation_mm: float | None
    # Everything below is for weather.py's forecast entities, not used by
    # the precipitation sensors/binary_sensor above.
    temperature_c: float | None = None
    wind_speed_kmh: float | None = None
    wind_direction_deg: float | None = None
    weather_code: int | None = None  # WMO code, see api._parse
    is_day: bool | None = None


@dataclass
class DailyPoint:
    """One daily forecast entry, for weather.py's forecast_daily."""

    date: datetime  # midnight UTC of that calendar day
    weather_code: int | None
    temperature_max_c: float | None
    temperature_min_c: float | None
    precipitation_mm: float | None
    precipitation_probability_max: int | None
    wind_speed_max_kmh: float | None
    wind_direction_dominant_deg: float | None


@dataclass
class ForecastResult:
    """Parsed forecast for a single point in time/space, or the max-combined
    result across several nearby sample points (see combine_max)."""

    latitude: float
    longitude: float
    fetched_at: datetime
    hourly: list[HourlyPoint]
    # Near-real-time observed/nowcast-blended precipitation for "right now",
    # as distinct from the hourly forecast probability. None if the API
    # didn't return a current block (e.g. older cached response).
    current_precipitation_mm: float | None = None
    current_rain_mm: float | None = None
    current_showers_mm: float | None = None
    # Daily forecast + "right now" weather fields, for weather.py. Unlike
    # the precipitation fields above, these are never max-merged across a
    # sampling ring (see combine_max) -- reporting the exact tracked
    # point's own temperature/wind/condition, not a ring point's, is what
    # a weather entity for that point should show.
    daily: list[DailyPoint] = None  # type: ignore[assignment]
    current_temperature_c: float | None = None
    current_wind_speed_kmh: float | None = None
    current_wind_direction_deg: float | None = None
    current_weather_code: int | None = None
    current_is_day: bool | None = None
    # Terrain elevation (m) of the grid cell Open-Meteo resolved this point
    # to. Used to filter out sample-ring points that land on a different
    # elevation band (see combine_max) -- in mountainous terrain, "nearby"
    # can mean "1000m higher up," which is a different microclimate, not a
    # cell we might have missed.
    elevation_m: float | None = None
    # Diagnostic: per-sample-point (lat, lon, elevation_m, max_probability,
    # included) when this result came from combine_max(). "included"
    # reflects whether the point passed the elevation-similarity filter and
    # actually contributed to the merged result. Empty for a plain
    # single-point result.
    sample_points: list[tuple[float, float, float | None, int | None, bool]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.sample_points is None:
            self.sample_points = []
        if self.daily is None:
            self.daily = []

    def max_probability_within(self, hours: int) -> int | None:
        """Highest precipitation probability among the next `hours` hourly points."""
        window = self._window(hours)
        probs = [p.precipitation_probability for p in window if p.precipitation_probability is not None]
        return max(probs) if probs else None

    def total_precipitation_within(self, hours: int) -> float | None:
        """Summed expected precipitation (mm) over the next `hours` hours."""
        window = self._window(hours)
        amounts = [p.precipitation_mm for p in window if p.precipitation_mm is not None]
        return round(sum(amounts), 2) if amounts else None

    def next_time_above_threshold(self, hours: int, threshold: int) -> datetime | None:
        """First forecast time within the window whose probability >= threshold."""
        for point in self._window(hours):
            if point.precipitation_probability is not None and point.precipitation_probability >= threshold:
                return point.time
        return None

    def _window(self, hours: int) -> list[HourlyPoint]:
        now = datetime.now(timezone.utc)
        return [p for p in self.hourly if now <= p.time <= _add_hours(now, hours)]

    @classmethod
    def combine_max(
        cls,
        center_latitude: float,
        center_longitude: float,
        results: list["ForecastResult"],
        max_elevation_diff_m: float | None = None,
    ) -> "ForecastResult":
        """Merge several single-point results (e.g. a center + N/E/S/W ring)
        into one "worst case across the sampled area" result, by taking the
        max at every matching hourly timestamp and for the current block.

        This directly counters the documented tendency of high-resolution
        convective precipitation forecasts to over/underestimate at any one
        exact point: if any nearby sample point sees rain, the combined
        result does too.

        In mountainous terrain, "nearby" can mean "far higher up" -- a 3km
        ring can span 1000m+ of elevation, and higher terrain genuinely
        gets more orographic precipitation as a real physical effect, not
        a localized cell we'd otherwise miss. If max_elevation_diff_m is
        given, any sample point whose elevation differs from the *first*
        result's (the exact tracked point) by more than that is excluded
        from the merge -- it's still recorded in sample_points for
        visibility, just marked not included.
        """
        if not results:
            raise ValueError("combine_max requires at least one result")

        center = results[0]
        center_elevation = center.elevation_m
        included: list[ForecastResult] = []
        sample_points: list[tuple[float, float, float | None, int | None, bool]] = []

        for result in results:
            is_included = True
            if (
                max_elevation_diff_m is not None
                and center_elevation is not None
                and result.elevation_m is not None
                and abs(result.elevation_m - center_elevation) > max_elevation_diff_m
            ):
                is_included = False

            if is_included:
                included.append(result)

            sample_points.append(
                (result.latitude, result.longitude, result.elevation_m, result.max_probability_within(1), is_included)
            )

        if not included:
            # Every ring point got filtered out (e.g. very steep terrain) --
            # fall back to the exact point alone rather than returning nothing.
            included = [center]

        # center is always first in `results` (by construction in
        # async_get_forecast) and always passes its own elevation-diff check
        # against itself, so it's always first in `included` too, and is
        # therefore always the first `result` this loop sees for any given
        # timestamp -- `replace(existing, ...)` below only ever touches the
        # precipitation fields, so temperature/wind/condition end up as
        # center's own values, never a ring point's.
        by_time: dict[datetime, HourlyPoint] = {}
        for result in included:
            for point in result.hourly:
                existing = by_time.get(point.time)
                if existing is None:
                    by_time[point.time] = point
                else:
                    by_time[point.time] = replace(
                        existing,
                        precipitation_probability=_max_optional(
                            existing.precipitation_probability, point.precipitation_probability
                        ),
                        precipitation_mm=_max_optional(existing.precipitation_mm, point.precipitation_mm),
                    )

        merged_hourly = [by_time[t] for t in sorted(by_time)]

        current_precip = _max_optional(*(r.current_precipitation_mm for r in included))
        current_rain = _max_optional(*(r.current_rain_mm for r in included))
        current_showers = _max_optional(*(r.current_showers_mm for r in included))

        return cls(
            latitude=center_latitude,
            longitude=center_longitude,
            fetched_at=max(r.fetched_at for r in results),
            hourly=merged_hourly,
            daily=center.daily,
            current_precipitation_mm=current_precip,
            current_rain_mm=current_rain,
            current_showers_mm=current_showers,
            current_temperature_c=center.current_temperature_c,
            current_wind_speed_kmh=center.current_wind_speed_kmh,
            current_wind_direction_deg=center.current_wind_direction_deg,
            current_weather_code=center.current_weather_code,
            current_is_day=center.current_is_day,
            elevation_m=center_elevation,
            sample_points=sample_points,
        )


def _max_optional(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _add_hours(dt: datetime, hours: int) -> datetime:
    from datetime import timedelta

    return dt + timedelta(hours=hours)


class InvalidWindowsError(ValueError):
    """Raised when a user-supplied display-windows string can't be parsed."""


def parse_display_windows(raw: str, max_windows: int, max_hours: int = 24) -> list[int]:
    """Parse a comma-separated hours string like "1,3,6,12" into a sorted,
    deduplicated list of ints, each in [1, max_hours], capped at max_windows
    entries. Raises InvalidWindowsError with a human-readable reason on any
    problem, so the config flow can show it back to the user instead of
    crashing.
    """
    raw = (raw or "").strip()
    if not raw:
        raise InvalidWindowsError("Enter at least one hour value, e.g. 1 or 1,3,6")

    values: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            hours = int(chunk)
        except ValueError as err:
            raise InvalidWindowsError(f"'{chunk}' is not a whole number of hours") from err
        if not (1 <= hours <= max_hours):
            raise InvalidWindowsError(f"{hours}h is out of range (must be 1-{max_hours})")
        values.add(hours)

    if not values:
        raise InvalidWindowsError("Enter at least one hour value, e.g. 1 or 1,3,6")
    if len(values) > max_windows:
        raise InvalidWindowsError(f"Too many windows ({len(values)}); max is {max_windows}")

    return sorted(values)


def offset_coordinates(latitude: float, longitude: float, radius_km: float) -> list[tuple[float, float]]:
    """Return [N, E, S, W] coordinates at radius_km from the given point.

    Uses a simple equirectangular approximation (fine at this radius scale,
    a few km) rather than full geodesic math -- good enough for choosing
    sample points, not for anything requiring survey-grade accuracy.
    """
    import math

    lat_offset_deg = radius_km / 111.32
    lon_offset_deg = radius_km / (111.32 * max(math.cos(math.radians(latitude)), 0.01))

    return [
        (latitude + lat_offset_deg, longitude),  # N
        (latitude, longitude + lon_offset_deg),  # E
        (latitude - lat_offset_deg, longitude),  # S
        (latitude, longitude - lon_offset_deg),  # W
    ]


class OpenMeteoClient:
    """Async client for the Open-Meteo forecast endpoint (MeteoSwiss model region)."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def async_get_forecast(
        self,
        latitude: float,
        longitude: float,
        sample_radius_km: float = 0,
        max_elevation_diff_m: float | None = None,
    ) -> ForecastResult:
        """Fetch and parse the hourly precipitation forecast for a coordinate.

        If sample_radius_km > 0, also samples N/E/S/W points at that radius
        in the same request (Open-Meteo supports batched comma-separated
        coordinates) and returns the max-combined result across all points,
        to counter under/overestimation of highly localized precipitation
        at any single exact coordinate. max_elevation_diff_m excludes ring
        points whose terrain elevation differs too much from the exact
        point's -- see combine_max for why that matters in the Alps.
        """
        points = [(latitude, longitude)]
        if sample_radius_km > 0:
            points += offset_coordinates(latitude, longitude, sample_radius_km)

        params = {
            "latitude": ",".join(f"{lat:.6f}" for lat, _ in points),
            "longitude": ",".join(f"{lon:.6f}" for _, lon in points),
            "hourly": "precipitation_probability,precipitation,temperature_2m,wind_speed_10m,wind_direction_10m,weather_code,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,wind_direction_10m_dominant",
            "current": "precipitation,rain,showers,temperature_2m,wind_speed_10m,wind_direction_10m,weather_code,is_day",
            "timezone": "UTC",
            "forecast_hours": str(FORECAST_HOURS_REQUESTED),
            "forecast_days": str(FORECAST_DAYS_REQUESTED),
            # best_match will use MeteoSwiss ICON-CH1/CH2 automatically inside
            # Switzerland/the Alps, and fall back sanely for points outside it.
            "models": "best_match",
        }

        try:
            async with async_timeout.timeout(API_TIMEOUT_SECONDS):
                async with self._session.get(API_BASE_URL, params=params) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise ApiError(f"Open-Meteo returned HTTP {resp.status}: {body[:200]}")
                    payload = await resp.json()
        except aiohttp.ClientError as err:
            raise ApiError(f"Network error calling Open-Meteo: {err}") from err
        except TimeoutError as err:
            raise ApiError("Timed out calling Open-Meteo") from err

        # With multiple coordinates, Open-Meteo returns a JSON array (one
        # object per point) instead of a single object.
        if isinstance(payload, list):
            results = [
                self._parse(lat, lon, single_payload)
                for (lat, lon), single_payload in zip(points, payload)
            ]
            return ForecastResult.combine_max(latitude, longitude, results, max_elevation_diff_m)

        return self._parse(latitude, longitude, payload)

    @staticmethod
    def _parse(latitude: float, longitude: float, payload: dict[str, Any]) -> ForecastResult:
        try:
            hourly_raw = payload["hourly"]
            times = hourly_raw["time"]
            probs = hourly_raw.get("precipitation_probability", [None] * len(times))
            amounts = hourly_raw.get("precipitation", [None] * len(times))
        except KeyError as err:
            raise ApiError(f"Unexpected Open-Meteo response shape, missing key: {err}") from err

        temps = hourly_raw.get("temperature_2m", [None] * len(times))
        wind_speeds = hourly_raw.get("wind_speed_10m", [None] * len(times))
        wind_dirs = hourly_raw.get("wind_direction_10m", [None] * len(times))
        codes = hourly_raw.get("weather_code", [None] * len(times))
        is_days = hourly_raw.get("is_day", [None] * len(times))

        points: list[HourlyPoint] = []
        for t, p, a, temp, wspd, wdir, code, is_day in zip(
            times, probs, amounts, temps, wind_speeds, wind_dirs, codes, is_days
        ):
            try:
                ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
            except ValueError:
                _LOGGER.debug("Skipping unparseable timestamp in Open-Meteo response: %s", t)
                continue
            points.append(
                HourlyPoint(
                    time=ts,
                    precipitation_probability=p,
                    precipitation_mm=a,
                    temperature_c=temp,
                    wind_speed_kmh=wspd,
                    wind_direction_deg=wdir,
                    weather_code=code,
                    is_day=bool(is_day) if is_day is not None else None,
                )
            )

        daily_raw = payload.get("daily") or {}
        daily_times = daily_raw.get("time", [])
        daily_codes = daily_raw.get("weather_code", [None] * len(daily_times))
        temps_max = daily_raw.get("temperature_2m_max", [None] * len(daily_times))
        temps_min = daily_raw.get("temperature_2m_min", [None] * len(daily_times))
        daily_precip = daily_raw.get("precipitation_sum", [None] * len(daily_times))
        daily_prob = daily_raw.get("precipitation_probability_max", [None] * len(daily_times))
        daily_wind = daily_raw.get("wind_speed_10m_max", [None] * len(daily_times))
        daily_wind_dir = daily_raw.get("wind_direction_10m_dominant", [None] * len(daily_times))

        daily_points: list[DailyPoint] = []
        for d, code, tmax, tmin, precip, prob, wind, wind_dir in zip(
            daily_times, daily_codes, temps_max, temps_min, daily_precip, daily_prob, daily_wind, daily_wind_dir
        ):
            try:
                day = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
            except ValueError:
                _LOGGER.debug("Skipping unparseable date in Open-Meteo daily response: %s", d)
                continue
            daily_points.append(
                DailyPoint(
                    date=day,
                    weather_code=code,
                    temperature_max_c=tmax,
                    temperature_min_c=tmin,
                    precipitation_mm=precip,
                    precipitation_probability_max=prob,
                    wind_speed_max_kmh=wind,
                    wind_direction_dominant_deg=wind_dir,
                )
            )

        current_raw = payload.get("current") or {}
        current_is_day = current_raw.get("is_day")

        return ForecastResult(
            latitude=latitude,
            longitude=longitude,
            fetched_at=datetime.now(timezone.utc),
            hourly=points,
            daily=daily_points,
            current_precipitation_mm=current_raw.get("precipitation"),
            current_rain_mm=current_raw.get("rain"),
            current_showers_mm=current_raw.get("showers"),
            current_temperature_c=current_raw.get("temperature_2m"),
            current_wind_speed_kmh=current_raw.get("wind_speed_10m"),
            current_wind_direction_deg=current_raw.get("wind_direction_10m"),
            current_weather_code=current_raw.get("weather_code"),
            current_is_day=bool(current_is_day) if current_is_day is not None else None,
            elevation_m=payload.get("elevation"),
        )
