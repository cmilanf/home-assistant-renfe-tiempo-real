"""Tests for the diagnostics dump."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.renfe_tiempo_real.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import STATION_CODE

REDACTED = "**REDACTED**"


async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Add and set up the config entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_diagnostics_redacts_every_location(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The station points at a home and each train reveals where it is."""
    await setup_integration(hass, config_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diagnostics["entry"]["data"]["station_id"] == STATION_CODE
    assert diagnostics["last_update_success"] is True
    assert diagnostics["station"]["name"] == "Atocha Cercanías"
    assert diagnostics["station"]["coordinates"] == REDACTED

    board = diagnostics["board"]
    assert board["station_code"] == STATION_CODE
    assert len(board["departures"]) == 5
    assert board["departures"][0]["line"] == "C7"
    assert board["departures"][0]["coordinates"] == REDACTED

    assert [alert["target"] for alert in diagnostics["alerts"]] == [
        STATION_CODE,
        "C7",
        "C3",
    ]
