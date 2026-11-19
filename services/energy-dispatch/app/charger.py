"""Adaptador hacia el hardware de carga.

Hoy el hardware se controla a través del sistema heredado (legacy-csms, OCPP 1.6). Este adaptador
aísla al dominio de ese detalle (puerto/adaptador) y se protege con un Circuit Breaker: si el
legado falla repetidamente, el circuito se ABRE y la SAGA compensa de inmediato (fail-fast) en vez
de esperar timeouts en cascada.
"""

from __future__ import annotations

from typing import Protocol

import httpx


class ChargerRejected(RuntimeError):
    pass


class ChargerGateway(Protocol):
    async def remote_start(self, *, station_id: str, connector_id: str, power_kw: float, idempotency_key: str) -> dict: ...
    async def remote_stop(self, *, station_id: str, connector_id: str, idempotency_key: str) -> dict: ...


class LegacyCsmsGateway:
    def __init__(self, base_url: str, timeout_s: float = 2.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def remote_start(self, *, station_id: str, connector_id: str, power_kw: float, idempotency_key: str) -> dict:
        resp = await self._client.post("/ocpp/remote-start", json={
            "chargePointId": station_id, "connectorId": connector_id, "maxPowerKw": power_kw,
            "idTag": idempotency_key[:20]}, headers={"Idempotency-Key": idempotency_key})
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "Accepted":
            raise ChargerRejected(body.get("status", "Rejected"))
        return body

    async def remote_stop(self, *, station_id: str, connector_id: str, idempotency_key: str) -> dict:
        resp = await self._client.post("/ocpp/remote-stop", json={"chargePointId": station_id,
                                                                   "connectorId": connector_id},
                                       headers={"Idempotency-Key": idempotency_key})
        resp.raise_for_status()
        return resp.json()


class FakeCharger:
    """Para tests: permite inducir fallos."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.started: list[str] = []
        self.stopped: list[str] = []
        self._tx = 1000

    async def remote_start(self, *, station_id: str, connector_id: str, power_kw: float, idempotency_key: str) -> dict:
        if self.fail:
            raise httpx.ConnectError("charger offline")
        self._tx += 1
        self.started.append(idempotency_key)
        return {"status": "Accepted", "transactionId": self._tx}

    async def remote_stop(self, *, station_id: str, connector_id: str, idempotency_key: str) -> dict:
        self.stopped.append(idempotency_key)
        return {"status": "Accepted"}
