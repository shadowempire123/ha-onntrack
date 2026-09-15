# Onntrack for Home Assistant

[![hacs][hacs-badge]][hacs-url]

Home Assistant integration for Onntrack GPS trackers, developed against an
Onntrack Portable Pro+. It signs in to the Onntrack portal, polls the cloud API
once a minute, and exposes every device on the account as a device tracker with
a set of sensors. It also renders an interactive route map with stop detection.

> **Not affiliated with Onntrack.** This is an independent, unofficial
> integration built against the portal's undocumented web API. Onntrack can
> change or withdraw that API at any time, which will break this integration
> without warning. The Onntrack name and logo identify the supported hardware
> and belong to their owner.

## What you get

For every device on the account:

| Entity | Notes |
| --- | --- |
| `device_tracker.<device>_location` | Position, with battery, speed, address and alert attributes |
| `sensor.<device>_battery` | Battery percentage of the tracker |
| `sensor.<device>_status` | Portal status, for example moving, parked or static |
| `sensor.<device>_speed` | km/h; reports `0` while parked and keeps the raw value in `reported_speed` |
| `sensor.<device>_mileage` | Total distance, as a `total_increasing` sensor |
| `sensor.<device>_today_s_mileage` | Distance today |
| `sensor.<device>_address` | Street address of the current position |
| `sensor.<device>_gnss`, `_visible_satellites`, `_cellular_signal_strength` | Reception quality |
| `sensor.<device>_last_online`, `_last_fix` | Timestamps |
| `sensor.<device>_imei`, `_alerts` | Identity and active alert count |

Sensors appear only when the portal actually reports the underlying value.

Two events are fired on the bus: `onntrack_status_changed` when the portal
status changes, and `onntrack_alert` when an alert becomes active or a device
disappears from the account.

## Requirements

Home Assistant 2025.1 or newer, and an Onntrack portal account.

## Installation

### HACS

1. HACS → three-dot menu → **Custom repositories**.
2. Add `https://github.com/shadowempire123/ha-onntrack` with category
   **Integration**.
3. Download **Onntrack**, then restart Home Assistant.

### Manually

Copy `custom_components/onntrack` into your Home Assistant `config` directory
and restart.

## Configuration

**Settings → Devices & services → Add integration → Onntrack**, then enter the
portal URL (`https://platform.onntrack.nl` unless yours differs), username and
password. The credentials are verified during setup.

The password is sent to the portal MD5-hashed, which is the format the portal's
own client uses — that is the portal's design, not a choice made here. The
session token is kept in memory only and never written to the log.

## The route map

The `onntrack.get_route` action fetches a period from the portal and returns the
URL of a rendered map:

```yaml
action: onntrack.get_route
data:
  imei: "<the IMEI shown by sensor.<device>_imei>"
  start: 2026-09-01 00:00:00
  end: 2026-09-15 00:00:00
response_variable: route
```

The response contains `map_url`, `geojson_url`, `point_count` and
`map_point_count`. Put `map_url` into an iframe card to keep the map on a
dashboard:

```yaml
type: iframe
url: /api/onntrack/map/<map_id>?token=<token>
aspect_ratio: "60%"
```

The map lets you page through months or pick an explicit range; it lists
waypoints and detects stops (20 minutes or longer within 100 metres). Map tiles
come from OpenStreetMap and Leaflet is loaded from unpkg, so the browser
showing the map needs internet access.

### About the token in the URL

The route endpoints are not served from `/local`. Anything placed there is
readable by anyone who can reach your Home Assistant, with no login — which for
a page holding a vehicle's movement history is not acceptable. Instead the page
and its data come from authenticated views.

A dashboard iframe cannot send an `Authorization` header, so the views accept
two things: a normal Home Assistant session, or a 256-bit token generated per
config entry and compared in constant time. That token is what makes the iframe
work.

Treat the URL like a password: anyone holding it can read that device's route
history. It is stored in the config entry and stays stable across restarts. To
invalidate it, remove the integration and add it again — a new token is
generated on setup.

## Icon and logo

The brand images live in `custom_components/onntrack/brand/` and are served by
Home Assistant itself through its brands proxy, at
`/api/brands/integration/onntrack/icon.png`. Local images take priority over
the brands CDN, so nothing has to be registered anywhere: since Home Assistant
2026.3 the [brands repository](https://github.com/home-assistant/brands) no
longer accepts icons for custom integrations.

On older versions the frontend falls back to the CDN, which has no entry for
this integration and answers with a placeholder. The integration works either
way; only the icon is affected.

## Addresses and OpenStreetMap

When the portal supplies no address for a position, the integration asks
OpenStreetMap's Nominatim service to resolve the coordinates, which means those
coordinates leave your network. The
[Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)
is respected: requests are at least one second apart, a single installation
makes at most one uncached lookup every two minutes, resolved addresses are
cached on disk, and an HTTP 403 or 429 pauses the geocoder for an hour. While a
lookup is throttled the last known address is kept, so the sensor does not
flicker.

## Development

The test suite runs without a Home Assistant installation — the handful of
`homeassistant` and `aiohttp` names the modules need are stubbed in
`tests/conftest.py`:

```bash
pip install -r requirements_test.txt
pytest
```

Releasing is a single step: bump `version` in `manifest.json` and `VERSION` in
`const.py`, add a `CHANGELOG.md` section for it, then push the matching `v*`
tag. A workflow publishes the release with those changelog entries as its
notes, and refuses the tag if the versions disagree.

## License

MIT — see [LICENSE](LICENSE).

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
