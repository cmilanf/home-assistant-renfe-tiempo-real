"""Registration of the Renfe Tiempo Real Lovelace card.

The card ships inside the integration, so it is served from a static path and
registered with Lovelace automatically. Users do not have to add a resource by
hand.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import (
    CONF_RESOURCE_TYPE_WS,
    LOVELACE_DATA,
    MODE_STORAGE,
)
from homeassistant.const import CONF_ID, CONF_TYPE, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CARD_FILENAME, DOMAIN, FRONTEND_URL_BASE

_LOGGER = logging.getLogger(__name__)


def _resource_path(url: str) -> str:
    """Return a resource URL without its cache-busting query string."""
    return url.partition("?")[0]


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Persist the card as a Lovelace resource when storage mode is available.

    ``add_extra_js_url`` alone has a startup race: the index page snapshots the
    current URLs, while later additions are one-shot websocket events. A client
    that loads the page before registration and subscribes after the event never
    sees the module. Lovelace's storage collection is persistent and its normal
    collection updates avoid that lost-event window.
    """
    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None or lovelace.resource_mode != MODE_STORAGE:
        return False

    resources = lovelace.resources
    # ResourceStorageCollection loads lazily. async_get_info is its public way
    # to ensure async_items contains the persisted resources.
    await resources.async_get_info()

    for resource in resources.async_items():
        current_url = resource.get(CONF_URL, "")
        if _resource_path(current_url) != _resource_path(url):
            continue

        updates = {}
        if current_url != url:
            updates[CONF_URL] = url
        if resource.get(CONF_TYPE) != "module":
            updates[CONF_RESOURCE_TYPE_WS] = "module"
        if updates:
            await resources.async_update_item(resource[CONF_ID], updates)
        return True

    await resources.async_create_item({CONF_RESOURCE_TYPE_WS: "module", CONF_URL: url})
    return True


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the card and tell the frontend to load it."""
    if "frontend" not in hass.config.components:
        # Headless installs, and the test harness, have no frontend. The
        # sensors and the action do not depend on the card.
        _LOGGER.debug("Frontend not loaded, Renfe card not registered")
        return

    directory = Path(__file__).parent / "frontend"
    if not await hass.async_add_executor_job((directory / CARD_FILENAME).is_file):
        _LOGGER.warning("Renfe card not found in %s, skipping registration", directory)
        return

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                FRONTEND_URL_BASE,
                str(directory),
                # No long lived cache headers: the ?v= query busts the cache on
                # release, but without this an edited card would stay stale in
                # the browser for weeks. The file is a few KB.
                cache_headers=False,
            )
        ]
    )

    # The integration version busts the browser cache on upgrade.
    integration = await async_get_integration(hass, DOMAIN)
    url = f"{FRONTEND_URL_BASE}/{CARD_FILENAME}?v={integration.version}"

    if not await _async_register_lovelace_resource(hass, url):
        # YAML resource mode and installations without Lovelace cannot be
        # mutated. Keep the previous frontend-module behavior as a fallback.
        add_extra_js_url(hass, url)
        _LOGGER.debug("Registered Renfe card as an extra frontend module")
    else:
        _LOGGER.debug("Registered Renfe card as a Lovelace resource")

    _LOGGER.debug("Serving Renfe card from %s", directory)
