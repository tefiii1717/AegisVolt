"""E2E: flujo completo desde la API GraphQL (como lo usa el frontend) contra el stack de Docker Compose.

  docker compose -f deploy/compose/docker-compose.yml up -d --build
  E2E_BFF_URL=http://localhost:4000 pytest tests/e2e -m e2e

Verifica la SAGA feliz y el rollback inducido con la flag SIMULATE_CHARGER_HARDWARE_FAULT,
incluido el estado final de las tablas DynamoDB (Floci).
"""

from __future__ import annotations

import os
import time
import uuid

import boto3
import httpx
import pytest

pytestmark = pytest.mark.e2e

BFF = os.getenv("E2E_BFF_URL", "http://localhost:4000")
DDB = os.getenv("E2E_DYNAMODB_ENDPOINT", "http://localhost:4566")
TX = "id state outcome failureReason amountCop compensations { action status refundedCop } steps { step status }"


def token(role: str) -> str:
    resp = httpx.post(f"{BFF}/auth/dev-token", json={"role": role}, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def gql(tok: str, query: str, variables: dict | None = None) -> dict:
    resp = httpx.post(f"{BFF}/graphql", json={"query": query, "variables": variables or {}},
                      headers={"authorization": f"Bearer {tok}"}, timeout=15)
    body = resp.json()
    assert not body.get("errors"), body
    return body["data"]


def wait_terminal(tok: str, saga_id: str, timeout_s: int = 60) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        tx = gql(tok, f"query($id: ID!) {{ transaction(id: $id) {{ {TX} isTerminal }} }}", {"id": saga_id})["transaction"]
        if tx and tx["isTerminal"]:
            return tx
        time.sleep(1)
    raise AssertionError(f"la SAGA {saga_id} no terminó en {timeout_s}s")


def free_connector(tok: str) -> tuple[str, str]:
    stations = gql(tok, "{ stations { id source connectors { id tier status } } }")["stations"]
    for s in stations:
        for c in s["connectors"]:
            if s["source"] == "CORE" and c["status"] == "AVAILABLE" and c["tier"] == "FAST":
                return s["id"], c["id"]
    raise AssertionError("no hay conectores libres")


def reserve(tok: str, station: str, connector: str, fleet: str = "flota-andina") -> str:
    data = gql(tok, f"mutation($i: ReserveAndChargeInput!) {{ reserveAndCharge(input: $i) {{ transaction {{ {TX} }} }} }}",
               {"i": {"fleetId": fleet, "stationId": station, "connectorId": connector, "vehicleId": "VAN-ANDI-01",
                      "estimatedKwh": 20, "idempotencyKey": str(uuid.uuid4())}})
    return data["reserveAndCharge"]["transaction"]["id"]


def set_flag(tok: str, name: str, enabled: bool) -> None:
    gql(tok, "mutation($n: String!, $e: Boolean!) { setFeatureFlag(name: $n, enabled: $e) { enabled } }",
        {"n": name, "e": enabled})
    time.sleep(4)  # > TTL de la caché de flags (3 s): el cambio se aplica sin redesplegar


@pytest.fixture(scope="module")
def admin():
    return token("platform-admin")


def test_operador_no_puede_cambiar_flags():
    resp = httpx.post(f"{BFF}/graphql", headers={"authorization": f"Bearer {token('fleet-operator')}"}, json={
        "query": 'mutation { setFeatureFlag(name: "ENABLE_DYNAMIC_SURGE_PRICING", enabled: true) { enabled } }'})
    assert resp.json()["errors"][0]["extensions"]["code"] == "FORBIDDEN"


def test_saga_feliz(admin):
    set_flag(admin, "SIMULATE_CHARGER_HARDWARE_FAULT", False)
    station, connector = free_connector(admin)
    tx = wait_terminal(admin, reserve(admin, station, connector))
    assert tx["state"] == "COMPLETED" and tx["outcome"] == "SUCCESS"


def test_fallo_inducido_ejecuta_compensaciones(admin):
    set_flag(admin, "SIMULATE_CHARGER_HARDWARE_FAULT", True)
    try:
        station, connector = free_connector(admin)
        saga_id = reserve(admin, station, connector)
        tx = wait_terminal(admin, saga_id)
    finally:
        set_flag(admin, "SIMULATE_CHARGER_HARDWARE_FAULT", False)
    assert tx["state"] == "COMPENSATED" and tx["failureReason"] == "HARDWARE_FAULT_SIMULATED"
    assert [c["action"] for c in tx["compensations"] if c["status"] == "DONE"] == ["REFUND_FUNDS", "RELEASE_CONNECTOR"]

    ddb = boto3.client("dynamodb", endpoint_url=DDB, region_name="us-east-1",
                       aws_access_key_id="test", aws_secret_access_key="test")
    hold = ddb.get_item(TableName="aegisvolt-billing-local", Key={"pk": {"S": f"HOLD#{saga_id}"}, "sk": {"S": "HOLD"}})
    assert hold["Item"]["status"]["S"] == "RELEASED"
    res = ddb.get_item(TableName="aegisvolt-reservations-local",
                       Key={"pk": {"S": f"RESERVATION#{saga_id}"}, "sk": {"S": "RESERVATION"}})
    assert res["Item"]["status"]["S"] == "RELEASED"
