"""Diagnostics support for the Renfe Tiempo Real integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import RenfeConfigEntry

# The station itself is public data, but it points at the user's home or
# commute, and every departure carries the live position of its train.
TO_REDACT = {"coordinates"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RenfeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    station = asdict(coordinator.station) if coordinator.station else None
    board = asdict(coordinator.data.board) if coordinator.data else None
    if board is not None:
        for section in ("departures", "terminating"):
            board[section] = [
                async_redact_data(departure, TO_REDACT) for departure in board[section]
            ]
    return {
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "station": async_redact_data(station, TO_REDACT) if station else None,
        "last_update_success": coordinator.last_update_success,
        "board": board,
        "alerts": [asdict(alert) for alert in coordinator.data.alerts]
        if coordinator.data
        else None,
    }
