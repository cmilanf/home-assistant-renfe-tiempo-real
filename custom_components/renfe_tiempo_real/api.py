"""Async client for the public Renfe Cercanías real time API.

Renfe publishes the data that backs https://tiempo-real.renfe.com through a set
of unauthenticated static JSON documents served by its own reverse proxy.

Relevant endpoints, all relative to ``https://tiempo-real.renfe.com/``:

* ``data/estaciones.geojson`` -- the catalogue of every Cercanías station, with
  its nucleus, coordinates, lines and facilities. Static, cached in memory.
* ``renfe-json-cutter/write/salidas/estacion/<code>.json`` -- the departure
  board of one station, a rolling window of roughly the next two hours.
* ``renfe-visor/flota.json`` -- the live position and delay of every train in
  service.
* ``renfe-visor/alerts.json`` -- service warnings and notices, per station and
  per line, in several languages.

Quirks of the service that this module normalises:

* The departure board answers ``404`` for a station with no service right now,
  which is a normal night time state and not an error.
* Timestamps come in two shapes: ``dd-mm-yyyy HH:MM:SS`` on the departure board
  and naive ISO-8601 in the fleet document. Neither carries an offset, and both
  are local time in Spain, so they are read in ``Europe/Madrid`` and returned
  offset aware.
* ``accesible`` is ``1``/``2`` on the departure board and a boolean in the
  fleet document.
* The site's own JavaScript points the departure board and the alerts at
  ``grt-nginx-visor-publico.desa.sir.renfe.es``, a host that does not resolve
  from the public internet. The paths below go through the ``tiempo-real``
  reverse proxy, which is what actually works.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://tiempo-real.renfe.com/"
DEFAULT_TIMEOUT = 30

STATIONS_PATH = "data/estaciones.geojson"
DEPARTURES_PATH = "renfe-json-cutter/write/salidas/estacion/{code}.json"
FLEET_PATH = "renfe-visor/flota.json"
ALERTS_PATH = "renfe-visor/alerts.json"

# The station catalogue is a 400 KB static document that changes a couple of
# times a year, so one download per Home Assistant day is plenty.
DEFAULT_CATALOGUE_TTL = timedelta(hours=12)

# The alert document is global. Every configured station would otherwise
# download the same copy on every poll, so it is cached for slightly less than
# the shortest polling interval allowed by the integration.
DEFAULT_ALERTS_TTL = timedelta(seconds=25)

# The board is regenerated once a minute. The site refuses to render anything
# older than this, and so does the integration when it reports staleness.
STALE_AFTER = timedelta(minutes=5)

try:
    RENFE_TIMEZONE: timezone | ZoneInfo = ZoneInfo("Europe/Madrid")
except ZoneInfoNotFoundError:  # pragma: no cover - depends on the host tzdata
    RENFE_TIMEZONE = timezone(timedelta(hours=1))

BOARD_TIME_FORMAT = "%d-%m-%Y %H:%M:%S"

# Nucleus (commuter network) codes as used by NUCLEO and idnegocio.
NUCLEI: dict[str, str] = {
    "10": "Madrid",
    "20": "Asturias",
    "30": "Sevilla",
    "31": "Cádiz",
    "32": "Málaga",
    "40": "Valencia",
    "41": "Murcia/Alicante",
    "45": "Cartagena",
    "46": "Ferrol",
    "47": "León",
    "50": "Rodalies de Catalunya",
    "60": "Bilbao",
    "61": "San Sebastián",
    "62": "Cantabria",
    "70": "Zaragoza",
}

# Official line colours, transcribed from renfe-visor/lineas.geojson. That
# document is 1.5 MB of track geometry for 73 colour values, so it is not worth
# downloading at runtime.
LINE_COLOURS: dict[str, dict[str, str]] = {
    "10": {
        "C1": "#75B2E0",
        "C2": "#00943D",
        "C3": "#952585",
        "C4": "#2C2A86",
        "C4a": "#2C2A86",
        "C4b": "#2C2A86",
        "C5": "#FECB00",
        "C7": "#E5202A",
        "C8": "#868584",
        "C8a": "#868584",
        "C8b": "#868584",
        "C9": "#936037",
        "C10": "#BCCF00",
    },
    "20": {
        "C1": "#EF3340",
        "C2": "#009645",
        "C3": "#0060C4",
        "C4": "#E93CAC",
        "C5": "#C4D600",
        "C5a": "#C4D600",
        "C6": "#579BE2",
        "C7": "#ED7D31",
        "C8": "#FFB10F",
    },
    "30": {
        "C1": "#78B4E1",
        "C2": "#0F8C2D",
        "C3": "#D7001E",
        "C4": "#7D2582",
        "C5": "#0F3287",
    },
    "31": {"C1": "#D7001E", "C1a": "#D7001E", "T1": "#C4D600"},
    "32": {"C1": "#4A8CCC", "C2": "#0FCF34"},
    "40": {
        "C1": "#78B4E1",
        "C2": "#F59628",
        "C3": "#7D2582",
        "C5": "#0F8C2D",
        "C6": "#0F3287",
        "ER02": "#F56600",
    },
    "41": {"C1": "#78B4E1", "C2": "#0F8C2D", "C3": "#7D2582"},
    "45": {"C1": "#D7001E"},
    "46": {"C1": "#D7001E"},
    "47": {"C1": "#D7001E"},
    "50": {
        "R1": "#7DBCEC",
        "R2": "#26A741",
        "R2N": "#D0DE00",
        "R2S": "#136524",
        "R3": "#EA412B",
        "R4": "#F6A30D",
        "R7": "#B57BBA",
        "R8": "#8800C4",
        "R11": "#0069AA",
        "R13": "#EE4999",
        "R14": "#6C60A8",
        "R15": "#978571",
        "R16": "#B52B46",
        "R17": "#EF7100",
        "RG1": "#0071CE",
        "RL3": "#949300",
        "RL4": "#FFDD00",
        "RT1": "#00C4B3",
        "RT2": "#E777CB",
    },
    "60": {
        "C1": "#D7001E",
        "C2": "#0F8C2D",
        "C3": "#78B4E1",
        "C4": "#E93CAC",
        "C5": "#2C2A86",
    },
    "61": {"C1": "#D7001E"},
    "62": {"C1": "#EF3340", "C2": "#009645", "C3": "#0060A9"},
    "70": {"C1": "#D7001E"},
}

FALLBACK_LINE_COLOUR = "#E6001E"

# `position` on the departure board and `porAvanc` in the fleet document share
# this vocabulary. Anything else in `porAvanc` is a percentage of progress
# between the current and the next station, which also means "running".
TRAIN_STATUS: dict[str, str] = {
    "E": "at_station",
    "A": "approaching",
    "S": "en_route",
}

ALERT_WARNING = "warning"
ALERT_INFORMATION = "information"

ALERT_SCOPE_STATION = "station"
ALERT_SCOPE_LINE = "line"


class RenfeError(Exception):
    """Base error for the Renfe client."""


class RenfeConnectionError(RenfeError):
    """Raised when the Renfe service cannot be reached."""


class RenfeApiError(RenfeError):
    """Raised when the Renfe service answers with something unusable."""


class RenfeStationNotFoundError(RenfeError):
    """Raised when the requested station is not in the catalogue."""


class RenfeNotFound(RenfeError):
    """Raised internally when a document does not exist.

    Public methods translate this into a meaningful result, so it never leaves
    the client.
    """


@contextmanager
def _parse_errors_as_api_error(endpoint: str) -> Iterator[None]:
    """Turn unexpected payload shapes into a RenfeApiError.

    The endpoints are undocumented, so a structural surprise must not escape
    the client as an ``AttributeError`` or ``TypeError``.
    """
    try:
        yield
    except (AttributeError, TypeError, KeyError, ValueError) as err:
        raise RenfeApiError(f"Malformed payload returned by {endpoint}: {err}") from err


def as_mapping(value: Any) -> dict[str, Any]:
    """Return ``value`` when it is a mapping, an empty mapping otherwise."""
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    """Return ``value`` when it is a list, an empty list otherwise."""
    return value if isinstance(value, list) else []


def as_text(value: Any) -> str:
    """Render a scalar as text, mapping ``None`` to the empty string.

    Station and line codes arrive as integers in the GeoJSON catalogue and as
    strings everywhere else, so everything is normalised to text.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def parse_board_datetime(value: Any) -> datetime | None:
    """Parse a ``dd-mm-yyyy HH:MM:SS`` timestamp from the departure board.

    The board omits the UTC offset, and every value is local time in Spain, so
    the result is made offset aware in the timezone of the Renfe backend.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.strptime(value.strip(), BOARD_TIME_FORMAT)
    except ValueError:
        _LOGGER.debug("Unparseable Renfe board timestamp: %s", value)
        return None
    return parsed.replace(tzinfo=RENFE_TIMEZONE)


def parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp as returned by the fleet document.

    The fleet document has no offset either, so a naive value is read in the
    timezone of the Renfe backend.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        _LOGGER.debug("Unparseable Renfe timestamp: %s", value)
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=RENFE_TIMEZONE)
    return parsed


def line_colour(nucleus: str, line: str) -> str:
    """Return the official colour of a line.

    The nucleus is tried first. The departure board occasionally reports a
    nucleus that is absent from the colour table (``51`` for the long distance
    platforms of Barcelona-Sants, for instance), so a line code that is unique
    across the whole network is resolved regardless of the nucleus. An ambiguous
    code such as ``C1``, which exists in eleven nuclei, falls back to the Renfe
    corporate red rather than to a colour from the wrong city.
    """
    if not line:
        return FALLBACK_LINE_COLOUR
    if colour := LINE_COLOURS.get(as_text(nucleus), {}).get(line):
        return colour
    matches = {colours[line] for colours in LINE_COLOURS.values() if line in colours}
    if len(matches) == 1:
        return matches.pop()
    return FALLBACK_LINE_COLOUR


def _split_list(value: Any) -> tuple[str, ...]:
    """Split a comma or pipe separated catalogue field into its items."""
    if not isinstance(value, str) or not value.strip():
        return ()
    separator = "|" if "|" in value else ","
    return tuple(item.strip() for item in value.split(separator) if item.strip())


def _sort_line_key(line: str) -> tuple[str, int, str]:
    """Sort ``C2`` before ``C10``, which a plain string sort gets wrong."""
    prefix = line.rstrip("0123456789abcdefghijklmnopqrstuvwxyz")
    rest = line[len(prefix) :]
    digits = rest.rstrip("abcdefghijklmnopqrstuvwxyz")
    return (prefix, int(digits) if digits.isdigit() else 0, line)


@dataclass(frozen=True, slots=True)
class Coordinates:
    """WGS84 coordinates of a station or a train."""

    latitude: float
    longitude: float

    @property
    def is_valid(self) -> bool:
        """Return True when the API reported usable coordinates."""
        return self.latitude != 0 or self.longitude != 0

    @classmethod
    def from_json(cls, latitude: Any, longitude: Any) -> Coordinates | None:
        """Build coordinates from two raw values, or None when unusable."""
        try:
            return cls(latitude=float(latitude), longitude=float(longitude))
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class Station:
    """A Cercanías station, as described by the GeoJSON catalogue."""

    code: str
    name: str
    nucleus: str = ""
    nucleus_name: str = ""
    coordinates: Coordinates | None = None
    lines: tuple[str, ...] = ()
    accessible: bool = False
    bike_parking: bool = False
    metro_connections: tuple[str, ...] = ()
    bus_connections: tuple[str, ...] = ()

    @classmethod
    def from_feature(cls, feature: dict[str, Any]) -> Station:
        """Build a station from one feature of ``estaciones.geojson``."""
        properties = as_mapping(feature.get("properties"))
        accessibility = properties.get("ACCESIBILIDAD")
        return cls(
            code=as_text(properties.get("CODIGO_ESTACION")),
            name=as_text(properties.get("NOMBRE_ESTACION")),
            nucleus=as_text(properties.get("NUCLEO")),
            nucleus_name=as_text(properties.get("NOMBRE_NUCLEO")),
            coordinates=Coordinates.from_json(
                properties.get("LATITUD"), properties.get("LONGITUD")
            ),
            lines=tuple(
                sorted(_split_list(properties.get("LINEAS")), key=_sort_line_key)
            ),
            accessible=bool(accessibility),
            bike_parking=bool(properties.get("PARKING_BICIS")),
            metro_connections=_split_list(properties.get("COR_METRO")),
            bus_connections=_split_list(properties.get("COR_BUS")),
        )

    @property
    def line_colours(self) -> dict[str, str]:
        """Return the colour of every line serving this station."""
        return {line: line_colour(self.nucleus, line) for line in self.lines}


@dataclass(frozen=True, slots=True)
class Departure:
    """A single train leaving a station."""

    train: str
    trip_id: str
    route_id: str
    line: str
    destination_code: str
    destination: str
    departure: datetime | None
    scheduled: datetime | None
    platform: str = ""
    accessible: bool = False
    status: str = ""
    located_at: str = ""
    coordinates: Coordinates | None = None
    terminates_here: bool = False

    @classmethod
    def from_json(cls, data: dict[str, Any], station_code: str = "") -> Departure:
        """Build a departure from one element of ``estacion.salidas``.

        ``station_code`` is the station whose board this is. When the trip ends
        there, the record is the arrival at its last stop rather than a
        departure, which ``terminates_here`` records.
        """
        location = as_mapping(data.get("localizacion"))
        destination_code = as_text(data.get("destino"))
        # `horaSalida` is the time the board shows; `horaSalidaReal` repeats it
        # once the train is running and is absent before that.
        return cls(
            train=as_text(data.get("trenId")),
            trip_id=as_text(data.get("tripId")),
            route_id=as_text(data.get("routeId")),
            line=as_text(data.get("linea")),
            destination_code=destination_code,
            destination=as_text(data.get("destinoNombre")),
            departure=parse_board_datetime(data.get("horaSalida")),
            scheduled=parse_board_datetime(data.get("horaSalidaPlanificada")),
            platform=as_text(data.get("via")),
            # 1 means accessible, 2 means not. Anything else is unknown, which
            # is reported as not accessible rather than guessed.
            accessible=as_text(data.get("accesible")) == "1",
            status=TRAIN_STATUS.get(as_text(data.get("position")), ""),
            located_at=as_text(data.get("locEstacion")),
            coordinates=Coordinates.from_json(
                location.get("latitud"), location.get("longitud")
            ),
            terminates_here=bool(
                destination_code
                and station_code
                and destination_code == as_text(station_code)
            ),
        )

    @property
    def group_key(self) -> str:
        """Return the identifier of the line and destination of this train.

        A station serves both directions of a line from a single code, so the
        destination is what separates one journey from the other.
        """
        return f"{self.line}_{self.destination_code}"

    @property
    def delay(self) -> int | None:
        """Return the delay in whole minutes, negative when running early."""
        if self.departure is None or self.scheduled is None:
            return None
        return round((self.departure - self.scheduled).total_seconds() / 60)

    @property
    def has_started(self) -> bool:
        """Return True when the train is already running.

        A departure with no status has not left its origin yet, so its time is
        still the timetable rather than an estimate.
        """
        return bool(self.status)

    def minutes_from(self, reference: datetime | None) -> float | None:
        """Return the minutes left until this departure, from ``reference``."""
        if self.departure is None or reference is None:
            return None
        return max(0.0, (self.departure - reference).total_seconds() / 60)


def _departure_sort_key(departure: Departure) -> tuple[bool, float]:
    """Sort departures soonest first, pushing entries without a time to the end."""
    if departure.departure is None:
        return (True, 0.0)
    return (False, departure.departure.timestamp())


@dataclass(frozen=True, slots=True)
class StationBoard:
    """The departure board of one station.

    Renfe serves a per-station slice of a stop-times feed, so every trip calling
    at the station is listed once, with the time at this station and the terminus
    of the trip in ``destino``. When the trip ends here that record is the
    arrival at its last stop and there is no onward departure, so those entries
    are kept apart in ``terminating``.
    """

    station_code: str
    station_name: str
    nucleus: str
    updated: datetime | None
    departures: tuple[Departure, ...] = ()
    terminating: tuple[Departure, ...] = ()
    in_service: bool = True

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> StationBoard:
        """Build a board from a ``salidas/estacion`` payload."""
        station = as_mapping(data.get("estacion"))
        station_code = as_text(station.get("stopId"))
        entries = [
            Departure.from_json(item, station_code)
            for item in as_list(station.get("salidas"))
            if isinstance(item, dict)
        ]
        entries.sort(key=_departure_sort_key)
        return cls(
            station_code=station_code,
            station_name=as_text(station.get("nombre")),
            nucleus=as_text(station.get("nucleo")),
            updated=parse_board_datetime(data.get("fechaActualizacion")),
            departures=tuple(item for item in entries if not item.terminates_here),
            terminating=tuple(item for item in entries if item.terminates_here),
        )

    @classmethod
    def out_of_service(cls, station_code: str) -> StationBoard:
        """Return the empty board served for a station with no trains due.

        Renfe answers ``404`` outside service hours, which is a state rather
        than a failure: the sensors report no departure instead of going
        unavailable.
        """
        return cls(
            station_code=station_code,
            station_name="",
            nucleus="",
            updated=None,
            departures=(),
            terminating=(),
            in_service=False,
        )

    def departures_for(self, group_key: str) -> tuple[Departure, ...]:
        """Return the departures of one line and destination, soonest first."""
        return tuple(item for item in self.departures if item.group_key == group_key)

    @property
    def group_keys(self) -> tuple[str, ...]:
        """Return every line and destination present in this board."""
        seen: dict[str, None] = {}
        for departure in self.departures:
            seen.setdefault(departure.group_key, None)
        return tuple(seen)

    @property
    def lines(self) -> tuple[str, ...]:
        """Return the lines with a train due, in board order."""
        seen: dict[str, None] = {}
        for departure in self.departures:
            if departure.line:
                seen.setdefault(departure.line, None)
        return tuple(seen)

    def age(self, now: datetime) -> timedelta | None:
        """Return how old the board is, or None when it carries no timestamp."""
        if self.updated is None:
            return None
        return now - self.updated

    def is_stale(self, now: datetime) -> bool:
        """Return True when Renfe stopped regenerating the board."""
        age = self.age(now)
        return age is not None and age > STALE_AFTER


@dataclass(frozen=True, slots=True)
class Train:
    """A train in service, as reported by the fleet document."""

    trip_id: str
    train: str
    line: str
    nucleus: str
    delay: int | None
    origin_code: str
    destination_code: str
    current_station_code: str
    next_station_code: str
    next_arrival: datetime | None
    status: str = ""
    progress: float | None = None
    platform: str = ""
    next_platform: str = ""
    accessible: bool = False
    coordinates: Coordinates | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Train:
        """Build a train from one element of ``flota.json``."""
        raw_status = as_text(data.get("porAvanc"))
        progress: float | None = None
        if raw_status not in TRAIN_STATUS:
            try:
                progress = float(raw_status)
            except ValueError:
                progress = None
        try:
            delay: int | None = int(float(as_text(data.get("retrasoMin"))))
        except ValueError:
            delay = None
        return cls(
            trip_id=as_text(data.get("tripId")),
            train=as_text(data.get("codTren")),
            line=as_text(data.get("codLinea")),
            nucleus=as_text(data.get("nucleo")),
            delay=delay,
            origin_code=as_text(data.get("codEstOrig")),
            destination_code=as_text(data.get("codEstDest")),
            current_station_code=as_text(data.get("codEstAct")),
            next_station_code=as_text(data.get("codEstSig")),
            next_arrival=parse_iso_datetime(data.get("horaLlegadaSigEst")),
            # A numeric `porAvanc` is a percentage of the way to the next
            # station, which the site draws as a moving train.
            status=TRAIN_STATUS.get(raw_status, "" if progress is None else "en_route"),
            progress=progress,
            platform=as_text(data.get("via")),
            next_platform=as_text(data.get("nextVia")),
            accessible=bool(data.get("accesible")),
            coordinates=Coordinates.from_json(
                data.get("latitud"), data.get("longitud")
            ),
        )


@dataclass(frozen=True, slots=True)
class Fleet:
    """Every train in service at a point in time."""

    updated: datetime | None
    trains: tuple[Train, ...] = ()

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Fleet:
        """Build the fleet from a ``flota.json`` payload."""
        return cls(
            updated=parse_iso_datetime(data.get("fechaActualizacion")),
            trains=tuple(
                Train.from_json(item)
                for item in as_list(data.get("trenes"))
                if isinstance(item, dict)
            ),
        )

    def by_trip_id(self) -> dict[str, Train]:
        """Index the fleet by trip, which is what the board refers to."""
        return {train.trip_id: train for train in self.trains if train.trip_id}


@dataclass(frozen=True, slots=True)
class Alert:
    """A service warning or notice affecting a station or a line."""

    scope: str
    kind: str
    nucleus: str
    target: str
    texts: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_json(cls, data: dict[str, Any], scope: str, kind: str) -> Alert:
        """Build an alert from one element of ``alerts.json``."""
        texts: dict[str, str] = {}
        for item in as_list(data.get("idiomas")):
            if not isinstance(item, dict):
                continue
            language = as_text(item.get("lengua")).lower()
            text = as_text(item.get("texto")).strip()
            # Renfe repeats a language when one alert carries several
            # paragraphs; keep the first, which is the headline.
            if language and text:
                texts.setdefault(language, text)
        target = (
            data.get("estacion") if scope == ALERT_SCOPE_STATION else data.get("linea")
        )
        return cls(
            scope=scope,
            kind=kind,
            nucleus=as_text(data.get("idnegocio")),
            target=as_text(target),
            texts=tuple(texts.items()),
        )

    def text(self, language: str = "es") -> str:
        """Return the alert in ``language``, falling back to Spanish."""
        texts = dict(self.texts)
        if not texts:
            return ""
        for candidate in (language.lower(), language.lower().split("-")[0], "es"):
            if candidate in texts:
                return texts[candidate]
        return next(iter(texts.values()))

    @property
    def languages(self) -> tuple[str, ...]:
        """Return the languages this alert is published in."""
        return tuple(language for language, _ in self.texts)


def alerts_for_station(alerts: Iterable[Alert], station: Station) -> tuple[Alert, ...]:
    """Return the alerts that concern a station or any line serving it.

    Line alerts are keyed by nucleus and line code, so a line code is only
    matched inside the nucleus of the station. Without that check the ``C1`` of
    Bilbao would raise an alert on the ``C1`` of Madrid.
    """
    selected: list[Alert] = []
    for alert in alerts:
        if alert.scope == ALERT_SCOPE_STATION:
            if alert.target and alert.target == station.code:
                selected.append(alert)
        elif alert.target in station.lines and alert.nucleus == station.nucleus:
            selected.append(alert)
    return tuple(selected)


class RenfeClient:
    """Minimal async client for the Renfe real time endpoints."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        catalogue_ttl: timedelta = DEFAULT_CATALOGUE_TTL,
        alerts_ttl: timedelta = DEFAULT_ALERTS_TTL,
    ) -> None:
        """Initialise the client with an externally owned aiohttp session."""
        self._session = session
        self._base_url = URL(base_url if base_url.endswith("/") else f"{base_url}/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._catalogue_ttl = catalogue_ttl
        self._alerts_ttl = alerts_ttl
        self._stations: dict[str, Station] = {}
        self._stations_fetched: datetime | None = None
        self._stations_lock = asyncio.Lock()
        self._alerts: tuple[Alert, ...] = ()
        self._alerts_fetched: datetime | None = None
        self._alerts_lock = asyncio.Lock()

    def _is_fresh(self, fetched: datetime | None, ttl: timedelta) -> bool:
        """Return True when a cached document is still within its lifetime."""
        if fetched is None:
            return False
        return datetime.now(tz=RENFE_TIMEZONE) - fetched < ttl

    async def _get(self, path: str) -> Any:
        """Perform a GET request and return the decoded JSON payload."""
        url = self._base_url.join(URL(path))
        try:
            response = await self._session.get(url, timeout=self._timeout)
            if response.status == 404:
                raise RenfeNotFound(path)
            response.raise_for_status()
            # The documents are served as `application/octet-stream`.
            payload = await response.json(content_type=None)
        except TimeoutError as err:
            raise RenfeConnectionError(f"Timeout calling {path}") from err
        except aiohttp.ClientError as err:
            raise RenfeConnectionError(f"Error calling {path}: {err}") from err
        except ValueError as err:
            raise RenfeApiError(f"Invalid JSON returned by {path}") from err
        return payload

    async def async_get_stations(self, *, force: bool = False) -> dict[str, Station]:
        """Return the station catalogue keyed by station code.

        The result is cached for the lifetime of the client so that adding a
        second station, or reloading an entry, does not re-download 400 KB.
        """
        async with self._stations_lock:
            if (
                not force
                and self._stations
                and self._is_fresh(self._stations_fetched, self._catalogue_ttl)
            ):
                return self._stations

            payload = as_mapping(await self._get(STATIONS_PATH))
            with _parse_errors_as_api_error(STATIONS_PATH):
                stations = {}
                for feature in as_list(payload.get("features")):
                    if not isinstance(feature, dict):
                        continue
                    station = Station.from_feature(feature)
                    if station.code:
                        stations[station.code] = station
            if not stations:
                raise RenfeApiError("The Renfe station catalogue came back empty")
            self._stations = stations
            self._stations_fetched = datetime.now(tz=RENFE_TIMEZONE)
            return self._stations

    async def async_get_station(self, station_code: str) -> Station:
        """Return one station of the catalogue."""
        stations = await self.async_get_stations()
        try:
            return stations[as_text(station_code)]
        except KeyError:
            raise RenfeStationNotFoundError(
                f"Unknown station code: {station_code}"
            ) from None

    async def async_search_stations(
        self, query: str, *, nucleus: str | None = None
    ) -> list[Station]:
        """Search stations by name, nucleus name or code.

        The catalogue has no search endpoint, so the match is done locally:
        accents and case are ignored and every whitespace separated word of the
        query has to appear somewhere in the station.
        """
        stations = await self.async_get_stations()
        candidates = [
            station
            for station in stations.values()
            if nucleus is None or station.nucleus == as_text(nucleus)
        ]
        words = _normalise(query).split()
        if not words:
            return sorted(candidates, key=lambda station: station.name)

        matches = [
            station
            for station in candidates
            if all(word in _searchable(station) for word in words)
        ]
        return sorted(matches, key=lambda station: (len(station.name), station.name))

    async def async_get_departures(self, station_code: str) -> StationBoard:
        """Return the departure board of a station."""
        path = DEPARTURES_PATH.format(code=as_text(station_code))
        try:
            payload = await self._get(path)
        except RenfeNotFound:
            # No board is published for a station with nothing due, which is
            # the normal state at night and at unserved platforms.
            return StationBoard.out_of_service(as_text(station_code))
        board = as_mapping(payload)
        if not as_mapping(board.get("estacion")):
            raise RenfeApiError(
                f"No departure data returned for station {station_code}"
            )
        with _parse_errors_as_api_error(path):
            return StationBoard.from_json(board)

    async def async_get_fleet(self) -> Fleet:
        """Return the live position and delay of every train in service."""
        payload = as_mapping(await self._get(FLEET_PATH))
        with _parse_errors_as_api_error(FLEET_PATH):
            return Fleet.from_json(payload)

    async def async_get_alerts(self, *, force: bool = False) -> tuple[Alert, ...]:
        """Return every published warning and notice.

        One global document covers the whole network, so it is cached briefly
        and shared by every configured station.
        """
        async with self._alerts_lock:
            if not force and self._is_fresh(self._alerts_fetched, self._alerts_ttl):
                return self._alerts

            payload = as_mapping(await self._get(ALERTS_PATH))
            with _parse_errors_as_api_error(ALERTS_PATH):
                alerts: list[Alert] = []
                for key, kind in (
                    ("avisos", ALERT_WARNING),
                    ("informaciones", ALERT_INFORMATION),
                ):
                    section = as_mapping(payload.get(key))
                    for scope_key, scope in (
                        ("estacion", ALERT_SCOPE_STATION),
                        ("linea", ALERT_SCOPE_LINE),
                    ):
                        for item in as_list(section.get(scope_key)):
                            if isinstance(item, dict):
                                alerts.append(Alert.from_json(item, scope, kind))
            self._alerts = tuple(alerts)
            self._alerts_fetched = datetime.now(tz=RENFE_TIMEZONE)
            return self._alerts


_ACCENTS = str.maketrans("áàäâãéèëêíìïîóòöôõúùüûñç", "aaaaaeeeeiiiiooooouuuunc")


def _normalise(value: str) -> str:
    """Lowercase a string and strip the accents, for searching."""
    return value.lower().translate(_ACCENTS)


def _searchable(station: Station) -> str:
    """Return the text of a station that a search query is matched against."""
    return _normalise(
        " ".join(
            (
                station.name,
                station.code,
                station.nucleus_name,
                " ".join(station.lines),
            )
        )
    )
