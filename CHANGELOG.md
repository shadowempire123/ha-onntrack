# Changelog

## 0.7.1 — 2026-09-15

### Fixed
- The Leaflet zoom control sat in the top left corner, directly on top of the
  waypoint panel. The control now lives in the bottom right corner, and is
  hidden below 760 px where the waypoint list moves to the bottom.
- The route page is rendered in English and formats dates using the browser's
  locale instead of a hard-coded `de-AT`.

## 0.7.0 — 2026-09-15

### Security
- **Route pages are no longer written into `config/www`.** Files there are
  served as `/local` without any authentication, and the generated page carried
  the full coordinate list inline — so a vehicle's movement history was
  readable by anyone who could reach the Home Assistant instance and knew or
  guessed the map id. The route endpoint had `requires_auth = False` on top of
  that.
- The page is now served by `OnntrackRouteMapView` at
  `/api/onntrack/map/{map_id}` and contains no coordinates; it loads positions
  from the route endpoint at runtime.
- Both views check either a Home Assistant session or a per-config-entry token
  (`secrets.token_urlsafe(32)`, compared with `hmac.compare_digest`). A
  dashboard iframe cannot send an `Authorization` header, which is why the
  token travels in the URL.
- Route files written by earlier versions are deleted from `config/www` on
  setup.

### Changed
- Reverse geocoding now follows the Nominatim usage policy: requests are at
  least 1.1 s apart process-wide, an installation makes at most one uncached
  lookup every 120 s, the address cache is persisted and survives restarts, and
  HTTP 403 or 429 pauses the geocoder for an hour. Previously a driving vehicle
  produced one request per minute for the whole trip.
- The last resolved address per device is kept while a lookup is throttled, so
  the address sensor no longer drops to empty in between.
- Both `User-Agent` headers are built from a single version constant, and the
  Nominatim one carries a contact URL as the policy requires.

### Added
- Test suite that runs without a Home Assistant installation.
- MIT license.
