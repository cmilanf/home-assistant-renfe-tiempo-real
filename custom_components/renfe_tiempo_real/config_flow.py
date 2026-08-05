"""Config flow for the Renfe Tiempo Real integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import async_get_client
from .api import RenfeConnectionError, RenfeError, Station
from .const import (
    CONF_ALERTS,
    CONF_MAX_DEPARTURES,
    CONF_NUCLEUS,
    CONF_SEARCH,
    CONF_STATION_ID,
    CONF_STATION_NAME,
    DEFAULT_ALERTS,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import RenfeConfigEntry

_LOGGER = logging.getLogger(__name__)

# Station codes are four or five digits. The site keeps them in the URL
# fragment, so a copied link is accepted too.
STATION_CODE_RE = re.compile(r"^\d{3,6}$")
URL_STATION_RE = re.compile(r"(?:estacion|station|estacio)[=/](\d{3,6})")

# How many results a picker is allowed to hold. The dropdown filters as you
# type, so this only has to be generous enough for the largest network
# (Rodalies de Catalunya, just over 200 stations) to be browsed whole.
MAX_PICKER_OPTIONS = 250


def normalise_station_input(raw: str) -> str:
    """Extract a station code from user input.

    Accepts the code itself (``18000``) or a link that carries it.
    """
    value = raw.strip()
    if match := URL_STATION_RE.search(value):
        return match.group(1)
    return value.replace(" ", "")


def station_label(station: Station) -> str:
    """Build a human readable label for a station."""
    parts = [station.name or station.code]
    if station.nucleus_name:
        parts.append(station.nucleus_name)
    if station.lines:
        parts.append(", ".join(station.lines))
    # A station with no name is already labelled by its code.
    if station.name:
        parts.append(station.code)
    return " · ".join(parts)


def _station_options(stations: list[Station]) -> list[dict[str, str]]:
    """Turn stations into selector options."""
    return [
        {"value": station.code, "label": station_label(station)} for station in stations
    ]


class RenfeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Renfe Tiempo Real config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow state."""
        self._candidates: dict[str, Station] = {}
        self._nucleus: str | None = None
        self._nucleus_name: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: RenfeConfigEntry) -> RenfeOptionsFlow:
        """Return the options flow handler."""
        return RenfeOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose how to identify the station."""
        return self.async_show_menu(
            step_id="user", menu_options=["nucleus", "search", "manual"]
        )

    async def async_step_nucleus(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Browse the stations of one commuter network."""
        client = async_get_client(self.hass)

        try:
            stations = await client.async_get_stations()
        except RenfeConnectionError:
            return self.async_abort(reason="cannot_connect")
        except RenfeError:
            return self.async_abort(reason="unknown")

        # Nucleus names come from the catalogue rather than a hard coded table,
        # so a new commuter network appears without a code change.
        nuclei: dict[str, str] = {}
        for station in stations.values():
            if station.nucleus:
                nuclei.setdefault(station.nucleus, station.nucleus_name)

        if user_input is not None:
            self._nucleus = user_input[CONF_NUCLEUS]
            self._nucleus_name = nuclei.get(self._nucleus, "")
            return await self.async_step_search()

        options = [
            {"value": code, "label": name or code}
            for code, name in sorted(nuclei.items(), key=lambda item: item[1])
        ]
        return self.async_show_form(
            step_id="nucleus",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NUCLEUS): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Search stations by name, network or line."""
        errors: dict[str, str] = {}
        placeholders = {"nucleus": self._nucleus_name}
        if user_input is not None:
            query = user_input[CONF_SEARCH].strip()
            client = async_get_client(self.hass)
            try:
                stations = await client.async_search_stations(
                    query, nucleus=self._nucleus
                )
            except RenfeConnectionError:
                errors["base"] = "cannot_connect"
            except RenfeError:
                errors["base"] = "unknown"
            else:
                if not stations:
                    errors["base"] = "no_stations_found"
                elif len(stations) > MAX_PICKER_OPTIONS:
                    errors["base"] = "too_many_stations"
                    placeholders["count"] = str(len(stations))
                elif len(stations) == 1:
                    return await self._async_create_entry(stations[0])
                else:
                    self._candidates = {station.code: station for station in stations}
                    return await self.async_step_pick()

        return self.async_show_form(
            step_id="search",
            data_schema=vol.Schema({vol.Required(CONF_SEARCH, default=""): str}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one station out of the search results."""
        if user_input is not None:
            return await self._async_create_entry(
                self._candidates[user_input[CONF_STATION_ID]]
            )

        options = _station_options(list(self._candidates.values()))
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
            description_placeholders={"count": str(len(options))},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a station by entering its code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            code = normalise_station_input(user_input[CONF_STATION_ID])
            if not STATION_CODE_RE.match(code):
                errors[CONF_STATION_ID] = "invalid_station_code"
            else:
                client = async_get_client(self.hass)
                try:
                    station = await client.async_get_station(code)
                except RenfeConnectionError:
                    errors["base"] = "cannot_connect"
                except RenfeError:
                    errors[CONF_STATION_ID] = "station_not_found"
                else:
                    return await self._async_create_entry(station)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_STATION_ID): str}),
            errors=errors,
        )

    async def _async_create_entry(self, station: Station) -> ConfigFlowResult:
        """Create the config entry for the given station."""
        await self.async_set_unique_id(station.code)
        self._abort_if_unique_id_configured()
        title = f"{station.name} ({station.code})" if station.name else station.code
        return self.async_create_entry(
            title=title,
            data={CONF_STATION_ID: station.code, CONF_STATION_NAME: station.name},
        )


class RenfeOptionsFlow(OptionsFlow):
    """Handle the Renfe Tiempo Real options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the polling options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_MAX_DEPARTURES: int(user_input[CONF_MAX_DEPARTURES]),
                    CONF_ALERTS: bool(user_input[CONF_ALERTS]),
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=10,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_MAX_DEPARTURES,
                        default=options.get(
                            CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=20, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_ALERTS,
                        default=options.get(CONF_ALERTS, DEFAULT_ALERTS),
                    ): BooleanSelector(),
                }
            ),
        )
