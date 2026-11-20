"""legacy-csms — sistema HEREDADO de gestión de cargadores (Charge Station Management System).

Simula el monolito que AegisVolt está estrangulando (Strangler Fig):
  * Expone un catálogo con su propio modelo y vocabulario (`/api/v1/estaciones`).
  * Controla el hardware por OCPP 1.6 (remote-start / remote-stop).
  * Emite telemetría en formato OCPP-J (`[2, id, "MeterValues", {...}]`) al tópico legacy.ocpp.meter-values.

El nuevo core NUNCA consume este modelo directamente: lo traduce una capa anticorrupción
(ACL con Kafka Streams para eventos; adaptador en el BFF para el catálogo).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

logging.basicConfig(level="INFO", format='{"service":"legacy-csms","level":"%(levelname)s","message":"%(message)s"}')
log = logging.getLogger("legacy-csms")

CATALOG = json.loads((Path(__file__).parent / "catalogo_legado.json").read_text(encoding="utf-8"))
METER_TOPIC = os.getenv("LEGACY_METER_TOPIC", "legacy.ocpp.meter-values")
TIME_SCALE = float(os.getenv("SESSION_TIME_SCALE", "60"))
CHAOS = {"failure_rate": float(os.getenv("LEGACY_FAILURE_RATE", "0")), "latency_ms": int(os.getenv("LEGACY_LATENCY_MS", "50"))}

# estado en memoria (el legado no es cloud-native: un solo proceso, sin persistencia distribuida)
transactions: dict[tuple[str, str], dict] = {}
idempotency: dict[str, dict] = {}
_next_tx = 5000
producer = None


async def _start_producer() -> None:
    global producer
    bootstrap = os.getenv("KAFKA_BOOTSTRAP")
    if not bootstrap:
        return
    from aiokafka import AIOKafkaProducer

    for _ in range(30):
        try:
            producer = AIOKafkaProducer(bootstrap_servers=bootstrap, acks="all")
            await producer.start()
            return
        except Exception:  # noqa: BLE001
            log.warning("Kafka no disponible para el legado; reintentando")
            await asyncio.sleep(3)


async def meter_loop() -> None:
    """Cada 5 s emite MeterValues OCPP 1.6 de cada transacción activa."""
    while True:
        await asyncio.sleep(5)
        for (cp, conn), tx in list(transactions.items()):
            elapsed_h = (datetime.now(UTC) - tx["start"]).total_seconds() * TIME_SCALE / 3600
            power_w = tx["max_power_kw"] * 1000 * random.uniform(0.86, 0.98)  # noqa: S311 - simulación, no criptografía
            tx["wh"] = int(min(elapsed_h * tx["max_power_kw"] * 1000, 999_999))
            soc = min(100.0, tx["soc0"] + elapsed_h * 100 * tx["max_power_kw"] / 120)
            frame = [2, str(uuid.uuid4())[:8], "MeterValues", {
                "connectorId": int(conn.lstrip("C")),
                "transactionId": tx["id"],
                "meterValue": [{
                    "timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "sampledValue": [
                        {"value": str(tx["wh"]), "measurand": "Energy.Active.Import.Register", "unit": "Wh"},
                        {"value": f"{power_w:.0f}", "measurand": "Power.Active.Import", "unit": "W"},
                        {"value": f"{soc:.1f}", "measurand": "SoC", "unit": "Percent"},
                    ],
                }],
            }]
            if producer:
                await producer.send_and_wait(METER_TOPIC, json.dumps(frame).encode(), key=cp.encode())


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _start_producer()
    task = asyncio.create_task(meter_loop())
    yield
    task.cancel()
    if producer:
        await producer.stop()


app = FastAPI(title="legacy-csms (sistema heredado)", lifespan=lifespan)


@app.get("/health/live")
@app.get("/health/ready")
def health() -> dict:
    return {"status": "UP"}


@app.get("/api/v1/estaciones")
def estaciones() -> dict:
    """Catálogo en el formato heredado (nombres en español abreviados, estados como texto)."""
    out = []
    for est in CATALOG["estaciones"]:
        conns = []
        for c in est["conectores"]:
            ocupado = (est["cod"], f"C{c['num']}") in transactions
            conns.append({**c, "estado": "Cargando" if ocupado else "Libre"})
        out.append({**est, "conectores": conns})
    return {"total": len(out), "estaciones": out, "generado": datetime.now(UTC).strftime("%d/%m/%Y %H:%M")}


class RemoteStart(BaseModel):
    chargePointId: str
    connectorId: str
    maxPowerKw: float
    idTag: str


async def _chaos() -> None:
    await asyncio.sleep(CHAOS["latency_ms"] / 1000)
    if random.random() < CHAOS["failure_rate"]:  # noqa: S311 - inyección de fallos simulada
        raise HTTPException(503, "CSMS sobrecargado")


@app.post("/ocpp/remote-start")
async def remote_start(req: RemoteStart, idempotency_key: str = Header(...)) -> dict:
    global _next_tx
    if idempotency_key in idempotency:
        return idempotency[idempotency_key]
    await _chaos()
    key = (req.chargePointId, req.connectorId)
    if key in transactions:
        return {"status": "Rejected", "reason": "ConnectorBusy"}
    _next_tx += 1
    transactions[key] = {"id": _next_tx, "start": datetime.now(UTC), "max_power_kw": req.maxPowerKw,
                         "soc0": random.uniform(15, 35), "wh": 0}  # noqa: S311
    result = {"status": "Accepted", "transactionId": _next_tx}
    idempotency[idempotency_key] = result
    log.info(f"RemoteStartTransaction aceptado {req.chargePointId}/{req.connectorId} tx={_next_tx}")
    return result


class RemoteStop(BaseModel):
    chargePointId: str
    connectorId: str


@app.post("/ocpp/remote-stop")
async def remote_stop(req: RemoteStop, idempotency_key: str = Header(...)) -> dict:
    tx = transactions.pop((req.chargePointId, req.connectorId), None)
    return {"status": "Accepted", "transactionId": tx["id"] if tx else None}


class Chaos(BaseModel):
    failure_rate: float = 0.0
    latency_ms: int = 50


@app.post("/admin/chaos")
def set_chaos(cfg: Chaos) -> dict:
    """Permite demostrar el Circuit Breaker: con failure_rate=1 el circuito de energy-dispatch se abre."""
    CHAOS.update(failure_rate=max(0.0, min(cfg.failure_rate, 1.0)), latency_ms=max(0, min(cfg.latency_ms, 10_000)))
    return CHAOS
