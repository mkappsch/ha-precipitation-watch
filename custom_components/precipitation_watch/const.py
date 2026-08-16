"""Constants for the Precipitation Watch integration."""
from __future__ import annotations

DOMAIN = "precipitation_watch"

# --- Config entry data keys -------------------------------------------------
CONF_MODE = "mode"
CONF_NAME = "name"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_TRACKED_ENTITY_ID = "tracked_entity_id"

MODE_FIXED = "fixed"
MODE_TRACKED = "tracked"

# --- Options keys (all throttling / alerting knobs live here, generic) -----
CONF_UPDATE_INTERVAL_MIN = "update_interval_minutes"
CONF_MIN_DISTANCE_M = "min_distance_meters"
CONF_PROBABILITY_THRESHOLD = "probability_threshold"
# Single window driving the alert binary_sensor + next_precipitation_time.
CONF_LOOKAHEAD_HOURS = "lookahead_hours"
# Separate, independent list of windows (comma-separated hours, e.g.
# "1,3,6,12") each producing their own probability + amount sensors, for
# browsing "will it rain in the next 1h vs 3h vs 6h" side by side. Does not
# affect the alert binary_sensor, which always uses CONF_LOOKAHEAD_HOURS.
CONF_DISPLAY_WINDOWS_HOURS = "display_windows_hours"
CONF_SAMPLE_RADIUS_KM = "sample_radius_km"
CONF_MAX_ELEVATION_DIFF_M = "max_elevation_diff_m"

DEFAULT_UPDATE_INTERVAL_MIN = 15
DEFAULT_MIN_DISTANCE_M = 200
DEFAULT_PROBABILITY_THRESHOLD = 50
DEFAULT_LOOKAHEAD_HOURS = 1
DEFAULT_DISPLAY_WINDOWS_HOURS = "1"
MAX_DISPLAY_WINDOWS = 6  # sanity cap, avoid someone pasting 50 values and flooding entities
# 0 = disabled, single-point only. >0 = also sample N/E/S/W points at this
# radius and take the max, to counter localized-cell under/over-estimation
# at any single exact coordinate.
DEFAULT_SAMPLE_RADIUS_KM = 3
# Exclude ring points whose terrain elevation differs from the tracked
# point's by more than this. In mountains, "nearby" can mean "1000m
# higher up" -- a different microclimate with genuinely more orographic
# precipitation, not a cell we'd otherwise miss. 300m is a starting point,
# not a validated constant -- tune per how mountainous your watched points are.
DEFAULT_MAX_ELEVATION_DIFF_M = 300

# --- API -------------------------------------------------------------------
API_BASE_URL = "https://api.open-meteo.com/v1/forecast"
API_TIMEOUT_SECONDS = 15
ATTRIBUTION = "Weather data by Open-Meteo.com (MeteoSwiss ICON model, CC BY 4.0)"

# How many hours of hourly forecast to request/keep around. Kept modest
# since we only ever look a handful of hours ahead, but a bit of headroom
# avoids re-fetching if the user bumps lookahead_hours via options.
FORECAST_HOURS_REQUESTED = 24

PLATFORMS = ["sensor", "binary_sensor"]
