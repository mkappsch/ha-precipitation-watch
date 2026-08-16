"""Minimal async client for Open-Meteo's forecast API.

Deliberately dumb: takes coordinates, returns a parsed ForecastResult.
No knowledge of Home Assistant entities, config entries, or throttling
lives here -- that's the coordinator's job. Keeping this layer pure
makes it trivial to unit test with recorded JSON fixtures instead of
live HTTP calls.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp
import async_timeout

from .const import API_BASE_URL, API_TIMEOUT_SECONDS, FORECAST_HOURS_REQUESTED

_LOGGER = logging.getLogger(__name__)


class ApiError(Exception):
    """Raised for any failure talking to the weather API."""


@dataclass
class HourlyPoint:
    """One hourly forecast entry."""

    time: datetime
    precipitation_probability: int | None  # percent, 0-100
    precipitation_mm: float | None


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

        center_elevation = results[0].elevation_m
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
            included = [results[0]]

        by_time: dict[datetime, HourlyPoint] = {}
        for result in included:
            for point in result.hourly:
                existing = by_time.get(point.time)
                if existing is None:
                    by_time[point.time] = point
                else:
                    by_time[point.time] = HourlyPoint(
                        time=point.time,
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
            current_precipitation_mm=current_precip,
            current_rain_mm=current_rain,
            current_showers_mm=current_showers,
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
            "hourly": "precipitation_probability,precipitation",
            "current": "precipitation,rain,showers",
            "timezone": "UTC",
            "forecast_hours": str(FORECAST_HOURS_REQUESTED),
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

        points: list[HourlyPoint] = []
        for t, p, a in zip(times, probs, amounts):
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
                )
            )

        current_raw = payload.get("current") or {}

        return ForecastResult(
            latitude=latitude,
            longitude=longitude,
            fetched_at=datetime.now(timezone.utc),
            hourly=points,
            current_precipitation_mm=current_raw.get("precipitation"),
            current_rain_mm=current_raw.get("rain"),
            current_showers_mm=current_raw.get("showers"),
            elevation_m=payload.get("elevation"),
        )
