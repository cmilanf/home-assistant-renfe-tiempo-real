#!/usr/bin/env python3
"""Smoke check the Renfe client against the live API.

The unit tests run against recorded fixtures with the network blocked, so this
script is the contract check: run it to confirm Renfe has not changed the shape
of its documents.

    .venv/bin/python scripts/live_check.py [station_code ...]
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import aiohttp

# Load api.py straight from its path instead of importing the package. The
# package __init__ pulls in Home Assistant, and this check is meant to run with
# nothing but aiohttp, so CI can verify the Renfe contract cheaply. It also
# keeps api.py honest: the client must not depend on Home Assistant.
_API_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "renfe_tiempo_real"
    / "api.py"
)
_spec = importlib.util.spec_from_file_location("renfe_api", _API_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - defensive
    raise SystemExit(f"Cannot load the Renfe client from {_API_PATH}")
_api = importlib.util.module_from_spec(_spec)
sys.modules["renfe_api"] = _api
_spec.loader.exec_module(_api)

RenfeClient = _api.RenfeClient
RENFE_TIMEZONE = _api.RENFE_TIMEZONE
alerts_for_station = _api.alerts_for_station

# One busy interchange per style of network, plus a quiet halt that often has
# no board published at all.
DEFAULT_STATIONS = ["18000", "71801", "65000", "13100", "5209"]


async def check_catalogue(client: RenfeClient) -> dict[str, object]:
    """Print a summary of the station catalogue and return it."""
    stations = await client.async_get_stations()
    assert len(stations) > 500, f"only {len(stations)} stations in the catalogue"

    nuclei: dict[str, int] = {}
    for station in stations.values():
        assert station.code, "station without a code"
        assert station.name, f"station {station.code} without a name"
        nuclei[station.nucleus_name or station.nucleus] = (
            nuclei.get(station.nucleus_name or station.nucleus, 0) + 1
        )

    print(f"catalogue: {len(stations)} stations in {len(nuclei)} networks")
    for name, count in sorted(nuclei.items()):
        print(f"  {count:4}  {name}")

    unknown = {
        (station.nucleus, line)
        for station in stations.values()
        for line in station.lines
        if _api.line_colour(station.nucleus, line) == _api.FALLBACK_LINE_COLOUR
    }
    if unknown:
        # Not fatal: the card falls back to the corporate red. Worth knowing,
        # because it means the embedded colour table has drifted.
        print(f"  no colour for {len(unknown)} line/network pairs: {sorted(unknown)}")
    return stations


async def check_station(client: RenfeClient, station_code: str) -> None:
    """Print the metadata and live departures of one station."""
    station = await client.async_get_station(station_code)
    assert station.code == station_code, f"{station.code} != {station_code}"

    board = await client.async_get_departures(station_code)

    lines = ", ".join(station.lines) or "-"
    print(f"\n{station.code}  {station.name}")
    print(f"  network={station.nucleus_name}  lines=[{lines}]")
    if station.coordinates:
        print(
            f"  coords={station.coordinates.latitude:.5f},"
            f"{station.coordinates.longitude:.5f}"
        )

    if not board.in_service:
        print("  no departure board published right now")
        return

    assert board.updated is not None, "no fechaActualizacion in the board"
    assert board.station_code == station_code, "the board is for another station"
    now = datetime.now(tz=RENFE_TIMEZONE)
    age = board.age(now)
    print(f"  server time: {board.updated.isoformat()} (age {age})")
    if board.is_stale(now):
        print("  WARNING: the board is stale, Renfe has stopped regenerating it")
    print(f"  routes: {list(board.group_keys)}")
    if board.terminating:
        # Arrivals at the last stop of a trip, kept out of the departures.
        print(f"  terminating here: {len(board.terminating)}")
        for departure in board.terminating:
            assert departure.destination_code == station_code, "not a terminus"
            assert departure.terminates_here, "terminating flag not set"
            arrival = departure.departure
            when = arrival.strftime("%H:%M") if arrival else "--:--"
            print(f"  {departure.line:>5}  arrives {when}  → ends its run here")

    for departure in board.departures[:8]:
        minutes = departure.minutes_from(board.updated)
        assert departure.departure is not None, "departure without a time"
        assert minutes is not None and minutes >= 0, "negative wait"
        delay = "" if departure.delay is None else f"{departure.delay:+d}m"
        print(
            f"  {departure.line:>5}  {minutes:6.1f} min  "
            f"{departure.departure.strftime('%H:%M')} {delay:>5}  "
            f"via {departure.platform or '-':>2}  "
            f"{'♿' if departure.accessible else '  '} "
            f"{departure.status or 'not started':<12} → {departure.destination}"
        )


async def check_alerts(client: RenfeClient, stations: dict[str, object]) -> None:
    """Print the published alerts and how they map onto a station."""
    alerts = await client.async_get_alerts()
    print(f"\nalerts: {len(alerts)} published")
    for alert in alerts[:5]:
        assert alert.target, "alert without a target"
        assert alert.texts, f"alert {alert.scope}/{alert.target} without any text"
        text = alert.text("es").replace("\n", " ")[:70]
        print(f"  {alert.kind:<11} {alert.scope:<7} {alert.target:<6} {text}")

    atocha = stations.get("18000")
    if atocha is not None:
        selected = alerts_for_station(alerts, atocha)
        print(f"  affecting Atocha Cercanías: {len(selected)}")


async def check_fleet(client: RenfeClient) -> None:
    """Print a summary of the trains currently in service."""
    fleet = await client.async_get_fleet()
    print(f"\nfleet: {len(fleet.trains)} trains at {fleet.updated}")
    statuses: dict[str, int] = {}
    for train in fleet.trains:
        assert train.trip_id, "train without a tripId"
        statuses[train.status or "unknown"] = (
            statuses.get(train.status or "unknown", 0) + 1
        )
    for status, count in sorted(statuses.items()):
        print(f"  {count:4}  {status}")


async def main() -> int:
    """Run the smoke checks."""
    codes = sys.argv[1:] or DEFAULT_STATIONS
    async with aiohttp.ClientSession() as session:
        client = RenfeClient(session)
        try:
            stations = await check_catalogue(client)
            for station_code in codes:
                await check_station(client, station_code)
            await check_alerts(client, stations)
            await check_fleet(client)
        except AssertionError as err:
            print(f"\nFAILED: {err}")
            return 1
        except _api.RenfeError as err:
            print(f"\nFAILED: {type(err).__name__}: {err}")
            return 1
    print("\nAll live checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
