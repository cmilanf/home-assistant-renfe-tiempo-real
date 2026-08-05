"""Data update coordinator for the Renfe Tiempo Real integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    Alert,
    RenfeClient,
    RenfeError,
    Station,
    StationBoard,
    alerts_for_station,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

type RenfeConfigEntry = ConfigEntry[RenfeCoordinator]


@dataclass(frozen=True, slots=True)
class RenfeData:
    """Everything one poll produces for a station."""

    board: StationBoard
    alerts: tuple[Alert, ...] = field(default_factory=tuple)


class RenfeCoordinator(DataUpdateCoordinator[RenfeData]):
    """Poll the Renfe real time endpoints for a single station."""

    config_entry: RenfeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: RenfeConfigEntry,
        client: RenfeClient,
        station_code: str,
        scan_interval: int,
        *,
        with_alerts: bool = True,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {station_code}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.station_code = station_code
        self.with_alerts = with_alerts
        self.station: Station | None = None
        self.last_poll: datetime | None = None

    async def async_load_station(self) -> Station:
        """Fetch and cache the static metadata of the station."""
        self.station = await self.client.async_get_station(self.station_code)
        return self.station

    async def _async_update_data(self) -> RenfeData:
        """Fetch the current departure board and the active alerts."""
        try:
            board = await self.client.async_get_departures(self.station_code)
        except RenfeError as err:
            raise UpdateFailed(str(err)) from err

        alerts = await self._async_alerts()
        # Only advanced on success, so it always means "data this old".
        self.last_poll = dt_util.utcnow()
        return RenfeData(board=board, alerts=alerts)

    async def _async_alerts(self) -> tuple[Alert, ...]:
        """Return the alerts of this station, or the previous ones on failure.

        Departures are the point of the integration, so an alert document that
        is briefly unavailable must not mark every sensor unavailable with it.
        """
        if not self.with_alerts or self.station is None:
            return ()
        try:
            alerts = await self.client.async_get_alerts()
        except RenfeError as err:
            _LOGGER.debug("Could not fetch Renfe alerts: %s", err)
            return self.data.alerts if self.data else ()
        return alerts_for_station(alerts, self.station)
