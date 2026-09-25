# Changelog

## 0.9.0 — 2026-09-25

### Added
- **The alarm log from the Onntrack app.** A "Vibration alert" reached the
  phone through the Onntrack app and never reached Home Assistant: vibration,
  install and removal alarms are not part of `getMonitorInfo`, the only thing
  the integration read, so the alert detection there could not see them. They
  live in the portal's alarm report (`newReportAlarm/searchAlarmInfo`), which
  is now read on every poll.
  - The first run fetches the whole history the portal keeps -- on the
    author's tracker 386 alarms back to its activation in June -- quietly,
    without an event for each.
  - Every alarm after that fires `onntrack_alert` with `field: portal_alarm`,
    the kind in `value` and the alarm's own id, time and position.
  - A new sensor `last_alarm` has the time of the newest alarm as its state,
    so each one is a state change in the logbook, plus the last 50 and the
    totals per kind as attributes. Those two are kept out of the recorder.
  - `onntrack.get_alarms` returns the complete log.
  - The log is stored per config entry and survives restarts; a failed alarm
    request leaves it and the position sensors alone.

### Notes
- The portal writes its alarm times in the account's fixed offset
  (`timeZones: GMT+02:00`), without daylight saving. Reading them as local
  time would have put every winter alarm an hour off; they are converted with
  that offset instead.
- The report filters on alarm type codes and returns nothing when the filter
  is empty, so the integration asks for the account's list of 224 types once
  and sends all of them.

## 0.8.1 — 2026-09-16

### Fixed
- **Which portal field a sensor reads could change from one restart to the
  next.** The parser tries several names per value — `speed`, `gpsspeed`,
  `vehiclespeed` — and the order is meant to be the preference. They were held
  in a `set`, whose iteration order depends on string hashing, and Python
  randomises that per process. A device reporting two of the alternatives with
  different values would therefore pick a different one after every restart,
  silently. They are tuples now, so the written order is the order that counts.
  No device on the author's account reports two names from the same group, so
  this was latent rather than biting — but it is exactly the kind of fault that
  is impossible to reproduce once someone does report it.

### Added
- Tests for the parsing layer and for the coordinator's events. That layer
  guesses the field names of an undocumented API, and when the portal renames
  one a sensor goes quiet with nothing in the log; the events are what
  automations hang off, so a missed `onntrack_alert` means nobody hears that
  the tracker left the vehicle. Coverage of `api.py` went from 34% to 70%, of
  `coordinator.py` from nothing to 73%, and of the integration overall from
  37% to 51%. 164 tests.

## 0.8.0 — 2026-09-16

### Added
- **An options flow that actually has something in it.** Previously the
  "Configure" button opened a handler that created an empty entry and showed no
  form at all — from the outside that looks like a broken integration. It now
  offers the polling interval and a switch for reverse geocoding.
- **The polling interval is configurable** (30 s to 1 h, 60 s by default).
  `CONF_SCAN_INTERVAL` had been sitting in `const.py` unused; polling was hard
  wired. A tracker on a vehicle that stands still for eleven days does not need
  to be asked every minute.
- **Reverse geocoding can be turned off.** It only runs when the portal
  supplies no address, but the coordinates leave your network when it does, and
  that should be a choice.
- **Re-authentication.** When the portal stops accepting the stored password,
  Home Assistant now asks for a new one instead of failing every minute
  forever. Until now the only way out was deleting the integration and setting
  it up again.
- **`onntrack.regenerate_route_token`** issues a new route token and returns
  the new map URLs. The token is what protects the route endpoints, and there
  was no way to replace it short of removing the integration.
- **Diagnostics.** Settings → Devices & services → the three dots → Download
  diagnostics. The report is safe to paste into a public issue: credentials,
  route token, IMEI, device name, address and coordinates are gone, including
  the IMEIs used as keys in the device map, which a redaction helper working on
  values would have left untouched.
  The portal's raw payload cannot be cleaned by listing forbidden key names —
  it carries a display structure whose interesting values sit in generic
  `value` fields, so the same IMEI appears under half a dozen keys. It is
  therefore reduced to its shape: field names and types survive, which is what
  tells a maintainer what the portal actually sent, while the values do not. A
  value-based scrub runs over the result as a second line of defence.
- A My Home Assistant button in the README that opens this repository in HACS
  on the reader's own instance, and a second one that starts the config flow
  after the restart. The manual steps stay documented next to them: the button
  relies on my.home-assistant.io knowing the address of your instance, which
  not everyone wants to set up.

### Changed
- **Entity names come from the translations now** instead of being hard coded,
  so the German translation can finally name them. Existing entity IDs are
  unaffected; only the displayed names change, and only for non-English
  installations.
- **The password field in the setup dialog is a password field.** It used to be
  plain text, so the portal password was readable on screen while typing.
- **Devices added to the Onntrack account show up without a restart.** Entities
  were built once at setup, so a newly bought tracker stayed invisible.
- **The device tracker reports `battery_level`** through the property meant for
  it, rather than only as an attribute.

### Removed
- **`latitude` and `longitude` are no longer attached to every sensor.** All
  thirteen carried them, so each poll wrote the vehicle's position thirteen
  times into the recorder and any long-term database behind it. The position
  belongs on the device tracker, which has it.

### Fixed
- The release workflow fell over when a tag was pushed a second time at a
  different commit: it always called `gh release create`, which fails if a
  release for that tag already exists. It now updates the existing release
  instead.

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
