"""Tests for the Renfe API client and its response normalisation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.renfe_tiempo_real.api import (
    ALERT_INFORMATION,
    ALERT_SCOPE_LINE,
    ALERT_SCOPE_STATION,
    ALERT_WARNING,
    ALERTS_PATH,
    FALLBACK_LINE_COLOUR,
    STATIONS_PATH,
    Alert,
    Departure,
    Fleet,
    RenfeApiError,
    RenfeClient,
    RenfeConnectionError,
    RenfeStationNotFoundError,
    Station,
    StationBoard,
    alerts_for_station,
    as_list,
    as_mapping,
    as_text,
    line_colour,
    parse_board_datetime,
    parse_iso_datetime,
)

from .conftest import STATION_CODE, departures_url, load_fixture, url

MADRID = timezone(timedelta(hours=2))


def _client(hass, **kwargs) -> RenfeClient:
    """Return a client bound to the mocked aiohttp session of Home Assistant."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return RenfeClient(async_get_clientsession(hass), **kwargs)


def test_as_helpers_are_defensive() -> None:
    """The undocumented documents must not be trusted to have the right shape."""
    assert as_mapping({"a": 1}) == {"a": 1}
    assert as_mapping([1, 2]) == {}
    assert as_mapping(None) == {}
    assert as_list([1, 2]) == [1, 2]
    assert as_list({"a": 1}) == []
    assert as_list(None) == []


def test_as_text_normalises_codes() -> None:
    """Station codes arrive as integers in GeoJSON and as strings elsewhere."""
    assert as_text(18000) == "18000"
    assert as_text("18000") == "18000"
    assert as_text(18000.0) == "18000"
    assert as_text(None) == ""


def test_parse_board_datetime() -> None:
    """The board timestamp is dd-mm-yyyy and local to Spain."""
    assert parse_board_datetime("05-08-2026 16:22:41") == datetime(
        2026, 8, 5, 16, 22, 41, tzinfo=MADRID
    )
    assert parse_board_datetime("") is None
    assert parse_board_datetime(None) is None
    assert parse_board_datetime("2026-08-05T16:22:41") is None


def test_parse_board_datetime_is_offset_aware() -> None:
    """Waits are subtractions, so every timestamp has to carry an offset."""
    parsed = parse_board_datetime("05-08-2026 16:22:41")

    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=2)
    # Winter, when Spain is on CET rather than CEST.
    winter = parse_board_datetime("05-01-2026 16:22:41")
    assert winter is not None
    assert winter.utcoffset() == timedelta(hours=1)


def test_parse_iso_datetime() -> None:
    """The fleet timestamp is naive ISO and read in the Renfe timezone."""
    assert parse_iso_datetime("2026-08-05T16:22:10") == datetime(
        2026, 8, 5, 16, 22, 10, tzinfo=MADRID
    )
    assert parse_iso_datetime("2026-08-05T16:22:10+00:00") == datetime(
        2026, 8, 5, 16, 22, 10, tzinfo=UTC
    )
    assert parse_iso_datetime("nope") is None
    assert parse_iso_datetime(None) is None


def test_line_colour_prefers_the_nucleus() -> None:
    """The same line code means a different colour in each network."""
    assert line_colour("10", "C3") == "#952585"
    assert line_colour("60", "C3") == "#78B4E1"


def test_line_colour_resolves_a_unique_code_without_the_nucleus() -> None:
    """Barcelona-Sants reports nucleus 51, which has no colour table."""
    assert line_colour("51", "R2") == "#26A741"
    # C1 exists in eleven networks, so guessing would show the wrong colour.
    assert line_colour("99", "C1") == FALLBACK_LINE_COLOUR
    assert line_colour("10", "") == FALLBACK_LINE_COLOUR


def test_station_from_feature_reads_lines_and_facilities() -> None:
    """Station metadata exposes lines, network, coordinates and facilities."""
    feature = load_fixture("estaciones.json")["features"][0]
    station = Station.from_feature(feature)

    assert station.code == STATION_CODE
    assert station.name == "Atocha Cercanías"
    assert station.nucleus == "10"
    assert station.nucleus_name == "Madrid"
    # C2 sorts before C10, which a plain string sort gets wrong.
    assert station.lines == (
        "C2",
        "C3",
        "C4",
        "C4a",
        "C4b",
        "C5",
        "C7",
        "C8",
        "C8a",
        "C8b",
        "C10",
    )
    assert station.accessible is True
    assert station.bike_parking is False
    assert station.metro_connections == ("Metro: Línea 1",)
    assert station.coordinates is not None
    assert station.coordinates.is_valid
    assert station.line_colours["C3"] == "#952585"


def test_station_survives_a_stripped_feature() -> None:
    """A feature with nothing in it must not raise."""
    station = Station.from_feature({})

    assert station.code == ""
    assert station.lines == ()
    assert station.coordinates is None


def test_board_from_json_sorts_and_groups() -> None:
    """Departures are sorted by time and grouped per line and destination."""
    board = StationBoard.from_json(load_fixture("salidas.json"))

    assert board.station_code == STATION_CODE
    assert board.station_name == "Madrid-Atocha Cercanías"
    assert board.nucleus == "10"
    assert board.updated == datetime(2026, 8, 5, 16, 22, 41, tzinfo=MADRID)
    assert board.in_service is True
    # The fixture lists 16:45 first; the client sorts soonest first.
    assert [item.departure.strftime("%H:%M") for item in board.departures] == [
        "16:24",
        "16:26",
        "16:30",
        "16:45",
        "16:53",
    ]
    assert board.group_keys == ("C7_70103", "C3_60200", "C3_17000")
    assert board.lines == ("C7", "C3")
    assert len(board.departures_for("C3_17000")) == 3
    assert board.departures_for("C9_1") == ()


def test_board_keeps_terminating_trains_out_of_the_departures() -> None:
    """A trip ending here is an arrival at its last stop, not a departure.

    Renfe serves a per-station slice of a stop-times feed, so the final stop of
    every trip is listed with the station itself as the destination.
    """
    board = StationBoard.from_json(load_fixture("salidas.json"))

    assert [item.train for item in board.terminating] == ["79311"]
    terminating = board.terminating[0]
    assert terminating.terminates_here is True
    assert terminating.destination_code == board.station_code
    # It carries a real time and delay, it is just not something to board.
    assert terminating.delay == 16
    assert terminating.platform == "2"

    # Excluded from everything the sensors and the card consume.
    assert terminating not in board.departures
    assert all(not item.terminates_here for item in board.departures)
    assert "C7_18000" not in board.group_keys
    assert board.departures_for("C7_18000") == ()


def test_terminating_needs_both_codes_to_be_known() -> None:
    """A blank destination must not be read as "terminates here"."""
    assert Departure.from_json({}, "18000").terminates_here is False
    assert Departure.from_json({"destino": "18000"}, "").terminates_here is False
    assert Departure.from_json({"destino": "18000"}, "18000").terminates_here is True
    # The board reports the code as a string, the catalogue as an integer.
    assert Departure.from_json({"destino": "18000"}, 18000).terminates_here is True


def test_departure_reads_delay_platform_and_accessibility() -> None:
    """A departure carries everything the physical board shows."""
    board = StationBoard.from_json(load_fixture("salidas.json"))
    first = board.departures[0]

    assert first.line == "C7"
    assert first.train == "24132"
    assert first.destination == "Alcalá de Henares"
    assert first.destination_code == "70103"
    assert first.platform == "3"
    # 16:20 scheduled, 16:24 expected.
    assert first.delay == 4
    # `accesible` is 2 for this train, which means not accessible.
    assert first.accessible is False
    assert first.status == "at_station"
    assert first.has_started is True
    # 16:22:41 -> 16:24:00 is 1.3 minutes.
    assert round(first.minutes_from(board.updated), 1) == 1.3


def test_departure_that_has_not_left_its_origin_is_timetable_only() -> None:
    """Without a position Renfe is showing the timetable, not an estimate."""
    board = StationBoard.from_json(load_fixture("salidas.json"))
    timetabled = board.departures_for("C3_17000")[0]

    assert timetabled.train == "20059"
    assert timetabled.status == ""
    assert timetabled.has_started is False
    assert timetabled.delay == 0
    assert timetabled.platform == ""


def test_departure_can_run_early() -> None:
    """A negative delay is a train ahead of its timetable, not an error."""
    board = StationBoard.from_json(load_fixture("salidas.json"))
    early = board.departures[-1]

    assert early.train == "20063"
    assert early.delay == -3


def test_departure_minutes_never_go_negative() -> None:
    """A board that arrives late must not report a wait in the past."""
    board = StationBoard.from_json(load_fixture("salidas.json"))
    first = board.departures[0]

    assert first.minutes_from(None) is None
    assert first.minutes_from(first.departure + timedelta(minutes=5)) == 0.0


def test_departure_without_a_time_sorts_last() -> None:
    """A malformed timestamp must not push a train to the front of the board."""
    payload = load_fixture("salidas.json")
    payload["estacion"]["salidas"][3]["horaSalida"] = "who knows"
    board = StationBoard.from_json(payload)

    assert board.departures[-1].train == "24132"
    assert board.departures[-1].delay is None
    assert Departure.from_json({}).minutes_from(board.updated) is None


def test_board_staleness_uses_the_server_clock() -> None:
    """Renfe regenerates the board every minute; five is broken."""
    board = StationBoard.from_json(load_fixture("salidas.json"))
    assert board.updated is not None

    assert board.age(board.updated + timedelta(minutes=2)) == timedelta(minutes=2)
    assert board.is_stale(board.updated + timedelta(minutes=2)) is False
    assert board.is_stale(board.updated + timedelta(minutes=6)) is True

    empty = StationBoard.out_of_service(STATION_CODE)
    assert empty.age(datetime.now(tz=MADRID)) is None
    assert empty.is_stale(datetime.now(tz=MADRID)) is False


def test_board_handles_a_single_departure() -> None:
    """A quiet station has one train due and nothing else."""
    board = StationBoard.from_json(load_fixture("salidas_single.json"))

    assert board.station_code == "60200"
    assert len(board.departures) == 1
    assert board.group_keys == ("C3_17000",)


def test_fleet_reads_status_and_progress() -> None:
    """`porAvanc` is either a state or a percentage of progress."""
    fleet = Fleet.from_json(load_fixture("flota.json"))

    assert fleet.updated == datetime(2026, 8, 5, 16, 22, 10, tzinfo=MADRID)
    trains = fleet.by_trip_id()

    running = trains["1015X20061C3"]
    assert running.status == "en_route"
    assert running.progress == 63.0
    assert running.delay == 7
    assert running.next_arrival == datetime(2026, 8, 5, 16, 28, 42, tzinfo=MADRID)

    assert trains["1015X24132C7"].status == "at_station"
    assert trains["1015X24132C7"].progress is None
    assert trains["1015X20064C3"].status == "approaching"
    assert trains["1015X20064C3"].accessible is True

    # An unparseable delay and a missing status degrade instead of raising.
    unknown = trains["5015X31001R2"]
    assert unknown.delay is None
    assert unknown.status == ""
    assert unknown.next_arrival is None


def test_alert_picks_the_language_and_keeps_the_headline() -> None:
    """Renfe repeats a language for extra paragraphs; the first one wins."""
    payload = load_fixture("alerts.json")
    alert = Alert.from_json(
        payload["avisos"]["estacion"][0], ALERT_SCOPE_STATION, ALERT_WARNING
    )

    assert alert.languages == ("es", "en")
    assert alert.text("es").startswith("El ascensor de la vía 5")
    assert alert.text("en").startswith("The lift on platform 5")
    # An unpublished language falls back to Spanish, then to anything.
    assert alert.text("de") == alert.text("es")
    assert alert.text("en-GB").startswith("The lift")
    assert Alert.from_json({}, ALERT_SCOPE_LINE, ALERT_WARNING).text() == ""


def test_alerts_for_station_matches_the_station_and_its_lines() -> None:
    """A line alert only applies inside the network that published it."""
    stations = {
        Station.from_feature(feature).code: Station.from_feature(feature)
        for feature in load_fixture("estaciones.json")["features"]
    }
    payload = load_fixture("alerts.json")
    alerts = []
    for key, kind in (("avisos", ALERT_WARNING), ("informaciones", ALERT_INFORMATION)):
        for scope_key, scope in (
            ("estacion", ALERT_SCOPE_STATION),
            ("linea", ALERT_SCOPE_LINE),
        ):
            alerts += [
                Alert.from_json(item, scope, kind) for item in payload[key][scope_key]
            ]

    atocha = alerts_for_station(alerts, stations["18000"])
    assert [(item.scope, item.kind, item.target) for item in atocha] == [
        (ALERT_SCOPE_STATION, ALERT_WARNING, "18000"),
        (ALERT_SCOPE_LINE, ALERT_WARNING, "C7"),
        (ALERT_SCOPE_LINE, ALERT_INFORMATION, "C3"),
    ]

    # Orduña is on the C3 of Bilbao, so the C3 of Madrid must not reach it.
    orduna = alerts_for_station(alerts, stations["13100"])
    assert [item.target for item in orduna] == ["C3"]
    assert orduna[0].nucleus == "60"

    # Barcelona-Sants has no alert in the fixture at all.
    assert alerts_for_station(alerts, stations["71801"]) == ()


async def test_client_reads_the_catalogue_once(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """The 400 KB catalogue is cached for the lifetime of the client."""
    client = _client(hass)

    stations = await client.async_get_stations()
    assert len(stations) == 6
    assert mock_renfe.call_count == 1

    await client.async_get_stations()
    assert mock_renfe.call_count == 1

    await client.async_get_stations(force=True)
    assert mock_renfe.call_count == 2


async def test_client_expires_the_catalogue(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """A long lived Home Assistant still picks up a new station eventually."""
    client = _client(hass, catalogue_ttl=timedelta(seconds=-1))

    await client.async_get_stations()
    await client.async_get_stations()

    assert mock_renfe.call_count == 2


async def test_client_get_station(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """A known code resolves, an unknown one raises."""
    client = _client(hass)

    station = await client.async_get_station(18000)
    assert station.name == "Atocha Cercanías"

    with pytest.raises(RenfeStationNotFoundError):
        await client.async_get_station("99999")


async def test_client_search_ignores_case_and_accents(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """The catalogue has no search endpoint, so matching is done locally."""
    client = _client(hass)

    assert [item.code for item in await client.async_search_stations("atocha")] == [
        "18000"
    ]
    assert [item.code for item in await client.async_search_stations("ORDUNA")] == [
        "13100"
    ]
    # Every word has to match, in any order and any field.
    assert [
        item.code for item in await client.async_search_stations("madrid chamartin")
    ] == ["17000"]
    assert await client.async_search_stations("nowhere") == []

    # An empty query inside a network lists it whole, shortest name first.
    madrid = await client.async_search_stations("", nucleus="10")
    assert [item.code for item in madrid] == ["60200", "18000", "17000"]

    # The line code is searchable too.
    assert [item.code for item in await client.async_search_stations("R2")] == ["71801"]


async def test_client_get_departures(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """The departure board is parsed into a sorted, grouped board."""
    client = _client(hass)

    board = await client.async_get_departures(STATION_CODE)

    assert board.in_service is True
    assert len(board.departures) == 5
    assert board.departures[0].line == "C7"


async def test_client_treats_404_as_no_service(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Renfe stops publishing a board when nothing is due, which is not an error."""
    aioclient_mock.get(departures_url("60200"), status=404)
    client = _client(hass)

    board = await client.async_get_departures("60200")

    assert board.in_service is False
    assert board.departures == ()
    assert board.updated is None
    assert board.station_code == "60200"


async def test_client_rejects_a_board_without_a_station(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An empty document is a failure, unlike a 404."""
    aioclient_mock.get(departures_url(), json={"fechaActualizacion": "x"})
    client = _client(hass)

    with pytest.raises(RenfeApiError):
        await client.async_get_departures(STATION_CODE)


async def test_client_reports_a_connection_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 5xx from the reverse proxy surfaces as a connection error."""
    aioclient_mock.get(departures_url(), status=502)
    client = _client(hass)

    with pytest.raises(RenfeConnectionError):
        await client.async_get_departures(STATION_CODE)


async def test_client_reports_invalid_json(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An HTML error page served with a 200 is not silently accepted."""
    aioclient_mock.get(url(STATIONS_PATH), text="<html>maintenance</html>")
    client = _client(hass)

    with pytest.raises(RenfeApiError):
        await client.async_get_stations()


async def test_client_rejects_an_empty_catalogue(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A catalogue with no station in it would silently break every entry."""
    aioclient_mock.get(url(STATIONS_PATH), json={"features": []})
    client = _client(hass)

    with pytest.raises(RenfeApiError):
        await client.async_get_stations()


async def test_client_alerts_are_cached_and_flattened(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """One global document serves every station, so it is fetched once."""
    client = _client(hass)

    alerts = await client.async_get_alerts()
    assert len(alerts) == 5
    assert {alert.kind for alert in alerts} == {ALERT_WARNING, ALERT_INFORMATION}
    assert mock_renfe.call_count == 1

    await client.async_get_alerts()
    assert mock_renfe.call_count == 1

    await client.async_get_alerts(force=True)
    assert mock_renfe.call_count == 2


async def test_client_alerts_survive_a_reshaped_document(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A missing section must not raise, it means "no alerts of that kind"."""
    aioclient_mock.get(url(ALERTS_PATH), json={"avisos": {"linea": "unexpected"}})
    client = _client(hass)

    assert await client.async_get_alerts() == ()


async def test_client_get_fleet(
    hass: HomeAssistant, mock_renfe: AiohttpClientMocker
) -> None:
    """The fleet document is parsed into trains indexed by trip."""
    client = _client(hass)

    fleet = await client.async_get_fleet()

    assert len(fleet.trains) == 4
    assert "1015X20061C3" in fleet.by_trip_id()
