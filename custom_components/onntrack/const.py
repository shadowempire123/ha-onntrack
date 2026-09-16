from homeassistant.const import Platform

DOMAIN = "onntrack"
# Keep in sync with the version in manifest.json: it goes into the
# User-Agent that Nominatim requires for identification.
VERSION = "0.8.1"
SERVICE_GET_ROUTE = "get_route"
SERVICE_REGENERATE_ROUTE_TOKEN = "regenerate_route_token"
EVENT_ALERT = "onntrack_alert"
EVENT_STATUS_CHANGED = "onntrack_status_changed"
DEFAULT_BASE_URL = "https://platform.onntrack.nl"
DEFAULT_SCAN_INTERVAL = 60
CONF_BASE_URL = "base_url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"
# Polling interval in seconds, adjustable through the options flow. A tracker
# on a parked vehicle does not need to be asked every minute.
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 3600
# Reverse geocoding sends coordinates to OpenStreetMap's Nominatim service.
# It only runs when the portal supplies no address of its own, but it leaves
# the network either way, so it can be turned off.
CONF_REVERSE_GEOCODE = "reverse_geocode"
DEFAULT_REVERSE_GEOCODE = True
# Per-config-entry secret for the route URLs. Stored in entry.data so the
# dashboard URL survives a restart.
CONF_ROUTE_TOKEN = "route_token"
PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR]
