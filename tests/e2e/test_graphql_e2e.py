"""E2E: flujo completo desde la API GraphQL (como lo usa el frontend) contra el stack de Docker Compose.

  docker compose -f deploy/compose/docker-compose.yml up -d --build
  E2E_BFF_URL=http://localhost:4000 pytest tests/e2e -m e2e

Verifica el registro y la separación de roles, la SAGA feliz con su factura y el rollback inducido
con la flag SIMULATE_CHARGER_HARDWARE_FAULT, incluido el estado final de las tablas DynamoDB (Floci).
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
ENV = os.getenv("E2E_ENV", "local")  # local = Compose; dev/test/prod = EKS emulado en Floci
TX = "id state outcome failureReason amountCop compensations { action status refundedCop } steps { step status }"


ADMIN_EMAIL = os.getenv("E2E_ADMIN_EMAIL", "admin@admin.aegisvolt.co")
ADMIN_PASSWORD = os.getenv("E2E_ADMIN_PASSWORD", "AegisLocal2026")
PASSWORD = "Carga2026e2e"


def login(email: str, password: str = PASSWORD) -> str:
    resp = httpx.post(f"{BFF}/auth/login", json={"email": email, "password": password}, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def register(email: str, password: str = PASSWORD) -> httpx.Response:
    return httpx.post(f"{BFF}/auth/register", json={"email": email, "name": "Prueba E2E", "password": password},
                      timeout=10)


def new_operator() -> str:
    email = f"e2e-{uuid.uuid4().hex[:8]}@operador.aegisvolt.co"
    assert register(email).status_code == 201
    return login(email)


def add_payment_method(tok: str, company: str = "Transportes E2E S.A.S.") -> str:
    data = gql(tok, "mutation($i: RegisterPaymentMethodInput!) { registerPaymentMethod(input: $i) { id accountLast4 } }",
               {"i": {"companyName": company, "accountNumber": "0011223344"}})
    assert data["registerPaymentMethod"]["accountLast4"] == "3344"
    return data["registerPaymentMethod"]["id"]


def errors(tok: str, query: str, variables: dict | None = None) -> list[dict]:
    resp = httpx.post(f"{BFF}/graphql", json={"query": query, "variables": variables or {}},
                      headers={"authorization": f"Bearer {tok}"}, timeout=15)
    return resp.json().get("errors") or []


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


RESERVE = f"mutation($i: ReserveAndChargeInput!) {{ reserveAndCharge(input: $i) {{ transaction {{ {TX} }} }} }}"


def reserve_input(pm: str, station: str, connector: str) -> dict:
    return {"i": {"paymentMethodId": pm, "stationId": station, "connectorId": connector, "vehicleId": "WKT-512",
                  "estimatedKwh": 20, "idempotencyKey": str(uuid.uuid4())}}


def reserve(tok: str, pm: str, station: str, connector: str) -> str:
    return gql(tok, RESERVE, reserve_input(pm, station, connector))["reserveAndCharge"]["transaction"]["id"]


def set_flag(tok: str, name: str, enabled: bool) -> None:
    gql(tok, "mutation($n: String!, $e: Boolean!) { setFeatureFlag(name: $n, enabled: $e) { enabled } }",
        {"n": name, "e": enabled})
    time.sleep(4)  # > TTL de la caché de flags (3 s): el cambio se aplica sin redesplegar


@pytest.fixture(scope="module")
def admin():
    return login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def operator():
    tok = new_operator()
    return tok, add_payment_method(tok)


def test_solo_los_operadores_se_registran_solos():
    resp = register(f"e2e-{uuid.uuid4().hex[:8]}@admin.aegisvolt.co")
    assert resp.status_code == 403
    assert register("alguien@gmail.com").status_code == 400


def test_roles_separados(admin, operator):
    op, pm = operator
    assert errors(op, 'mutation { setFeatureFlag(name: "ENABLE_DYNAMIC_SURGE_PRICING", enabled: true) { enabled } }'
                  )[0]["extensions"]["code"] == "FORBIDDEN"
    assert errors(admin, "{ transactions { id } }")[0]["extensions"]["code"] == "FORBIDDEN"
    station, connector = free_connector(op)
    assert errors(admin, RESERVE, reserve_input(pm, station, connector))[0]["extensions"]["code"] == "FORBIDDEN"

    auditor_email = f"e2e-{uuid.uuid4().hex[:8]}@auditor.aegisvolt.co"
    gql(admin, "mutation($i: CreateUserInput!) { createUser(input: $i) { role } }",
        {"i": {"email": auditor_email, "name": "Auditor E2E", "password": PASSWORD}})
    auditor = login(auditor_email)
    assert errors(auditor, RESERVE, reserve_input(pm, station, connector))[0]["extensions"]["code"] == "FORBIDDEN"
    assert errors(auditor, "{ users { email } }")[0]["extensions"]["code"] == "FORBIDDEN"

    # Otro operador no puede usar un medio de pago ajeno.
    intruder = new_operator()
    assert errors(intruder, RESERVE, reserve_input(pm, station, connector))[0]["extensions"]["code"] == "FORBIDDEN"


def test_saga_feliz_emite_factura(admin, operator):
    op, pm = operator
    set_flag(admin, "SIMULATE_CHARGER_HARDWARE_FAULT", False)
    station, connector = free_connector(op)
    saga_id = reserve(op, pm, station, connector)
    tx = wait_terminal(op, saga_id)
    assert tx["state"] == "COMPLETED" and tx["outcome"] == "SUCCESS"

    invoice = None
    for _ in range(20):  # consistencia eventual de la proyección
        invoice = next((i for i in gql(op, "{ invoices { sagaId companyName totalCop } }")["invoices"]
                        if i["sagaId"] == saga_id), None)
        if invoice:
            break
        time.sleep(1)
    assert invoice and invoice["companyName"] == "Transportes E2E S.A.S." and invoice["totalCop"] == tx["amountCop"]

    other = new_operator()
    assert gql(other, "query($id: ID!) { transaction(id: $id) { id } }", {"id": saga_id})["transaction"] is None
    assert gql(other, "{ invoices { sagaId } }")["invoices"] == []


def test_fallo_inducido_ejecuta_compensaciones(admin, operator):
    op, pm = operator
    set_flag(admin, "SIMULATE_CHARGER_HARDWARE_FAULT", True)
    try:
        station, connector = free_connector(op)
        saga_id = reserve(op, pm, station, connector)
        tx = wait_terminal(op, saga_id)
    finally:
        set_flag(admin, "SIMULATE_CHARGER_HARDWARE_FAULT", False)
    assert tx["state"] == "COMPENSATED" and tx["failureReason"] == "HARDWARE_FAULT_SIMULATED"
    assert [c["action"] for c in tx["compensations"] if c["status"] == "DONE"] == ["REFUND_FUNDS", "RELEASE_CONNECTOR"]

    ddb = boto3.client("dynamodb", endpoint_url=DDB, region_name="us-east-1",
                       aws_access_key_id="test", aws_secret_access_key="test")  # noqa: S106 (credenciales de Floci)
    hold = ddb.get_item(TableName=f"aegisvolt-billing-{ENV}", Key={"pk": {"S": f"HOLD#{saga_id}"}, "sk": {"S": "HOLD"}})
    assert hold["Item"]["status"]["S"] == "RELEASED"
    res = ddb.get_item(TableName=f"aegisvolt-reservations-{ENV}",
                       Key={"pk": {"S": f"RESERVATION#{saga_id}"}, "sk": {"S": "RESERVATION"}})
    assert res["Item"]["status"]["S"] == "RELEASED"
    invoice = ddb.get_item(TableName=f"aegisvolt-billing-{ENV}",
                           Key={"pk": {"S": f"INVOICE#{saga_id}"}, "sk": {"S": "INVOICE"}})
    assert "Item" not in invoice  # una carga revertida no se factura
