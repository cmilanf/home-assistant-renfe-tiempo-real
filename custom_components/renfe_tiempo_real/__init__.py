"""The Renfe Tiempo Real integration.

Exposes the real time departure board of any Renfe Cercanías station using the
public documents behind https://tiempo-real.renfe.com.
"""

from __future__ import annotations

import logging

from homeassistant.const import CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import RenfeClient, RenfeError
from .card import async_register_frontend
from .const import (
    CONF_ALERTS,
    CONF_STATION_ID,
    DEFAULT_ALERTS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import RenfeConfigEntry, RenfeCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

DATA_CLIENT = "client"


@callback
def async_get_client(hass: HomeAssistant) -> RenfeClient:
    """Return the client shared by every station and by the config flow.

    One client means one in-memory copy of the 400 KB station catalogue and one
    download of the global alert document per polling cycle, however many
    stations are configured.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if DATA_CLIENT not in domain_data:
        domain_data[DATA_CLIENT] = RenfeClient(async_get_clientsession(hass))
    client: RenfeClient = domain_data[DATA_CLIENT]
    return client


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration wide actions and the Lovelace card."""
    async_setup_services(hass)
    try:
        await async_register_frontend(hass)
    except Exception:  # noqa: BLE001 - the card is optional, the sensors are not
        _LOGGER.exception("Could not register the Renfe card, sensors still work")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RenfeConfigEntry) -> bool:
    """Set up Renfe Tiempo Real from a config entry."""
    client = async_get_client(hass)

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = RenfeCoordinator(
        hass,
        entry,
        client,
        entry.data[CONF_STATION_ID],
        scan_interval,
        with_alerts=entry.options.get(CONF_ALERTS, DEFAULT_ALERTS),
    )

    try:
        await coordinator.async_load_station()
    except RenfeError as err:
        raise ConfigEntryNotReady(
            f"Unable to fetch metadata for station {entry.data[CONF_STATION_ID]}: {err}"
        ) from err

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RenfeConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: RenfeConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
