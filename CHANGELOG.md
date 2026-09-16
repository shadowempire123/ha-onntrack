# Changelog

## Unreleased

### Added
- A My Home Assistant button in the README that opens this repository in HACS
  on the reader's own instance, and a second one that starts the config flow
  after the restart. The manual steps stay documented next to them: the button
  relies on my.home-assistant.io knowing the address of your instance, which
  not everyone wants to set up.

### Documentation
- Corrected the reason `ignore: brands` sits in the validation workflow. The
  comment claimed it was temporary, pending a pull request against
  home-assistant/brands. That pull request would be closed unread: since Home
  Assistant 2026.3 the brands repository no longer accepts icons for custom
  integrations, because they serve their own. The check can never pass and
  never needs to.
- Documented in the README where the brand images live and how Home Assistant
  serves them.

## 0.7.1 — 2026-09-15

### Added
- Releases are published by a workflow when a tag is pushed. It refuses a tag
  whose version disagrees with `manifest.json` — HACS matches the two, so such
  a release would never be offered as an update — and takes the release notes
  from the matching `CHANGELOG.md` section, so the notes cannot drift from the
  changelog. It runs on the automatic `GITHUB_TOKEN`; no personal access token
  is stored anywhere.

### Fixed
- The icons were the wordmark letterboxed into a square: in the 256x256 file
  the artwork occupied 230x48 px, the rest was transparent padding. Home
  Assistant's brands repository requires images trimmed to their content, and
  trimming a wordmark cannot produce a 1:1 image. Both icons are now built from
  Onntrack's own square brand mark, trimmed and scaled to 256x256 and 512x512.
  The logos were already within spec (shortest side 128 and 256 px).
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
