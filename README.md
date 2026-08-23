# Point Weather Watch

> **Not affiliated with, endorsed by, or sponsored by MeteoSwiss, the
> Federal Office of Meteorology and Climatology, or Open-Meteo.** This is
> an independent, non-commercial, open-source hobby project that uses
> MeteoSwiss's Open Data (CC BY 4.0) via the Open-Meteo API. "MeteoSwiss"
> is named here only to accurately describe the data source, per its
> [Open Data terms](https://opendatadocs.meteoswiss.ch).

A Home Assistant weather integration that **follows a moving point**: give
it fixed coordinates, or any entity that exposes `latitude`/`longitude`
attributes (a `device_tracker`, a `person`, etc.), and it produces a full
weather entity (current conditions, hourly and daily forecast) plus
**configurable precipitation alert entities** — threshold and lookahead
window, tunable per watched point — for that point, wherever it currently
is. Home Assistant's own built-in weather integrations are fixed-zone
only; this one moves with a tracked entity, and ships alerting you'd
otherwise have to build yourself from a plain weather entity.

It does **not** know or care about specific use cases like "car parking
alerts." That logic belongs in your own automations, triggered off the
`binary_sensor.<name>_precipitation_expected` entity this integration
creates.

## When to use this vs. Home Assistant's built-in Open-Meteo integration

Home Assistant core already ships an official, zero-install Open-Meteo
integration (`open_meteo`) — no HACS, nothing to configure beyond picking
a zone. For a normal fixed-location weather card, **use that one first**:
the underlying forecast data is identical either way, since Open-Meteo's
automatic `best_match` model selection (MeteoSwiss's high-resolution
ICON-CH1/CH2 inside Switzerland/the Alps) is the default behavior
regardless of which integration asks for it — not something exclusive to
this project.

Reach for this integration instead when you need:

| | HA core's built-in `open_meteo` | This integration |
|---|---|---|
| Fixed-point weather (current + forecast) | ✓ | ✓ (identical data) |
| **Follows a moving entity** (`device_tracker`, `person`) | Not possible — fixed zone only | ✓ core feature |
| Configurable precipitation alert (`binary_sensor`, threshold + lookahead) | Build it yourself from the weather entity | ✓ out of the box |
| "Is it raining right now" nowcast sensor, distinct from the hourly forecast | No | ✓ |
| `next_precipitation_time` sensor | No | ✓ |
| Multiple forecast windows side by side (1h / 3h / 6h...) | No | ✓ |
| Nearby-point sampling + elevation filtering (catches localized cells, avoids mountain-elevation contamination) | No | ✓ |
| Install | Built-in | HACS custom repository |

If you only ever want a weather card for one fixed spot, HA core's own
integration is simpler and needs nothing extra installed. This project
exists for everything a fixed zone can't do.

**Tuned for Switzerland and the Alps, works worldwide.** Open-Meteo's
`best_match` uses MeteoSwiss's high-resolution regional model
(ICON-CH1/CH2) specifically for Switzerland and the Alps — real,
localized data, not a generic global model — and falls back to other
models elsewhere. That data-quality baseline is shared with HA core's
own integration for fixed points (see above); what this project adds on
top is everything in the table.

## Data source

Weather data comes from [Open-Meteo](https://open-meteo.com)'s
`best_match` model selection, which uses MeteoSwiss's high-resolution
ICON-CH1/CH2 models inside Switzerland and the Alps. Data is
[CC BY 4.0](https://open-meteo.com/en/license) — free for personal home
automation use, attribution required (already included in every entity via
`attribution`). Free tier: 10,000 calls/day, non-commercial use only.

### How this differs from other MeteoSwiss integrations

If you've compared this against [Rudd-O's `homeassistant-meteoswiss`](https://github.com/Rudd-O/homeassistant-meteoswiss)
or a similar integration and seen different numbers for the same location,
that's expected — they use a different pipeline end to end, not a bug in
either:

- **Current conditions**: that integration reads real physical weather
  station observations (or precipitation-only stations) from MeteoSwiss's
  own `data.geo.admin.ch`. This integration's `current_precipitation`
  sensor and the weather entity's current conditions instead come from
  Open-Meteo's model-based nowcast for your exact coordinates — an
  estimate, not a station reading. Trade-off either way: a station is
  real ground truth but may be several km from your actual point; the
  model is exactly at your coordinates but isn't a measurement.
- **Forecast**: national weather services almost always apply statistical
  post-processing/calibration on top of the raw numerical model before
  publishing their official public forecast. Open-Meteo's `best_match`
  serves the *raw* MeteoSwiss ICON-CH1/CH2 model output — an input into
  MeteoSwiss's official product, not identical to it.

Don't be surprised by a few degrees' difference or a differing condition
word between this and an official-source integration for the same
location. **This hasn't been validated for real-world accuracy yet, and
we'd love testers.** If you run it for a while — whether you spot a
systematic bias, a bug, or just have thoughts on what's missing —
[issue reports](https://github.com/mkappsch/ha-precipitation-watch/issues)
are genuinely welcome.

## Installation

### HACS (recommended)

1. Make sure [HACS](https://hacs.xyz) is installed.
2. HACS → ⋮ (top right) → **Custom repositories**.
3. Add `https://github.com/mkappsch/ha-precipitation-watch` as type
   **Integration** (or just search "Precipitation Watch" directly in that
   dialog — the repository is tagged for HACS to find without pasting the
   URL).
4. Install it from the listing that appears, then restart Home Assistant.

### Manual

1. Copy `custom_components/precipitation_watch/` into your Home
   Assistant `custom_components/` directory.
2. Restart Home Assistant.

## Configuration

1. Settings → Devices & Services → Add Integration → "Precipitation Watch".
2. Choose:
   - **Fixed coordinates** — a static point (garden, cabin, ...).
   - **Track an entity** — pick a `device_tracker` or `person` entity
     that exposes `latitude`/`longitude` attributes.
3. After creation, use the entry's **Configure** button (the gear icon —
   each watched point is its own config entry/"hub" with its own gear icon)
   to tune:
   - `update_interval_minutes` — floor between forecast refreshes (default 15)
   - `min_distance_meters` — for tracked mode, how far the point must move before re-fetching (default 200m)
   - `probability_threshold` — % that flips the alert binary sensor on (default 50)
   - `lookahead_hours` — window used by the alert binary sensor + next-precipitation-time sensor (default 1)
   - `display_windows_hours` — comma-separated hours, e.g. `1,3,6,12` — creates a probability + amount sensor pair *per value*, for browsing multiple horizons side by side (default `1`, max 6 values, each 1-24h)
   - `sample_radius_km` — also sample N/E/S/W points at this radius and take the max (default 3km, 0 disables)
   - `max_elevation_diff_m` — exclude sample-ring points whose terrain elevation differs from the tracked point's by more than this (default 300m)

   Changing any of these and saving reloads the entry, so entities (including
   the per-window ones) regenerate automatically to match.

### Nearby-point sampling (accuracy improvement)

Open-Meteo's own documentation for the MeteoSwiss model notes that highly
localized convective cells can be over- or underestimated at any single
exact coordinate. To counter this, when `sample_radius_km > 0` the
integration also queries N/E/S/W points at that radius (in the same
batched request, so no extra latency) and takes the max across all points
at each forecast hour and for the current-conditions block. If any nearby
sample point sees rain, the combined result does too.

The `sensor.<name>_precipitation_probability_<N>h` entities each expose a
`sample_points` attribute listing each sampled coordinate's elevation, its
own 1-hour max probability, and whether it was actually included in the
merge (see below).

Set `sample_radius_km` to `0` to disable and fall back to exact-point-only
queries (matches the original single-point behavior).

### Elevation filtering — why it exists

Testing at a point in the Gotthard area (46.5057°N, 8.5105°E) found a 3km
sampling ring spanning **1414m to 2485m elevation** — a 1000m+ swing. The
higher points showed dramatically more precipitation (70% probability, up
to 13.7mm/hr) than the exact tracked point (23%, under 1mm/hr). That's not
a localized cell the exact point would otherwise miss — it's real
orographic precipitation, genuinely wetter higher up as a physical effect
of the terrain, confirmed by checking Open-Meteo's own docs: elevation-based
downscaling in their API applies to temperature and temperature-derived
fields, *not* precipitation, so there's no simple "tell it the real
elevation" correction available. Blending in a ring point 1000m higher
would systematically over-alert for a point sitting in a valley.

`max_elevation_diff_m` fixes this using elevation data already returned
for free in the same API response (no extra call): any ring point whose
elevation differs from the tracked point's by more than this threshold is
excluded from the max-merge, though it's still recorded (with
`included: false`) in the `sample_points` attribute for visibility. If
every ring point gets excluded (very steep terrain), the integration falls
back to the exact point alone rather than returning nothing.

300m is a reasonable starting default, not a rigorously validated
constant — tune it based on how mountainous your watched points are. For
flat/urban locations it will rarely exclude anything; in the Alps it may
exclude most or all of the ring for some points, which is the intended,
conservative behavior.

## Entities created per watched point

| Entity | Description |
|---|---|
| `sensor.<name>_precipitation_probability_<N>h` | Max probability (%) within an N-hour window — one pair per entry in `display_windows_hours` (**forecast**) |
| `sensor.<name>_precipitation_amount_<N>h` | Summed expected precipitation (mm) within that same N-hour window (**forecast**) |
| `sensor.<name>_next_precipitation_time` | Timestamp of the next hour crossing your threshold, using `lookahead_hours` — **forecast** |
| `sensor.<name>_current_precipitation` | Observed/nowcast-blended precipitation (mm) **right now** — see below |
| `binary_sensor.<name>_precipitation_expected` | On/off, using `probability_threshold` + `lookahead_hours` — this is what your automations should trigger on |

With the default `display_windows_hours = "1"`, you get exactly the original
two sensors (`_precipitation_probability_1h`, `_precipitation_amount_1h`).
Set it to `1,3,6` to also get 3h and 6h variants of both, e.g. to compare
"is it about to rain" against "will it rain sometime this afternoon" without
touching the alert logic at all — the binary sensor stays governed solely by
`lookahead_hours`, independent of how many display windows you add.

### Forecast vs. "right now" — read this before relying on it for alerts

`precipitation_probability` and `precipitation_amount` come from Open-Meteo's
**hourly forecast model** (MeteoSwiss ICON-CH1/CH2 via `best_match`). This is
genuinely good data, but per Open-Meteo's own documentation: with 1-2km
resolution the model captures intense convective showers, but *due to their
highly localized nature, actual precipitation at a specific point may be
over- or underestimated* — confirmed in practice: a fast-forming storm cell
sitting directly over a test point showed 0% forecast probability while
MeteoSwiss's own radar app showed it actively raining there.

`current_precipitation` uses Open-Meteo's `current` block instead, which is
observed/nowcast-blended rather than an hours-ahead forecast, and tracks much
closer to "is it raining at this point right now." If you want the
forecast-probability binary sensor to be more honest, consider combining both:
an automation condition on `current_precipitation > 0 OR
precipitation_expected == on` catches both "raining now" and "forecast to
rain soon."

**Going further — MeteoSwiss's own INCA nowcasting data** (the exact system
behind their app's radar view) is also published as open data, at 10-minute
granularity. It's a separate, heavier lift: NetCDF files (not REST/JSON),
Swiss LV95 coordinates (needs reprojection from WGS84), and access via a
STAC search API rather than a simple GET. Not implemented here.

## Example automation (not part of the integration itself)

```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.car_precipitation_expected
    to: "on"
action:
  - service: notify.mobile_app_yourphone
    data:
      message: "Rain expected near your car within the hour"
```

## Manual refresh & debug logging

To force an immediate fetch, bypassing both throttles (useful when testing):

```yaml
service: precipitation_watch.refresh
target:
  device_id: <the watched point's device id>
```

(Find the device_id under Settings → Devices & Services → the entry's device page → ⋮ → Device info, or just call the service from Developer Tools → Actions and use the device picker.)

To see what the coordinator is actually doing (fetching vs. skipping due to the distance/time throttle), add to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.precipitation_watch: debug
```

## API usage & rate limits

This integration uses Open-Meteo's free tier (no API key): **10,000
calls/day, 5,000/hour, 600/minute**. For a normal setup you won't get
close to these:

- Each watched point fetches once per `update_interval_minutes` (default
  15min → 96 fetches/day). With the default `sample_radius_km=3` sampling
  ring, each fetch batches 5 coordinates (center + N/E/S/W) into one HTTP
  request. Open-Meteo's docs don't say whether a batched request counts as
  1 call or 5 against the quota — even under the pessimistic assumption
  (5), that's 480 calls/day per point, under 5% of the daily budget.
- There's no usage dashboard or rate-limit info for the free/keyless
  tier — checked directly, Open-Meteo's responses carry no `X-RateLimit-*`
  headers, so there's nothing to poll for a live "remaining quota." The
  most reliable way to see real call volume is the debug logging above:
  every fetch logs a `fetching forecast for (...)` line, so counting
  those over a day gives you an actual number, movement-triggered extras
  included.
- **Tracked mode**: movement-triggered refreshes (when the tracked entity
  crosses `min_distance_meters`) are floored by `update_interval_minutes`
  just like the periodic timer, so a continuously-moving point (e.g.
  driving) can't push the fetch rate past roughly double the periodic
  baseline. It's still a cheap floor rather than a smart one — see Known
  limitations for the "wait until actually stopped moving" idea that
  would avoid fetching mid-transit entirely.

None of this matters much for a handful of watched points at default
settings; it starts to matter with many watched points, an aggressively
short `update_interval_minutes`, or several continuously-moving tracked
points.

## Migrating from an old `swiss_rain_alert` install

If you have an existing install under the old `swiss_rain_alert` domain
(from a pre-release build of this project): the domain rename means Home
Assistant treats it as a different integration. Remove the old config
entries (Settings → Devices & Services → the old entries → delete), then
add this one fresh and re-create your watched points.

## Known limitations / roadmap

- **No `WeatherEntity` for tracked points.** A moving point can't yet power
  the standard HA weather forecast card the way HA core's built-in
  Open-Meteo integration does for a fixed zone — that would need real,
  separate work (temperature/wind/condition-code data, WMO condition-code
  mapping, daily/hourly `Forecast` arrays). For a normal *fixed*-location
  weather card, use HA core's built-in Open-Meteo integration directly —
  no custom component needed for that case.
- **`binary_sensor` availability** doesn't yet distinguish "never fetched"
  vs. "fetch failed" vs. "fetched but no rain" beyond `None`/`False`.
- **No reconfigure flow** for switching an existing entry between
  fixed/tracked mode — remove and re-add for now.
- **Movement-triggered refreshes only have a cheap time floor, not a smart
  one.** They're capped at `update_interval_minutes` (same as the
  periodic timer, see [API usage & rate limits](#api-usage--rate-limits)),
  which bounds worst-case API usage, but a continuously-moving point still
  fetches on that same cadence throughout the whole trip — once per
  `update_interval_minutes`, for a route that might not need updates at
  all until it actually arrives somewhere. A "wait until movement has
  settled" debounce (only fetch once the point has been roughly
  stationary for a bit, resetting while still moving) would skip
  in-transit fetches entirely and is a likely next addition.
- If the live API's response shape ever changes from what's assumed here
  (verified against a real call as of this writing — see `_parse` in
  `api.py`), please [open an issue](https://github.com/mkappsch/ha-precipitation-watch/issues).

## Contributing

```bash
pip install -r requirements_test.txt
pytest
```

- `tests/test_api.py` — pure parsing/derivation logic, no HA or network needed.
- `tests/test_coordinator.py` — distance math (always runs) plus full
  config-entry/coordinator integration tests using
  `pytest-homeassistant-custom-component`'s `hass` fixture, with the
  Open-Meteo call mocked out (no live network calls in tests).

**Windows note:** Home Assistant core imports the POSIX-only `fcntl` module
at startup, so the `hass`-fixture tests in `test_coordinator.py` can only run
on Linux/macOS (or WSL) — natively on Windows even collecting them crashes
pytest before it gets a chance to skip. Everything else (`test_api.py` plus
the pure-math tests in `test_coordinator.py`) runs fine on Windows with
`pytest -p no:homeassistant`.

CI runs the full test suite on Linux (`.github/workflows/test.yml`) plus
`hassfest` and HACS repository validation (`.github/workflows/validate.yml`)
on every push and pull request — that's the authoritative pass/fail signal.
