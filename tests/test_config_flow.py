"""Tests for the Renfe Tiempo Real config flow."""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.renfe_tiempo_real.api import STATIONS_PATH, Station
from custom_components.renfe_tiempo_real.config_flow import (
    normalise_station_input,
    station_label,
)
from custom_components.renfe_tiempo_real.const import (
    CONF_ALERTS,
    CONF_MAX_DEPARTURES,
    CONF_NUCLEUS,
    CONF_SEARCH,
    CONF_STATION_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

from .conftest import STATION_CODE, STATION_NAME, url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("18000", "18000"),
        (" 18000 ", "18000"),
        ("1 8000", "18000"),
        ("https://tiempo-real.renfe.com/?estacion=18000", "18000"),
        ("tiempo-real.renfe.com/station/71801", "71801"),
    ],
)
def test_normalise_station_input(raw: str, expected: str) -> None:
    """User input is accepted in several shapes."""
    assert normalise_station_input(raw) == expected


def test_station_label_lists_the_network_and_lines() -> None:
    """The picker has to be readable without opening a map."""
    station = Station(
        code="18000",
        name="Atocha Cercanías",
        nucleus="10",
        nucleus_name="Madrid",
        lines=("C3", "C7"),
    )

    assert station_label(station) == "Atocha Cercanías · Madrid · C3, C7 · 18000"
    assert station_label(Station(code="1", name="")) == "1"


async def test_user_step_shows_menu(hass: HomeAssistant) -> None:
    """The first step lets the user browse, search or type a code."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["nucleus", "search", "manual"]


async def test_manual_flow_creates_entry(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """Entering a station code creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATION_ID: STATION_CODE}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{STATION_NAME} ({STATION_CODE})"
    assert result["data"][CONF_STATION_ID] == STATION_CODE
    assert result["result"].unique_id == STATION_CODE


async def test_manual_flow_rejects_a_non_numeric_code(hass: HomeAssistant) -> None:
    """A code that is not a number never reaches the network."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATION_ID: "Atocha"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_STATION_ID: "invalid_station_code"}


async def test_manual_flow_rejects_an_unknown_code(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """A well formed code that is not in the catalogue is reported as such."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATION_ID: "99999"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_STATION_ID: "station_not_found"}


async def test_manual_flow_reports_an_outage(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A Renfe outage is reported instead of looking like a bad code."""
    aioclient_mock.get(url(STATIONS_PATH), exc=aiohttp.ClientError("offline"))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATION_ID: STATION_CODE}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_search_flow_with_one_match_skips_the_picker(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """An unambiguous search does not ask the user to confirm."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "search"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SEARCH: "atocha"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STATION_ID] == STATION_CODE


async def test_search_flow_offers_a_picker(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """Several matches are offered as a dropdown."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "search"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SEARCH: "madrid"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick"
    # The network name is searchable, so every Madrid station matches.
    assert result["description_placeholders"] == {"count": "3"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATION_ID: "17000"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STATION_ID] == "17000"


async def test_search_flow_reports_no_match(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """A search with no result keeps the user in the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "search"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SEARCH: "nowhere"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_stations_found"}


async def test_nucleus_flow_lists_the_network(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """Browsing a network narrows the search to its stations."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "nucleus"}
    )
    assert result["step_id"] == "nucleus"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NUCLEUS: "10"}
    )
    assert result["step_id"] == "search"
    assert result["description_placeholders"] == {"nucleus": "Madrid"}

    # An empty query lists the whole network.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SEARCH: ""}
    )
    assert result["step_id"] == "pick"
    assert result["description_placeholders"] == {"count": "3"}


async def test_nucleus_flow_aborts_on_an_outage(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The network list cannot be built without the catalogue."""
    aioclient_mock.get(url(STATIONS_PATH), exc=aiohttp.ClientError("offline"))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "nucleus"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_duplicate_station_is_rejected(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The same station cannot be added twice."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATION_ID: STATION_CODE}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The options flow stores the polling settings."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 300, CONF_MAX_DEPARTURES: 8, CONF_ALERTS: False},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_SCAN_INTERVAL: 300,
        CONF_MAX_DEPARTURES: 8,
        CONF_ALERTS: False,
    }


async def test_options_flow_defaults(
    hass: HomeAssistant,
    mock_renfe: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """An untouched entry offers the documented defaults."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    schema = result["data_schema"].schema
    defaults = {str(key): key.default() for key in schema}

    assert defaults[CONF_SCAN_INTERVAL] == DEFAULT_SCAN_INTERVAL
    assert defaults[CONF_MAX_DEPARTURES] == 5
    assert defaults[CONF_ALERTS] is True
