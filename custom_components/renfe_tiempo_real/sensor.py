"""Sensor platform for the Renfe Tiempo Real integration."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import ALERT_WARNING, Departure, Station, line_colour
from .const import (
    ALERT_ATTRIBUTE_LIMIT,
    ATTRIBUTION,
    CONF_MAX_DEPARTURES,
    DEFAULT_MAX_DEPARTURES,
    DOMAIN,
    MANUFACTURER,
    STATION_ATTRIBUTE_LIMIT,
    WEB_URL,
)
from .coordinator import RenfeConfigEntry, RenfeCoordinator

_LOGGER = logging.getLogger(__name__)

# Suffixes of the unique ids that are not a line and destination pair.
RESERVED_SUFFIXES = ("next_departure", "last_updated", "alerts")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RenfeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Renfe sensors for a station."""
    coordinator = entry.runtime_data
    max_departures = int(entry.options.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES))

    entities: list[SensorEntity] = [
        RenfeNextDepartureSensor(coordinator),
        RenfeUpdatedSensor(coordinator),
        RenfeAlertsSensor(coordinator),
    ]

    # Routes already known from a previous run are restored even if they are not
    # on the current board, so a restart outside service hours does not make
    # their entities disappear.
    known = _restore_known_routes(hass, entry)
    for group_key, label in known.items():
        entities.append(
            RenfeRouteDepartureSensor(coordinator, group_key, max_departures, label)
        )
        entities.append(RenfeRouteDepartureTimeSensor(coordinator, group_key, label))

    @callback
    def _async_add_new_routes() -> None:
        """Create entities for line and destination pairs seen for the first time."""
        if coordinator.data is None:
            return
        new_entities: list[SensorEntity] = []
        for group_key in coordinator.data.board.group_keys:
            if group_key in known:
                continue
            known[group_key] = None
            new_entities.append(
                RenfeRouteDepartureSensor(coordinator, group_key, max_departures)
            )
            new_entities.append(RenfeRouteDepartureTimeSensor(coordinator, group_key))
        if new_entities:
            async_add_entities(new_entities)

    async_add_entities(entities)
    _async_add_new_routes()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_routes))


def _restore_known_routes(
    hass: HomeAssistant, entry: RenfeConfigEntry
) -> dict[str, str | None]:
    """Return the line and destination pairs already in the entity registry.

    The label is recovered from the stored entity name so that a route without
    a train due still shows a meaningful name after a restart.
    """
    station_code = entry.runtime_data.station_code
    prefix = f"{station_code}_"
    reserved = {f"{prefix}{suffix}" for suffix in RESERVED_SUFFIXES}
    known: dict[str, str | None] = {}
    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = registry_entry.unique_id
        if (
            registry_entry.domain != "sensor"
            or unique_id in reserved
            or not unique_id.startswith(prefix)
            or unique_id.endswith("_time")
        ):
            continue
        known[unique_id.removeprefix(prefix)] = registry_entry.original_name
    return known


class RenfeEntity(CoordinatorEntity[RenfeCoordinator], SensorEntity):
    """Base entity sharing the station device and attribution."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: RenfeCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        station = coordinator.station
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.station_code)},
            manufacturer=MANUFACTURER,
            name=self._device_name(station, coordinator.station_code),
            model=station.nucleus_name if station else None,
            configuration_url=WEB_URL,
        )

    @staticmethod
    def _device_name(station: Station | None, station_code: str) -> str:
        """Return the device name for the station."""
        if station and station.name:
            return station.name
        return f"Renfe {station_code}"

    @property
    def _reference(self) -> datetime | None:
        """Return the server timestamp used to compute the remaining minutes."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.board.updated

    def _freshness_attributes(self) -> dict[str, object]:
        """Return the attributes shared by every Renfe sensor.

        ``data_timestamp`` is the clock of the Renfe backend
        (``fechaActualizacion``) and ``last_polled`` is when Home Assistant last
        fetched successfully. They normally differ by less than the minute it
        takes Renfe to regenerate the board; a larger gap means Renfe stopped
        publishing, which is what ``stale`` reports.
        """
        coordinator = self.coordinator
        data = coordinator.data
        station = coordinator.station
        now = dt_util.now()
        return {
            "station_code": coordinator.station_code,
            "station_name": station.name if station else None,
            "nucleus": station.nucleus_name if station else None,
            "data_timestamp": self._reference.isoformat() if self._reference else None,
            "last_polled": (
                dt_util.as_local(coordinator.last_poll).isoformat()
                if coordinator.last_poll
                else None
            ),
            "poll_interval_seconds": (
                int(coordinator.update_interval.total_seconds())
                if coordinator.update_interval
                else None
            ),
            "in_service": data.board.in_service if data else None,
            "stale": data.board.is_stale(now) if data else None,
        }

    def _describe(self, departure: Departure) -> dict[str, object]:
        """Serialise a departure for the entity attributes."""
        minutes = departure.minutes_from(self._reference)
        station = self.coordinator.station
        nucleus = station.nucleus if station else ""
        return {
            "line": departure.line,
            "line_colour": line_colour(nucleus, departure.line),
            "destination": departure.destination,
            "destination_code": departure.destination_code,
            "time": departure.departure.isoformat() if departure.departure else None,
            "scheduled": (
                departure.scheduled.isoformat() if departure.scheduled else None
            ),
            "minutes": None if minutes is None else int(round(minutes)),
            "delay": departure.delay,
            "platform": departure.platform or None,
            "accessible": departure.accessible,
            "train": departure.train,
            "status": departure.status or None,
        }


class RenfeNextDepartureSensor(RenfeEntity):
    """Minutes until the next train of any line leaves the station."""

    _attr_translation_key = "next_departure"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:train"

    def __init__(self, coordinator: RenfeCoordinator) -> None:
        """Initialise the station level sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.station_code}_next_departure"

    @property
    def native_value(self) -> int | None:
        """Return the minutes until the next departure."""
        if not (data := self.coordinator.data) or not data.board.departures:
            return None
        minutes = data.board.departures[0].minutes_from(data.board.updated)
        return None if minutes is None else int(round(minutes))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return every upcoming departure from the station."""
        data = self.coordinator.data
        if data is None:
            return {}
        station = self.coordinator.station
        board = data.board
        return {
            **self._freshness_attributes(),
            "lines": list(station.lines) if station else list(board.lines),
            "departure_count": len(board.departures),
            # Trains ending their run here are arrivals, not departures, so they
            # get no row and no entity. The count keeps them discoverable.
            "terminating_count": len(board.terminating),
            "departures": [
                self._describe(departure)
                for departure in board.departures[:STATION_ATTRIBUTE_LIMIT]
            ],
        }


class RenfeUpdatedSensor(RenfeEntity):
    """Timestamp reported by the Renfe backend for the current board."""

    _attr_translation_key = "last_updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:cloud-clock-outline"

    def __init__(self, coordinator: RenfeCoordinator) -> None:
        """Initialise the freshness sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.station_code}_last_updated"

    @property
    def native_value(self) -> datetime | None:
        """Return the server side timestamp of the current board."""
        return self._reference

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return the polling details."""
        return self._freshness_attributes()


class RenfeAlertsSensor(RenfeEntity):
    """Number of Renfe warnings and notices affecting the station."""

    _attr_translation_key = "alerts"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:alert-outline"

    def __init__(self, coordinator: RenfeCoordinator) -> None:
        """Initialise the alert sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.station_code}_alerts"

    @property
    def native_value(self) -> int | None:
        """Return how many alerts apply to the station and its lines."""
        if (data := self.coordinator.data) is None:
            return None
        if not self.coordinator.with_alerts:
            return None
        return len(data.alerts)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return the alert texts in the language of Home Assistant."""
        data = self.coordinator.data
        if data is None:
            return {}
        language = self.hass.config.language if self.hass else "es"
        warnings = [alert for alert in data.alerts if alert.kind == ALERT_WARNING]
        return {
            **self._freshness_attributes(),
            "warning_count": len(warnings),
            "information_count": len(data.alerts) - len(warnings),
            "alerts": [
                {
                    "scope": alert.scope,
                    "kind": alert.kind,
                    "target": alert.target,
                    "text": alert.text(language),
                }
                for alert in data.alerts[:ALERT_ATTRIBUTE_LIMIT]
            ],
        }


class RenfeRouteEntity(RenfeEntity):
    """Base entity for a line and destination served by the station."""

    def __init__(
        self,
        coordinator: RenfeCoordinator,
        group_key: str,
        label: str | None = None,
    ) -> None:
        """Initialise the route entity.

        ``label`` is the name recovered from the entity registry when the route
        is restored without a train due after a restart.
        """
        super().__init__(coordinator)
        self._group_key = group_key
        self._label = label or self._build_label()

    def _departures(self) -> tuple[Departure, ...]:
        """Return the pending departures of this line and destination."""
        if (data := self.coordinator.data) is None:
            return ()
        return data.board.departures_for(self._group_key)

    def _first(self) -> Departure | None:
        """Return the soonest departure of this line and destination."""
        departures = self._departures()
        return departures[0] if departures else None

    @property
    def _line(self) -> str:
        """Return the line code of this route, board or unique id."""
        if (departure := self._first()) is not None:
            return departure.line
        return self._group_key.rsplit("_", 1)[0]

    def _build_label(self) -> str:
        """Build a stable label such as ``C3 → Aranjuez``."""
        departure = self._first()
        if departure is None:
            return self._group_key
        if departure.destination:
            return f"{departure.line} → {departure.destination}"
        return f"{departure.line} → {departure.destination_code}"


class RenfeRouteDepartureSensor(RenfeRouteEntity):
    """Minutes until the next train of a line and destination leaves."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:train"

    def __init__(
        self,
        coordinator: RenfeCoordinator,
        group_key: str,
        max_departures: int,
        label: str | None = None,
    ) -> None:
        """Initialise the per route sensor."""
        super().__init__(coordinator, group_key, label)
        self._max_departures = max_departures
        self._attr_name = self._label
        self._attr_unique_id = f"{coordinator.station_code}_{group_key}"

    @property
    def native_value(self) -> int | None:
        """Return the minutes until the next train of this route."""
        if (departure := self._first()) is None:
            return None
        minutes = departure.minutes_from(self._reference)
        return None if minutes is None else int(round(minutes))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return the details of the upcoming trains of this route."""
        departures = self._departures()
        first = departures[0] if departures else None
        station = self.coordinator.station
        nucleus = station.nucleus if station else ""
        return {
            **self._freshness_attributes(),
            "line": self._line,
            "line_colour": line_colour(nucleus, self._line),
            "destination": first.destination if first else None,
            "destination_code": (
                first.destination_code if first else self._group_key.rsplit("_", 1)[-1]
            ),
            "departure_time": (
                first.departure.isoformat() if first and first.departure else None
            ),
            "scheduled_time": (
                first.scheduled.isoformat() if first and first.scheduled else None
            ),
            "delay": first.delay if first else None,
            "platform": (first.platform or None) if first else None,
            "accessible": first.accessible if first else None,
            "train": first.train if first else None,
            "status": (first.status or None) if first else None,
            # False before the train leaves its origin, when the board still
            # shows the timetable instead of a live estimate.
            "realtime": first.has_started if first else False,
            "next_departures": [
                self._describe(departure)
                for departure in departures[: self._max_departures]
            ],
        }


class RenfeRouteDepartureTimeSensor(RenfeRouteEntity):
    """Absolute departure time of the next train of a line and destination."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "route_departure_time"
    _attr_icon = "mdi:clock-outline"

    def __init__(
        self,
        coordinator: RenfeCoordinator,
        group_key: str,
        label: str | None = None,
    ) -> None:
        """Initialise the per route timestamp sensor."""
        super().__init__(coordinator, group_key, label)
        self._attr_translation_placeholders = {"label": self._label}
        self._attr_unique_id = f"{coordinator.station_code}_{group_key}_time"

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp of the next train of this route."""
        departure = self._first()
        return departure.departure if departure else None
