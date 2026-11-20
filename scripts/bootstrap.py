"""Bootstrap de datos de AegisVolt para un ambiente.

  python scripts/bootstrap.py --env local --create-tables --topics   # Compose (Floci + Kafka local)
  python scripts/bootstrap.py --env dev                              # nube: las tablas ya las creó Terraform

Es idempotente: no sobrescribe saldos ni flags existentes (usa attribute_not_exists), salvo --reset.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from aegis_common.dynamo import create_service_table, make_client, to_ddb
from aegis_common.topics import ALL_TOPICS

ROOT = Path(__file__).resolve().parents[1]
SERVICE_TABLES = ("reservations", "billing", "dispatch", "sagas", "readmodel", "feature-flags")


def table(name: str, env: str) -> str:
    return f"aegisvolt-{name}-{env}"


def put(client, table_name: str, item: dict, reset: bool) -> bool:
    kwargs = {} if reset else {"ConditionExpression": "attribute_not_exists(pk)"}
    try:
        client.put_item(TableName=table_name, Item=to_ddb(item), **kwargs)
        return True
    except client.exceptions.ConditionalCheckFailedException:
        return False


def seed(client, env: str, reset: bool) -> dict:
    catalog = json.loads((ROOT / "deploy/seed/catalog.json").read_text(encoding="utf-8"))
    created = {"connectors": 0, "stations": 0, "wallets": 0, "flags": 0}
    for st in catalog["stations"]:
        connectors = {}
        for c in st["connectors"]:
            created["connectors"] += put(client, table("reservations", env), {
                "pk": f"STATION#{st['station_id']}", "sk": f"CONNECTOR#{c['connector_id']}",
                "status": "AVAILABLE", "version": 0, **c}, reset)
            connectors[c["connector_id"]] = {**c, "status": "AVAILABLE"}
        put(client, table("reservations", env), {"pk": f"STATION#{st['station_id']}", "sk": "META",
                                                 **{k: v for k, v in st.items() if k != "connectors"}}, reset)
        created["stations"] += put(client, table("readmodel", env), {
            "pk": "STATIONS", "sk": f"STATION#{st['station_id']}",
            **{k: v for k, v in st.items() if k != "connectors"}, "connectors": connectors}, reset)
    for f in catalog["fleets"]:
        created["wallets"] += put(client, table("billing", env), {
            "pk": f"WALLET#{f['fleet_id']}", "sk": "WALLET", "fleet_id": f["fleet_id"], "name": f["name"],
            "available_cop": f["available_cop"], "held_cop": 0, "captured_cop": 0, "version": 0}, reset)
        put(client, table("readmodel", env), {"pk": "FLEETS", "sk": f"FLEET#{f['fleet_id']}", "fleet_id": f["fleet_id"],
                                              "name": f["name"], "available_cop": f["available_cop"], "held_cop": 0,
                                              "captured_cop": 0, "version": 0}, reset)
    for name, spec in catalog["flags"].items():
        created["flags"] += put(client, table("feature-flags", env), {
            "pk": f"FLAG#{name}", "sk": "FLAG", "enabled": bool(spec.get(env, False)),
            "description": spec["description"], "prod_locked": spec["prod_locked"], "updated_by": "bootstrap"}, reset)
    return created


async def create_topics(bootstrap: str, partitions: int) -> list[str]:
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic
    from aiokafka.errors import TopicAlreadyExistsError

    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    for _ in range(30):
        try:
            await admin.start()
            break
        except Exception:  # noqa: BLE001
            await asyncio.sleep(2)
    try:
        existing = set(await admin.list_topics())
        missing = [t for t in ALL_TOPICS if t not in existing]
        if missing:
            try:
                await admin.create_topics([NewTopic(t, num_partitions=partitions, replication_factor=1) for t in missing])
            except TopicAlreadyExistsError:
                pass
        return missing
    finally:
        await admin.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, choices=["local", "dev", "test", "prod"])
    ap.add_argument("--endpoint", default=os.getenv("DYNAMODB_ENDPOINT"))
    ap.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    ap.add_argument("--create-tables", action="store_true")
    ap.add_argument("--topics", action="store_true")
    ap.add_argument("--partitions", type=int, default=6)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    client = make_client(args.region, args.endpoint)
    for attempt in range(60):  # Floci / DynamoDB pueden tardar unos segundos en aceptar conexiones
        try:
            existing = set(client.list_tables()["TableNames"])
            break
        except Exception:  # noqa: BLE001
            print(f"esperando DynamoDB ({attempt})...")
            time.sleep(2)
    else:
        return 1
    if args.create_tables:
        for name in SERVICE_TABLES:
            if table(name, args.env) not in existing:
                create_service_table(client, table(name, args.env))
                print(f"tabla creada: {table(name, args.env)}")
    print("semilla:", seed(client, args.env, args.reset))
    if args.topics:
        print("tópicos creados:", asyncio.run(create_topics(os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"), args.partitions)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
