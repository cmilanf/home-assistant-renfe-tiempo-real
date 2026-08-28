"""Fixtures for the Renfe Tiempo Real integration tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.renfe_tiempo_real.api import (
    ALERTS_PATH,
    DEFAULT_BASE_URL,
    DEPARTURES_PATH,
    FLEET_PATH,
    STATIONS_PATH,
)
from custom_components.renfe_tiempo_real.const import (
    CONF_STATION_ID,
    CONF_STATION_NAME,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURE_DIR = Path(__file__).parent / "fixtures"

STATION_CODE = "18000"
STATION_NAME = "Atocha Cercanías"

# The moment the departure board fixture was generated, as the board reports it.
BOARD_TIMESTAMP = "2026-08-05T16:22:41+02:00"


def url(path: str) -> str:
    """Return the absolute URL of one of the Renfe documents."""
    return f"{DEFAULT_BASE_URL}{path}"


def departures_url(station_code: str = STATION_CODE) -> str:
    """Return the absolute URL of a departure board."""
    return url(DEPARTURES_PATH.format(code=station_code))


def load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture."""
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def get_station_device(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    station_code: str = STATION_CODE,
) -> dr.DeviceEntry | None:
    """Return the device of a station, looked up through its config entry.

    Home Assistant 2026.9 deprecated `DeviceRegistry.async_get_device` because
    identifiers are no longer unique across config entries, and reports the call
    as an error rather than a warning when it comes from a test, where there is
    no `custom_components` frame to blame. Its replacement,
    `async_get_device_by_identifier`, only exists from that release on, so the
    suite would stop running against the older cores `hacs.json` claims to
    support. Walking the entry's devices works on every version.
    """
    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )
    return next((d for d in devices if (DOMAIN, station_code) in d.identifiers), None)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test."""
    yield


@pytest.fixture
def stations() -> dict[str, Any]:
    """Return the station catalogue payload."""
    return load_fixture("estaciones.json")


@pytest.fixture
def departures() -> dict[str, Any]:
    """Return the departure board payload."""
    return load_fixture("salidas.json")


@pytest.fixture
def alerts() -> dict[str, Any]:
    """Return the alerts payload."""
    return load_fixture("alerts.json")


@pytest.fixture
def fleet() -> dict[str, Any]:
    """Return the fleet payload."""
    return load_fixture("flota.json")


@pytest.fixture
def mock_renfe(
    aioclient_mock: AiohttpClientMocker,
    stations: dict[str, Any],
    departures: dict[str, Any],
    alerts: dict[str, Any],
    fleet: dict[str, Any],
) -> AiohttpClientMocker:
    """Mock every Renfe document used by the integration."""
    aioclient_mock.get(url(STATIONS_PATH), json=stations)
    aioclient_mock.get(departures_url(), json=departures)
    aioclient_mock.get(url(ALERTS_PATH), json=alerts)
    aioclient_mock.get(url(FLEET_PATH), json=fleet)
    return aioclient_mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a configured Renfe config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"{STATION_NAME} ({STATION_CODE})",
        unique_id=STATION_CODE,
        data={CONF_STATION_ID: STATION_CODE, CONF_STATION_NAME: STATION_NAME},
    )
