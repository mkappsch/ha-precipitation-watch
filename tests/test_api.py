"""Tests for api.py: parsing and the derived-value helpers on ForecastResult.

These run with plain pytest, no Home Assistant test harness required,
since api.py has no HA imports beyond aiohttp typing.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.precipitation_watch.api import OpenMeteoClient
from tests.conftest import DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE


def test_parse_builds_hourly_points(sample_payload):
    result = OpenMeteoClient._parse(DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE, sample_payload)
    assert result.latitude == DEVICE_TRACKER_LATITUDE
    assert result.longitude == DEVICE_TRACKER_LONGITUDE
    assert len(result.hourly) == 24
    assert result.hourly[0].precipitation_probability == 5


def test_max_probability_within_finds_rainy_hour(sample_payload):
    result = OpenMeteoClient._parse(DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE, sample_payload)
    # rainy_hour_index=3 is inside a 6 hour lookahead window
    assert result.max_probability_within(hours=6) == 85


def test_max_probability_within_excludes_out_of_window_rain(sample_payload):
    result = OpenMeteoClient._parse(DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE, sample_payload)
    # rainy_hour_index=3, so a 1-hour window (now..now+1h) should miss it
    assert result.max_probability_within(hours=1) != 85


def test_dry_forecast_has_low_max_probability(dry_payload):
    result = OpenMeteoClient._parse(DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE, dry_payload)
    assert result.max_probability_within(hours=24) == 5


def test_next_time_above_threshold(sample_payload):
    result = OpenMeteoClient._parse(DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE, sample_payload)
    next_time = result.next_time_above_threshold(hours=6, threshold=50)
    assert next_time is not None
    assert next_time == result.hourly[3].time


def test_next_time_above_threshold_none_when_dry(dry_payload):
    result = OpenMeteoClient._parse(DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE, dry_payload)
    assert result.next_time_above_threshold(hours=24, threshold=50) is None


def test_total_precipitation_within(sample_payload):
    result = OpenMeteoClient._parse(DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE, sample_payload)
    assert result.total_precipitation_within(hours=6) == pytest.approx(2.4)


def test_parse_raises_on_malformed_response():
    from custom_components.precipitation_watch.api import ApiError

    with pytest.raises(ApiError):
        OpenMeteoClient._parse(0.0, 0.0, {"unexpected": "shape"})


def test_current_precipitation_parsed(sample_payload):
    result = OpenMeteoClient._parse(DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE, sample_payload)
    assert result.current_precipitation_mm == 0.0


def test_current_precipitation_missing_block_defaults_to_none():
    """Older cached responses or partial payloads shouldn't crash parsing."""
    payload = {
        "hourly": {
            "time": ["2026-08-14T19:00"],
            "precipitation_probability": [0],
            "precipitation": [0.0],
        }
    }
    result = OpenMeteoClient._parse(0.0, 0.0, payload)
    assert result.current_precipitation_mm is None


def test_forecast_can_miss_localized_cell_that_current_catches(actively_raining_but_forecast_missed_payload):
    """Real-world case: hourly forecast says 0% everywhere, but it's actively raining now."""
    result = OpenMeteoClient._parse(
        DEVICE_TRACKER_LATITUDE, DEVICE_TRACKER_LONGITUDE, actively_raining_but_forecast_missed_payload
    )
    assert result.max_probability_within(hours=1) == 5  # forecast missed it
    assert result.current_precipitation_mm == 3.2  # but current catches it


def test_offset_coordinates_land_at_expected_distance():
    from custom_components.precipitation_watch.api import offset_coordinates

    lat, lon = 46.360525, 8.970639
    points = offset_coordinates(lat, lon, radius_km=3.0)
    assert len(points) == 4  # N, E, S, W

    for plat, plon in points:
        d = _haversine_meters(lat, lon, plat, plon)
        assert 2900 < d < 3100  # within ~3% of the 3km target


def test_combine_max_surfaces_cell_only_one_sample_point_caught():
    """The whole point of nearby-point sampling: if the exact center misses a
    localized cell but a nearby sample point catches it, the combined
    result should still show it."""
    from custom_components.precipitation_watch.api import ForecastResult, HourlyPoint

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    t0 = now

    center = ForecastResult(
        latitude=46.36, longitude=8.97, fetched_at=now,
        hourly=[HourlyPoint(time=t0, precipitation_probability=0, precipitation_mm=0.0)],
    )
    east_catches_it = ForecastResult(
        latitude=46.36, longitude=9.00, fetched_at=now,
        hourly=[HourlyPoint(time=t0, precipitation_probability=85, precipitation_mm=2.4)],
        current_precipitation_mm=4.2,
    )

    combined = ForecastResult.combine_max(46.36, 8.97, [center, east_catches_it])
    assert combined.hourly[0].precipitation_probability == 85
    assert combined.hourly[0].precipitation_mm == 2.4
    assert combined.current_precipitation_mm == 4.2
    assert len(combined.sample_points) == 2


def test_combine_max_requires_at_least_one_result():
    from custom_components.precipitation_watch.api import ForecastResult

    with pytest.raises(ValueError):
        ForecastResult.combine_max(0.0, 0.0, [])


def _make_gotthard_ring():
    """Real-world fixture from a live test: 46.5057/8.5105, a point where a
    3km N/E/S/W ring spans 1400m-2485m elevation. Without filtering, the
    ring's higher (genuinely wetter, orographic) terrain drowns out the
    much drier exact point."""
    from custom_components.precipitation_watch.api import ForecastResult, HourlyPoint

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    def mk(lat, lon, elev, prob, current):
        return ForecastResult(
            latitude=lat, longitude=lon, fetched_at=now,
            hourly=[HourlyPoint(time=now, precipitation_probability=prob, precipitation_mm=0.0)],
            current_precipitation_mm=current,
            elevation_m=elev,
        )

    return [
        mk(46.50, 8.52, 1414.0, 23, 1.60),   # center
        mk(46.54, 8.52, 2430.0, 70, 1.10),   # N, +1016m
        mk(46.50, 8.54, 1834.0, 50, 0.30),   # E, +420m
        mk(46.48, 8.50, 2078.0, 70, 3.60),   # S, +664m
        mk(46.50, 8.48, 2485.0, 70, 4.80),   # W, +1071m
    ]


def test_combine_max_without_elevation_filter_is_dominated_by_higher_terrain():
    from custom_components.precipitation_watch.api import ForecastResult

    combined = ForecastResult.combine_max(46.5057, 8.5105, _make_gotthard_ring(), max_elevation_diff_m=None)
    assert combined.hourly[0].precipitation_probability == 70  # pulled up by +1000m ring points


def test_combine_max_tight_elevation_filter_falls_back_to_center_only():
    from custom_components.precipitation_watch.api import ForecastResult

    combined = ForecastResult.combine_max(46.5057, 8.5105, _make_gotthard_ring(), max_elevation_diff_m=300)
    assert combined.hourly[0].precipitation_probability == 23  # every ring point excluded, center-only
    included_flags = [sp[4] for sp in combined.sample_points]
    assert included_flags == [True, False, False, False, False]


def test_combine_max_looser_elevation_filter_includes_closer_points():
    from custom_components.precipitation_watch.api import ForecastResult

    combined = ForecastResult.combine_max(46.5057, 8.5105, _make_gotthard_ring(), max_elevation_diff_m=700)
    included_flags = [sp[4] for sp in combined.sample_points]
    # center (0m), E (+420m), S (+664m) included; N (+1016m), W (+1071m) excluded
    assert included_flags == [True, False, True, True, False]


def test_combine_max_all_points_excluded_still_returns_a_result():
    """Even at an impossibly tight threshold, we should never crash or
    return nothing -- fall back to the exact tracked point."""
    from custom_components.precipitation_watch.api import ForecastResult

    combined = ForecastResult.combine_max(46.5057, 8.5105, _make_gotthard_ring(), max_elevation_diff_m=1)
    assert combined.hourly[0].precipitation_probability == 23  # center-only fallback


def _haversine_meters(lat1, lon1, lat2, lon2):
    import math

    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def test_parse_display_windows_sorts_and_dedupes():
    from custom_components.precipitation_watch.api import parse_display_windows

    assert parse_display_windows("6,1,3,1", max_windows=6) == [1, 3, 6]


def test_parse_display_windows_strips_whitespace():
    from custom_components.precipitation_watch.api import parse_display_windows

    assert parse_display_windows(" 1, 3 , 6 ", max_windows=6) == [1, 3, 6]


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "0", "25", "abc", "1,2,3,4,5,6,7", "1,,3"],
)
def test_parse_display_windows_rejects_invalid_input(raw):
    from custom_components.precipitation_watch.api import InvalidWindowsError, parse_display_windows

    if raw == "1,,3":
        # empty chunks between commas are just skipped, not an error
        assert parse_display_windows(raw, max_windows=6) == [1, 3]
        return

    with pytest.raises(InvalidWindowsError):
        parse_display_windows(raw, max_windows=6)
