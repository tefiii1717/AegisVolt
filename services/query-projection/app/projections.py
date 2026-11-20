"""Lado de LECTURA de CQRS: proyecciones construidas desde eventos de dominio.

Las proyecciones son idempotentes y tolerantes al desorden: cada actualización está condicionada a
que el evento sea más nuevo (timestamp o versión) que lo ya proyectado. La UI ve consistencia
EVENTUAL: tras una mutación, el estado aparece en el modelo de lectura milisegundos después.
"""

from __future__ import annotations

import asyncio
import logging

from aegis_common.dynamo import from_ddb, query_pk, to_ddb, values
from aegis_common.envelope import Envelope

log = logging.getLogger("projection")

STATIONS_PK = "STATIONS"
FLEETS_PK = "FLEETS"


class Projector:
    def __init__(self, client, table: str) -> None:
        self.client, self.table = client, table
        self.applied = 0

    async def handle(self, env: Envelope) -> None:
        fn = {
            "station.ConnectorStatusChanged": self.connector_status,
            "telemetry.MeterValuesRecorded": self.telemetry,
            "saga.SagaStateChanged": self.saga_changed,
            "billing.WalletBalanceChanged": self.wallet_changed,
            "dispatch.SessionEnded": self.session_ended,
        }.get(env.type)
        if fn:
            await asyncio.to_thread(self._safe, fn, env)

    def _safe(self, fn, env: Envelope) -> None:
        try:
            fn(env)
            self.applied += 1
        except self.client.exceptions.ConditionalCheckFailedException:
            log.debug("evento obsoleto ignorado", extra={"type": env.type})

    def _update_connector(self, station_id: str, connector_id: str, sets: dict, ts: str) -> None:
        names = {"#c": "connectors", "#id": connector_id, "#ts": "updated_at"}
        exprs, vals = [], values(ts=ts)
        for i, (field, value) in enumerate(sets.items()):
            names[f"#f{i}"] = field
            exprs.append(f"#c.#id.#f{i} = :v{i}")
            vals.update(values(**{f"v{i}": value}))
        self.client.update_item(
            TableName=self.table, Key=to_ddb({"pk": STATIONS_PK, "sk": f"STATION#{station_id}"}),
            UpdateExpression="SET " + ", ".join([*exprs, "#c.#id.#ts = :ts"]),
            ConditionExpression="attribute_exists(#c.#id) AND (attribute_not_exists(#c.#id.#ts) OR #c.#id.#ts <= :ts)",
            ExpressionAttributeNames=names, ExpressionAttributeValues=vals)

    def connector_status(self, env: Envelope) -> None:
        p = env.payload
        self._update_connector(p["station_id"], p["connector_id"],
                               {"status": p["status"], "saga_id": p.get("saga_id") or "-"},
                               p.get("changed_at") or env.occurred_at.isoformat())

    def telemetry(self, env: Envelope) -> None:
        p = env.payload
        self._update_connector(p["station_id"], p["connector_id"],
                               {"last_power_kw": p.get("power_kw", 0), "energy_kwh": p.get("energy_kwh", 0),
                                "soc_percent": p.get("soc_percent") if p.get("soc_percent") is not None else -1,
                                "telemetry_at": p["measured_at"]}, p["measured_at"])

    def session_ended(self, env: Envelope) -> None:
        p = env.payload
        self._update_connector(p["station_id"], p["connector_id"], {"last_power_kw": 0},
                               env.occurred_at.isoformat())

    def saga_changed(self, env: Envelope) -> None:
        snap = env.payload
        item = {"pk": f"SAGA#{snap['saga_id']}", "sk": "SUMMARY", **snap,
                "gsi1pk": "SAGAS", "gsi1sk": snap.get("created_at", env.occurred_at.isoformat())}
        self.client.put_item(TableName=self.table, Item=to_ddb(item),
                             ConditionExpression="attribute_not_exists(pk) OR version < :v",
                             ExpressionAttributeValues=values(v=snap.get("version", 0)))

    def wallet_changed(self, env: Envelope) -> None:
        p = env.payload
        self.client.update_item(
            TableName=self.table, Key=to_ddb({"pk": FLEETS_PK, "sk": f"FLEET#{p['fleet_id']}"}),
            UpdateExpression="SET available_cop = :a, held_cop = :h, captured_cop = :c, version = :v, updated_at = :t",
            ConditionExpression="attribute_not_exists(version) OR version < :v",
            ExpressionAttributeValues=values(a=p["available_cop"], h=p["held_cop"], c=p.get("captured_cop", 0),
                                             v=p["version"], t=p.get("changed_at")))

    # ---------- consultas ----------
    def stations(self) -> list[dict]:
        docs = query_pk(self.client, self.table, STATIONS_PK, sk_prefix="STATION#")
        for d in docs:
            d["connectors"] = sorted(d.get("connectors", {}).values(), key=lambda c: c["connector_id"])
            d.pop("pk", None)
            d.pop("sk", None)
        return sorted(docs, key=lambda d: d["station_id"])

    def station(self, station_id: str) -> dict | None:
        return next((s for s in self.stations() if s["station_id"] == station_id), None)

    def sagas(self, limit: int = 20) -> list[dict]:
        resp = self.client.query(TableName=self.table, IndexName="gsi1", KeyConditionExpression="gsi1pk = :p",
                                 ExpressionAttributeValues=values(p="SAGAS"), ScanIndexForward=False, Limit=limit)
        return [self._clean(from_ddb(i)) for i in resp.get("Items", [])]

    def saga(self, saga_id: str) -> dict | None:
        resp = self.client.get_item(TableName=self.table, Key=to_ddb({"pk": f"SAGA#{saga_id}", "sk": "SUMMARY"}))
        item = from_ddb(resp.get("Item"))
        return self._clean(item) if item else None

    def fleets(self) -> list[dict]:
        return [self._clean(f) for f in query_pk(self.client, self.table, FLEETS_PK, sk_prefix="FLEET#")]

    @staticmethod
    def _clean(item: dict) -> dict:
        return {k: v for k, v in item.items() if k not in ("pk", "sk", "gsi1pk", "gsi1sk")}
