# Swiss Precipitation Watch (MeteoSwiss data via Open-Meteo)

> **Not affiliated with, endorsed by, or sponsored by MeteoSwiss, the
> Federal Office of Meteorology and Climatology, or Open-Meteo.** This is
> an independent, non-commercial, open-source hobby project that uses
> MeteoSwiss's Open Data (CC BY 4.0) via the Open-Meteo API. "MeteoSwiss"
> is named here only to accurately describe the data source, per its
> [Open Data terms](https://opendatadocs.meteoswiss.ch).

A generic Home Assistant integration: give it a point — fixed coordinates,
or any entity that exposes `latitude`/`longitude` attributes (a
`device_tracker`, a `person`, etc.) — and it produces precipitation
probability / amount / next-rain-time entities for that point, wherever it
currently is.

It does **not** know or care about specific use cases like "car parking
alerts." That logic belongs in your own automations, triggered off the
`binary_sensor.<name>_precipitation_expected` entity this integration
creates.

**Works globally, best in Switzerland/the Alps.** Open-Meteo's `best_match`
uses MeteoSwiss's high-resolution regional model (ICON-CH1/CH2) specifically
for Switzerland and the Alps, and falls back to other models elsewhere —
there's no hard geographic restriction, but the accuracy advantage this
integration is built around is regional.

## Data source

Weather data comes from [Open-Meteo](https://open-meteo.com)'s
`best_match` model selection, which uses MeteoSwiss's high-resolution
ICON-CH1/CH2 models inside Switzerland and the Alps. Data is
[CC BY 4.0](https://open-meteo.com/en/license) — free for personal home
automation use, attribution required (already included in every entity via
`attribution`). Free tier: 10,000 calls/day, non-commercial use only.

## Setup

1. Copy `custom_components/precipitation_watch/` into your HA `custom_components/` directory.
2. Restart Home Assistant.
3. Settings → Devices & Services → Add Integration → "Precipitation Watch".
4. Choose:
   - **Fixed coordinates** — a static point (garden, cabin, ...).
   - **Track an entity** — pick a `device_tracker` or `person` entity.
     Your example: `device_tracker.xyz` exposing
     `latitude`/`longitude` attributes works directly here.
5. After creation, use the entry's **Configure** button (the gear icon —
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

Real-world testing at a point in the Gotthard area (46.5057°N, 8.5105°E)
found a 3km sampling ring spanning **1414m to 2485m elevation** — a
1000m+ swing. The higher points showed dramatically more precipitation
(70% probability, up to 13.7mm/hr) than the exact tracked point (23%,
under 1mm/hr). That's not a localized cell the exact point would otherwise
miss — it's real orographic precipitation, genuinely wetter higher up as a
physical effect of the terrain, confirmed by checking Open-Meteo's own
docs: elevation-based downscaling in their API applies to temperature and
temperature-derived fields, *not* precipitation, so there's no simple
"tell it the real elevation" correction available. Blending in a ring
point 1000m higher would systematically over-alert for a point sitting in
a valley.

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
STAC search API rather than a simple GET. Not implemented here — worth a
dedicated follow-up if `current_precipitation` still isn't tight enough for
your use case.

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

## Testing

```bash
pip install -r requirements_test.txt
pytest
```

- `tests/test_api.py` — pure parsing/derivation logic, no HA or network needed.
- `tests/test_coordinator.py` — distance math (always runs) plus full
  config-entry/coordinator integration tests using
  `pytest-homeassistant-custom-component`'s `hass` fixture and your
  `device_tracker.xyz` fixture coordinates, with the Open-Meteo call mocked
  out (no live network calls in tests).

## Migrating from the old `swiss_rain_alert` domain

If you already have this installed under the old name: the domain rename
means HA treats it as a different integration. Remove the old config
entries (Settings → Devices & Services → the old entries → delete), install
this version, and re-add your watched points. Sorry for the churn — better
now than after anyone else has installed it.

## Publishing checklist (HACS custom repository)

- [x] `hacs.json` at repo root
- [x] Single integration under `custom_components/precipitation_watch/`
- [x] `custom_components/precipitation_watch/brand/icon.png` (256x256, self-hosted brand asset)
- [ ] Push to a public GitHub repo
- [ ] Set a repo description + topics (used by HACS for search/display)
- [ ] Cut a proper GitHub **Release** (not just a tag) matching `manifest.json`'s `version`
- [ ] Update `manifest.json`'s `codeowners`/`documentation`/`issue_tracker` placeholders to your real GitHub username/repo
- [ ] Then: HACS → ⋮ → Custom repositories → paste the repo URL

Default HACS store listing (searchable without adding a custom repo URL) is
a separate, optional, much slower step — a PR to `hacs/default` after the
above, reviewed by HACS maintainers, commonly taking months. Not needed to
actually use or share this integration.

## Known gaps / next steps

- **Deferred: a `WeatherEntity` for tracked points**, so a moving point could
  power the standard HA weather forecast card the way HA core's built-in
  Open-Meteo integration does for a fixed zone. Not built yet — real,
  scoped work (temperature/wind/condition-code data, WMO condition-code
  mapping, daily/hourly `Forecast` arrays), deferred until the sensor-based
  approach here has proven itself. For a normal *fixed*-location weather
  card, just use HA core's built-in Open-Meteo integration directly —
  no custom code needed for that case.
- No live-network verification has been done yet in this environment — the
  API client is built from Open-Meteo's documented parameter names
  (`hourly=precipitation_probability,precipitation`, `models=best_match`).
  First real call should happen on your own HA instance; if the response
  shape differs from what's assumed here, `api.py`'s `_parse` is the only
  place that needs adjusting.
- No `binary_sensor` availability handling yet distinguishes "never fetched"
  vs "fetch failed" vs "fetched but no rain" beyond `None`/`False` — worth
  tightening once you see real failure modes.
- No reconfigure flow for switching an existing entry between fixed/tracked
  mode — currently you'd remove and re-add.
- HACS `brands` repo submission and `hassfest` validation intentionally
  skipped per your request — needed before a real HACS listing.
