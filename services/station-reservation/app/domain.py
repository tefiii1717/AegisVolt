"""Bounded context: Station Reservation. Modelo y reglas propias del contexto."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ConnectorStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    CHARGING = "CHARGING"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


class ReservationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CHARGING = "CHARGING"
    RELEASED = "RELEASED"
    # Lápida: llegó la compensación antes que la reserva (mensaje tardío). Bloquea la reserva futura.
    RELEASED_EMPTY = "RELEASED_EMPTY"
    FINISHED = "FINISHED"


class Tier(str, Enum):
    FAST = "FAST"     # <= 120 kW
    ULTRA = "ULTRA"   # >= 150 kW, requiere ENABLE_FAST_CHARGE_BOOST


class ReserveConnector(BaseModel):
    model_config = ConfigDict(extra="ignore")
    saga_id: str
    station_id: str = Field(min_length=1, max_length=64)
    connector_id: str = Field(min_length=1, max_length=64)
    fleet_id: str = Field(min_length=1, max_length=64)
    fast_charge: bool = False


class ReleaseConnector(BaseModel):
    model_config = ConfigDict(extra="ignore")
    saga_id: str
    reason: str = "COMPENSATION"


def connector_key(station_id: str, connector_id: str) -> dict[str, str]:
    return {"pk": f"STATION#{station_id}", "sk": f"CONNECTOR#{connector_id}"}


def reservation_key(saga_id: str) -> dict[str, str]:
    return {"pk": f"RESERVATION#{saga_id}", "sk": "RESERVATION"}


FAILURE_REASONS = {
    "connector": "CONNECTOR_NOT_AVAILABLE",
    "reservation": "SAGA_ALREADY_RELEASED",
}
