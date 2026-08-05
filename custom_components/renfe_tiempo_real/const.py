"""Constants for the Renfe Tiempo Real integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "renfe_tiempo_real"

CONF_STATION_ID: Final = "station_id"
CONF_STATION_NAME: Final = "station_name"
CONF_NUCLEUS: Final = "nucleus"
CONF_SEARCH: Final = "search"
CONF_MAX_DEPARTURES: Final = "max_departures"
CONF_ALERTS: Final = "alerts"

# The departure board is regenerated once a minute, so polling faster than that
# only costs bandwidth. Three minutes is a courteous default for an endpoint
# nobody promised us; lower it in the options if a commute needs it.
DEFAULT_SCAN_INTERVAL: Final = 180
MIN_SCAN_INTERVAL: Final = 30
MAX_SCAN_INTERVAL: Final = 900

DEFAULT_MAX_DEPARTURES: Final = 5
DEFAULT_ALERTS: Final = True

SERVICE_REFRESH: Final = "refresh"

FRONTEND_URL_BASE: Final = "/renfe-tiempo-real-frontend"
CARD_FILENAME: Final = "renfe-tiempo-real-card.js"

ATTRIBUTION: Final = "Data provided by Renfe Viajeros"

MANUFACTURER: Final = "Renfe Cercanías"

WEB_URL: Final = "https://tiempo-real.renfe.com"

# How many departures the station level sensor lists in its attributes,
# regardless of the per line option. Recorder stores every attribute on every
# state change, so this is deliberately bounded.
STATION_ATTRIBUTE_LIMIT: Final = 20

# How many alert texts a sensor publishes in its attributes.
ALERT_ATTRIBUTE_LIMIT: Final = 10
