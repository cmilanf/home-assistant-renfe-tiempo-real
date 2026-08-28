"""Tests for the Renfe Tiempo Real actions."""

from __future__ import annotations

import pytest
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.renfe_tiempo_real.api import ALERTS_PATH, STATIONS_PATH
from custom_components.renfe_tiempo_real.const import (
    CONF_STATION_ID,
    CONF_STATION_NAME,
    DOMAIN,
    SERVICE_REFRESH,
)

from .conftest import STATION_CODE, departures_url, get_station_device, url

NEXT_DEPARTURE = "sensor.atocha_cercanias_next_departure"
ARANJUEZ_NEXT_DEPARTURE = "sensor.aranjuez_next_departure"


def _board_calls(aioclient_mock: AiohttpClientMocker, station: str) -> int:
    """Count the departure board requests performed for one station."""
    needle = f"/estacion/{station}.json"
    return len([call for call in aioclient_mock.mock_calls if needle in str(call[1])])


async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Add and set up the config entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_action_is_registered(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The refresh action exists once the integration is set up."""
    await setup_integration(hass, config_entry)

    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH)


async def test_refresh_without_target_polls_every_station(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    stations,
    departures,
    alerts,
) -> None:
    """Calling the action without a target refreshes all configured stations."""
    aioclient_mock.get(url(STATIONS_PATH), json=stations)
    aioclient_mock.get(url(ALERTS_PATH), json=alerts)
    aioclient_mock.get(departures_url(), json=departures)
    aioclient_mock.get(departures_url("60200"), status=404)

    await setup_integration(hass, config_entry)
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Aranjuez (60200)",
        unique_id="60200",
        data={CONF_STATION_ID: "60200", CONF_STATION_NAME: "Aranjuez"},
    )
    await setup_integration(hass, second)

    before = (
        _board_calls(aioclient_mock, STATION_CODE),
        _board_calls(aioclient_mock, "60200"),
    )

    await hass.services.async_call(DOMAIN, SERVICE_REFRESH, blocking=True)
    await hass.async_block_till_done()

    assert _board_calls(aioclient_mock, STATION_CODE) == before[0] + 1
    assert _board_calls(aioclient_mock, "60200") == before[1] + 1


async def test_refresh_with_entity_target_polls_one_station(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    stations,
    departures,
    alerts,
) -> None:
    """A targeted call leaves the other stations alone."""
    aioclient_mock.get(url(STATIONS_PATH), json=stations)
    aioclient_mock.get(url(ALERTS_PATH), json=alerts)
    aioclient_mock.get(departures_url(), json=departures)
    aioclient_mock.get(departures_url("60200"), status=404)

    await setup_integration(hass, config_entry)
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Aranjuez (60200)",
        unique_id="60200",
        data={CONF_STATION_ID: "60200", CONF_STATION_NAME: "Aranjuez"},
    )
    await setup_integration(hass, second)

    before = (
        _board_calls(aioclient_mock, STATION_CODE),
        _board_calls(aioclient_mock, "60200"),
    )

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REFRESH,
        {ATTR_ENTITY_ID: NEXT_DEPARTURE},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert _board_calls(aioclient_mock, STATION_CODE) == before[0] + 1
    assert _board_calls(aioclient_mock, "60200") == before[1]


async def test_refresh_accepts_a_device_target(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A dashboard can target the station device instead of an entity."""
    await setup_integration(hass, config_entry)
    device = get_station_device(hass, config_entry)
    assert device is not None

    before = _board_calls(mock_renfe, STATION_CODE)

    await hass.services.async_call(
        DOMAIN, SERVICE_REFRESH, {"device_id": device.id}, blocking=True
    )
    await hass.async_block_till_done()

    assert _board_calls(mock_renfe, STATION_CODE) == before + 1


async def test_refresh_rejects_an_unrelated_target(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Targeting something that is not a Renfe station is an error."""
    await setup_integration(hass, config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REFRESH,
            {ATTR_ENTITY_ID: "sensor.kitchen_temperature"},
            blocking=True,
        )
