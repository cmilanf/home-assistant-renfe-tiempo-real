"""Actions for the Renfe Tiempo Real integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_extract_config_entry_ids

from .const import DOMAIN, SERVICE_REFRESH
from .coordinator import RenfeConfigEntry

_LOGGER = logging.getLogger(__name__)

REFRESH_SCHEMA = vol.Schema(cv.TARGET_SERVICE_FIELDS)


async def _async_targeted_entries(
    hass: HomeAssistant, call: ServiceCall
) -> list[RenfeConfigEntry]:
    """Return the loaded Renfe entries the call is aimed at.

    Without a target every configured station is refreshed, which is what a user
    pressing a single dashboard button expects.
    """
    loaded: list[RenfeConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    if not call.data:
        return loaded
    entry_ids = await async_extract_config_entry_ids(call)
    return [entry for entry in loaded if entry.entry_id in entry_ids]


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Renfe actions."""

    async def async_handle_refresh(call: ServiceCall) -> None:
        """Poll Renfe immediately instead of waiting for the next interval."""
        entries = await _async_targeted_entries(hass, call)
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_station_targeted",
            )
        for entry in entries:
            _LOGGER.debug("Manual refresh requested for %s", entry.title)
            # Debounced by the coordinator: the first call runs immediately and
            # repeats within the cooldown coalesce, so an automation gone wild
            # cannot hammer the Renfe endpoint.
            await entry.runtime_data.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, async_handle_refresh, schema=REFRESH_SCHEMA
    )
