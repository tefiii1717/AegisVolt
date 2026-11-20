"""Arnés para ejecutar la SAGA completa EN PROCESO: DynamoDB simulado (moto) + bus en memoria.

Cada servicio se carga como paquete independiente (todos se llaman `app`), igual que en
producción donde cada uno es un contenedor distinto con su propia tabla.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from aegis_common.bus import InMemoryBus
from aegis_common.dynamo import create_service_table, to_ddb
from aegis_common.flags import StaticFlags
from aegis_common.outbox import OutboxRelay
from aegis_common import topics

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / "deploy/seed/catalog.json").read_text(encoding="utf-8"))


def load_service(alias: str, folder: str):
    path = ROOT / "services" / folder / "app"
    if alias not in sys.modules:
        spec = importlib.util.spec_from_file_location(alias, path / "__init__.py",
                                                      submodule_search_locations=[str(path)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        spec.loader.exec_module(module)
    return alias


class Platform:
    TABLES = ("reservations", "billing", "dispatch", "sagas", "readmodel")

    def __init__(self, client, flags: StaticFlags) -> None:
        self.client, self.flags, self.bus = client, flags, InMemoryBus()
        self.t = {name: f"aegisvolt-{name}-local" for name in self.TABLES}
        for table in self.t.values():
            create_service_table(client, table)
        self._seed()

        res = importlib.import_module(f"{load_service('svc_reservation', 'station-reservation')}.handlers")
        bil = importlib.import_module(f"{load_service('svc_billing', 'billing-wallet')}.handlers")
        dis = importlib.import_module(f"{load_service('svc_dispatch', 'energy-dispatch')}.handlers")
        chg = importlib.import_module("svc_dispatch.charger")
        orc = importlib.import_module(f"{load_service('svc_orchestrator', 'saga-orchestrator')}.engine")
        prj = importlib.import_module(f"{load_service('svc_projection', 'query-projection')}.projections")
        from aegis_common.circuit_breaker import CircuitBreaker

        self.charger = chg.FakeCharger()
        self.breaker = CircuitBreaker("charger", failure_threshold=2, recovery_timeout_s=60)
        self.reservation = res.ReservationHandlers(client, self.t["reservations"], flags)
        self.billing = bil.BillingHandlers(client, self.t["billing"], flags, tariff_cop_per_kwh=1000, force_peak=True)
        self.dispatch = dis.DispatchHandlers(client, self.t["dispatch"], flags, self.charger, self.breaker)
        self.engine = orc.SagaEngine(client, self.t["sagas"], "local", step_timeout_s=30)
        self.StartSagaRequest = orc.StartSagaRequest
        self.projector = prj.Projector(client, self.t["readmodel"])

        b = self.bus
        b.subscribe(topics.RESERVATION_COMMANDS, self.reservation.handle)
        b.subscribe(topics.DISPATCH_EVENTS, self.reservation.handle)
        b.subscribe(topics.BILLING_COMMANDS, self.billing.handle)
        b.subscribe(topics.DISPATCH_COMMANDS, self.dispatch.handle)
        b.subscribe(topics.SAGA_REPLIES, self.engine.handle)
        for t in (topics.STATION_EVENTS, topics.SAGA_EVENTS, topics.BILLING_EVENTS, topics.DISPATCH_EVENTS):
            b.subscribe(t, self.projector.handle)
        self.relays = [OutboxRelay(client, self.t[n], b) for n in ("reservations", "billing", "dispatch", "sagas")]

    def _seed(self) -> None:
        put = self.client.put_item
        for st in CATALOG["stations"]:
            connectors = {}
            for c in st["connectors"]:
                put(TableName=self.t["reservations"], Item=to_ddb({
                    "pk": f"STATION#{st['station_id']}", "sk": f"CONNECTOR#{c['connector_id']}",
                    "status": "AVAILABLE", "version": 0, **c}))
                connectors[c["connector_id"]] = {**c, "status": "AVAILABLE"}
            put(TableName=self.t["readmodel"], Item=to_ddb({
                "pk": "STATIONS", "sk": f"STATION#{st['station_id']}",
                **{k: v for k, v in st.items() if k != "connectors"}, "connectors": connectors}))
        for f in CATALOG["fleets"]:
            put(TableName=self.t["billing"], Item=to_ddb({
                "pk": f"WALLET#{f['fleet_id']}", "sk": "WALLET", "fleet_id": f["fleet_id"], "name": f["name"],
                "available_cop": f["available_cop"], "held_cop": 0, "captured_cop": 0, "version": 0}))

    async def pump(self, max_rounds: int = 50) -> None:
        """Publica outboxes y entrega mensajes hasta que el sistema queda en reposo."""
        for _ in range(max_rounds):
            sent = 0
            for relay in self.relays:
                sent += await relay.run_once()
            delivered = await self.bus.deliver_all()
            if sent == 0 and delivered == 0:
                return
        raise AssertionError("el sistema no convergió")

    def start(self, **overrides):
        idem = overrides.pop("idempotency_key", None)
        req = {"fleet_id": "flota-andina", "station_id": "EST-BOG-080", "connector_id": "C1",
               "vehicle_id": "VAN-ANDI-01", "estimated_kwh": 40, **overrides}
        return self.engine.start(self.StartSagaRequest(**req), idem)

    def item(self, table: str, pk: str, sk: str) -> dict:
        from aegis_common.dynamo import get

        return get(self.client, self.t[table], pk, sk)


@pytest.fixture
def platform():
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        flags = StaticFlags(ENABLE_DYNAMIC_SURGE_PRICING=False, ENABLE_FAST_CHARGE_BOOST=True,
                            SIMULATE_CHARGER_HARDWARE_FAULT=False)
        yield Platform(client, flags)
