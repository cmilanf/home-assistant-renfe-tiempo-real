"""Tests for the Renfe sensors and the entry lifecycle."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_SCAN_INTERVAL, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.renfe_tiempo_real.api import ALERTS_PATH, STATIONS_PATH
from custom_components.renfe_tiempo_real.const import (
    ATTRIBUTION,
    CONF_ALERTS,
    CONF_MAX_DEPARTURES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    WEB_URL,
)

from .conftest import (
    BOARD_TIMESTAMP,
    STATION_CODE,
    STATION_NAME,
    departures_url,
    get_station_device,
    url,
)

_SLUG = "atocha_cercanias"
NEXT_DEPARTURE = f"sensor.{_SLUG}_next_departure"
DATA_TIMESTAMP = f"sensor.{_SLUG}_data_timestamp"
ALERTS = f"sensor.{_SLUG}_service_alerts"
ROUTE_C7 = f"sensor.{_SLUG}_c7_alcala_de_henares"
ROUTE_C3_ARANJUEZ = f"sensor.{_SLUG}_c3_aranjuez"
ROUTE_C3_CHAMARTIN = f"sensor.{_SLUG}_c3_madrid_chamartin_clara_campoamor"

# One second past the poll interval, so a scheduled refresh fires.
POLL_TICK = timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1)


async def setup_integration(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    options: dict[str, object] | None = None,
) -> None:
    """Add and set up the config entry, optionally with non default options."""
    config_entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(config_entry, options=options)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_entry_setup_and_unload(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The entry loads and unloads cleanly."""
    await setup_integration(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_entry_retries_when_catalogue_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A Renfe outage during setup schedules a retry instead of failing hard."""
    aioclient_mock.get(url(STATIONS_PATH), exc=aiohttp.ClientError("offline"))
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_entry_fails_for_a_station_outside_the_catalogue(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
) -> None:
    """A station Renfe has withdrawn cannot be set up."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="99999", data={"station_id": "99999"}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_device_registered_for_station(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A device is created for the station, named after its network."""
    await setup_integration(hass, config_entry)

    device = get_station_device(hass, config_entry)
    assert device is not None
    assert device.name == STATION_NAME
    assert device.model == "Madrid"
    assert device.manufacturer == "Renfe Cercanías"
    assert device.configuration_url == WEB_URL


async def test_route_sensors_created_per_line_and_destination(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Both directions of a line get their own entity."""
    await setup_integration(hass, config_entry)

    assert hass.states.get(ROUTE_C7) is not None
    assert hass.states.get(ROUTE_C3_ARANJUEZ) is not None
    assert hass.states.get(ROUTE_C3_CHAMARTIN) is not None

    registry = er.async_get(hass)
    entity = registry.async_get(ROUTE_C3_ARANJUEZ)
    assert entity is not None
    assert entity.unique_id == f"{STATION_CODE}_C3_60200"

    # The absolute time sensor of every route ships disabled.
    timestamp = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{STATION_CODE}_C3_60200_time"
    )
    assert timestamp is not None
    assert (
        registry.async_get(timestamp).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )


async def test_no_entity_for_a_train_that_terminates_here(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The board lists arrivals at a trip's last stop; they are not departures."""
    await setup_integration(hass, config_entry)

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{STATION_CODE}_C7_18000")
        is None
    )

    state = hass.states.get(NEXT_DEPARTURE)
    assert state is not None
    # Six entries on the board, one of which ends its run here.
    assert state.attributes["departure_count"] == 5
    assert state.attributes["terminating_count"] == 1
    assert all(
        item["destination_code"] != STATION_CODE
        for item in state.attributes["departures"]
    )


async def test_next_departure_uses_the_server_clock(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Waits are computed against the Renfe timestamp, not the local clock."""
    await setup_integration(hass, config_entry)

    state = hass.states.get(NEXT_DEPARTURE)
    assert state is not None
    # 16:22:41 -> 16:24:00 is 1.3 minutes, which rounds to 1.
    assert state.state == "1"
    assert state.attributes["attribution"] == ATTRIBUTION
    assert state.attributes["station_code"] == STATION_CODE
    assert state.attributes["station_name"] == STATION_NAME
    assert state.attributes["nucleus"] == "Madrid"
    assert state.attributes["data_timestamp"] == BOARD_TIMESTAMP
    assert state.attributes["in_service"] is True
    assert state.attributes["departure_count"] == 5
    assert state.attributes["poll_interval_seconds"] == DEFAULT_SCAN_INTERVAL
    assert state.attributes["lines"][:3] == ["C2", "C3", "C4"]

    first = state.attributes["departures"][0]
    assert first["line"] == "C7"
    assert first["line_colour"] == "#E5202A"
    assert first["destination"] == "Alcalá de Henares"
    assert first["minutes"] == 1
    assert first["delay"] == 4
    assert first["platform"] == "3"
    assert first["accessible"] is False
    assert first["status"] == "at_station"


async def test_data_timestamp_sensor(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The freshness sensor publishes the Renfe clock as a timestamp."""
    await setup_integration(hass, config_entry)

    state = hass.states.get(DATA_TIMESTAMP)
    assert state is not None
    assert state.state == "2026-08-05T14:22:41+00:00"
    assert state.attributes["device_class"] == "timestamp"


async def test_route_sensor_attributes(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A route sensor carries the platform, the delay and the next trains."""
    await setup_integration(hass, config_entry)

    state = hass.states.get(ROUTE_C3_CHAMARTIN)
    assert state is not None
    # 16:22:41 -> 16:30:00 is 7.3 minutes.
    assert state.state == "7"
    assert state.attributes["line"] == "C3"
    assert state.attributes["line_colour"] == "#952585"
    assert state.attributes["destination"] == "Madrid-Chamartín-Clara Campoamor"
    assert state.attributes["destination_code"] == "17000"
    assert state.attributes["delay"] == 0
    assert state.attributes["platform"] is None
    assert state.attributes["accessible"] is True
    # This train has not left its origin, so the board is showing the timetable.
    assert state.attributes["realtime"] is False
    assert [item["minutes"] for item in state.attributes["next_departures"]] == [
        7,
        22,
        30,
    ]

    running = hass.states.get(ROUTE_C7)
    assert running is not None
    assert running.attributes["realtime"] is True
    assert running.attributes["platform"] == "3"
    assert running.attributes["delay"] == 4


async def test_max_departures_option_limits_the_attribute(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The option bounds what recorder has to store on every state change."""
    await setup_integration(hass, config_entry, {CONF_MAX_DEPARTURES: 1})

    state = hass.states.get(ROUTE_C3_CHAMARTIN)
    assert state is not None
    assert len(state.attributes["next_departures"]) == 1


async def test_alerts_sensor_reports_station_and_line_alerts(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Alerts of the station and of the lines serving it are counted together."""
    await setup_integration(hass, config_entry)

    state = hass.states.get(ALERTS)
    assert state is not None
    assert state.state == "3"
    assert state.attributes["warning_count"] == 2
    assert state.attributes["information_count"] == 1
    targets = [item["target"] for item in state.attributes["alerts"]]
    assert targets == [STATION_CODE, "C7", "C3"]
    # The test harness runs in English and the station alert is published in it.
    assert state.attributes["alerts"][0]["text"].startswith("The lift on platform 5")
    # The line alerts are Spanish only, which is the documented fallback.
    assert state.attributes["alerts"][1]["text"].startswith("Circulación restablecida")


async def test_alert_text_follows_the_home_assistant_language(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A Spanish install gets the Spanish text of a multilingual alert."""
    hass.config.language = "es"
    await setup_integration(hass, config_entry)

    # The entity id follows the translated name, so it is looked up by unique id.
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{STATION_CODE}_alerts"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["alerts"][0]["text"].startswith("El ascensor de la vía 5")


async def test_alerts_can_be_turned_off(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """With the option off no alert document is downloaded at all."""
    await setup_integration(hass, config_entry, {CONF_ALERTS: False})

    state = hass.states.get(ALERTS)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert not any(ALERTS_PATH in str(call[1]) for call in mock_renfe.mock_calls)


async def test_alert_outage_does_not_break_the_departures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    stations,
    departures,
) -> None:
    """Departures are the point; a missing alert document is not fatal."""
    aioclient_mock.get(url(STATIONS_PATH), json=stations)
    aioclient_mock.get(departures_url(), json=departures)
    aioclient_mock.get(url(ALERTS_PATH), exc=aiohttp.ClientError("offline"))

    await setup_integration(hass, config_entry)

    assert hass.states.get(NEXT_DEPARTURE).state == "1"
    assert hass.states.get(ALERTS).state == "0"


async def test_station_without_trains_due_is_not_an_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    stations,
    alerts,
) -> None:
    """Renfe answers 404 outside service hours; the entity stays available."""
    aioclient_mock.get(url(STATIONS_PATH), json=stations)
    aioclient_mock.get(departures_url(), status=404)
    aioclient_mock.get(url(ALERTS_PATH), json=alerts)

    await setup_integration(hass, config_entry)

    state = hass.states.get(NEXT_DEPARTURE)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes["in_service"] is False
    assert state.attributes["departures"] == []
    assert hass.states.get(DATA_TIMESTAMP).state == STATE_UNKNOWN


async def test_outage_marks_entities_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    stations,
    departures,
    alerts,
) -> None:
    """A failing poll marks the entities unavailable rather than stale."""
    aioclient_mock.get(url(STATIONS_PATH), json=stations)
    aioclient_mock.get(departures_url(), json=departures)
    aioclient_mock.get(url(ALERTS_PATH), json=alerts)
    await setup_integration(hass, config_entry)
    assert hass.states.get(NEXT_DEPARTURE).state == "1"

    aioclient_mock.clear_requests()
    aioclient_mock.get(url(STATIONS_PATH), json=stations)
    aioclient_mock.get(departures_url(), exc=aiohttp.ClientError("offline"))
    aioclient_mock.get(url(ALERTS_PATH), json=alerts)
    async_fire_time_changed(hass, dt_util.utcnow() + POLL_TICK)
    await hass.async_block_till_done()

    assert hass.states.get(NEXT_DEPARTURE).state == STATE_UNAVAILABLE
    assert hass.states.get(ROUTE_C7).state == STATE_UNAVAILABLE


async def test_known_routes_survive_a_restart_without_service(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    stations,
    departures,
    alerts,
) -> None:
    """A route with no train due keeps its entity and its name."""
    aioclient_mock.get(url(STATIONS_PATH), json=stations)
    aioclient_mock.get(departures_url(), json=departures)
    aioclient_mock.get(url(ALERTS_PATH), json=alerts)
    await setup_integration(hass, config_entry)
    assert hass.states.get(ROUTE_C7) is not None

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    aioclient_mock.get(url(STATIONS_PATH), json=stations)
    aioclient_mock.get(departures_url(), status=404)
    aioclient_mock.get(url(ALERTS_PATH), json=alerts)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(ROUTE_C7)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes["friendly_name"] == f"{STATION_NAME} C7 → Alcalá de Henares"


async def test_scan_interval_option_is_applied(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Changing the interval reloads the entry with the new one."""
    await setup_integration(hass, config_entry, {CONF_SCAN_INTERVAL: 300})

    assert hass.states.get(NEXT_DEPARTURE).attributes["poll_interval_seconds"] == 300
