"""Acceso técnico a DynamoDB: serialización, transacciones idempotentes y outbox.

Diseño single-table por servicio (Database-per-Service):
    pk (S), sk (S)  + GSI `gsi1` (gsi1pk, gsi1sk) disperso (sparse)
    - Outbox pendiente:  gsi1pk = "OUTBOX#PENDING"
    - Sagas activas:     gsi1pk = "SAGA#ACTIVE" (solo orquestador)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config

from .envelope import Envelope

_ser = TypeSerializer()
_de = TypeDeserializer()

OUTBOX_PENDING = "OUTBOX#PENDING"
DEFAULT_TTL_DAYS = 7


def make_client(region: str, endpoint: str | None = None):
    cfg = Config(retries={"max_attempts": 5, "mode": "adaptive"}, connect_timeout=2, read_timeout=5)
    return boto3.client("dynamodb", region_name=region, endpoint_url=endpoint or None, config=cfg)


def _clean(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def to_ddb(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _ser.serialize(_clean(v)) for k, v in item.items() if v is not None}


def plain(value: Any) -> Any:
    """Decimal → int/float para poder serializar a JSON."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, set, tuple)):
        return [plain(v) for v in value]
    return value


def from_ddb(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return plain({k: _de.deserialize(v) for k, v in item.items()})


def values(**kwargs: Any) -> dict[str, Any]:
    """Construye ExpressionAttributeValues: values(s="RESERVED") → {":s": {"S": "RESERVED"}}."""
    return {f":{k}": _ser.serialize(_clean(v)) for k, v in kwargs.items()}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def ttl_epoch(days: int = DEFAULT_TTL_DAYS) -> int:
    return int(time.time()) + days * 86400


class TxnOutcome(str, Enum):
    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"


class BusinessConditionFailed(Exception):
    """Falló una condición de negocio (no la de idempotencia)."""

    def __init__(self, labels: list[str]) -> None:
        super().__init__(f"condiciones fallidas: {labels}")
        self.labels = labels


@dataclass
class Txn:
    """Constructor de TransactWriteItems con etiquetas para identificar qué condición falló."""

    table: str
    ops: list[dict[str, Any]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    def _add(self, label: str, op: dict[str, Any]) -> Txn:
        if len(self.ops) >= 100:
            raise ValueError("DynamoDB admite máximo 100 operaciones por transacción")
        self.ops.append(op)
        self.labels.append(label)
        return self

    @staticmethod
    def _expr(body: dict[str, Any], condition: str | None, names: dict | None, vals: dict | None) -> dict:
        if condition:
            body["ConditionExpression"] = condition
        if names:
            body["ExpressionAttributeNames"] = names
        if vals:
            body["ExpressionAttributeValues"] = vals
        return body

    def put(self, item: dict[str, Any], *, label: str = "put", condition: str | None = None,
            names: dict | None = None, vals: dict | None = None) -> Txn:
        body = self._expr({"TableName": self.table, "Item": to_ddb(item)}, condition, names, vals)
        return self._add(label, {"Put": body})

    def update(self, key: dict[str, Any], expression: str, *, label: str = "update", condition: str | None = None,
               names: dict | None = None, vals: dict | None = None) -> Txn:
        body = self._expr({"TableName": self.table, "Key": to_ddb(key), "UpdateExpression": expression},
                          condition, names, vals)
        return self._add(label, {"Update": body})

    def check(self, key: dict[str, Any], condition: str, *, label: str = "check",
              names: dict | None = None, vals: dict | None = None) -> Txn:
        body = self._expr({"TableName": self.table, "Key": to_ddb(key)}, condition, names, vals)
        return self._add(label, {"ConditionCheck": body})

    def outbox(self, topic: str, envelope: Envelope, *, key: str | None = None) -> Txn:
        """Transactional Outbox: el mensaje se guarda en la MISMA transacción que el cambio de estado."""
        created = now_iso()
        return self.put(
            {
                "pk": f"OUTBOX#{envelope.message_id}",
                "sk": "OUTBOX",
                "gsi1pk": OUTBOX_PENDING,
                "gsi1sk": f"{created}#{envelope.message_id}",
                "topic": topic,
                "partition_key": key or envelope.correlation_id,
                "message_type": envelope.type,
                "correlation_id": envelope.correlation_id,
                "body": envelope.model_dump_json(),
                "created_at": created,
                "ttl": ttl_epoch(),
            },
            label=f"outbox:{envelope.type}",
            condition="attribute_not_exists(pk)",
        )


def execute(client, txn: Txn, *, message_id: str | None = None, consumer: str = "") -> TxnOutcome:
    """Ejecuta la transacción. Si hay message_id, la hace idempotente (registro MSG#<id>)."""
    ops, labels = list(txn.ops), list(txn.labels)
    if message_id:
        ops.insert(0, {"Put": {
            "TableName": txn.table,
            "Item": to_ddb({"pk": f"MSG#{consumer}#{message_id}", "sk": "PROCESSED",
                            "processed_at": now_iso(), "ttl": ttl_epoch()}),
            "ConditionExpression": "attribute_not_exists(pk)",
        }})
        labels.insert(0, "idempotency")
    try:
        client.transact_write_items(TransactItems=ops)
    except client.exceptions.TransactionCanceledException as exc:
        reasons = exc.response.get("CancellationReasons", []) or []
        failed = [labels[i] for i, r in enumerate(reasons) if r.get("Code") == "ConditionalCheckFailed"]
        if "idempotency" in failed:
            return TxnOutcome.DUPLICATE
        raise BusinessConditionFailed(failed or ["unknown"]) from exc
    return TxnOutcome.APPLIED


def already_processed(client, table: str, message_id: str, consumer: str = "") -> bool:
    resp = client.get_item(TableName=table, Key=to_ddb({"pk": f"MSG#{consumer}#{message_id}", "sk": "PROCESSED"}),
                           ConsistentRead=True)
    return "Item" in resp


def get(client, table: str, pk: str, sk: str, *, consistent: bool = True) -> dict[str, Any] | None:
    resp = client.get_item(TableName=table, Key=to_ddb({"pk": pk, "sk": sk}), ConsistentRead=consistent)
    return from_ddb(resp.get("Item"))


def query_pk(client, table: str, pk: str, *, sk_prefix: str | None = None) -> list[dict[str, Any]]:
    expr = "pk = :pk"
    vals = values(pk=pk)
    if sk_prefix:
        expr += " AND begins_with(sk, :sk)"
        vals.update(values(sk=sk_prefix))
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"TableName": table, "KeyConditionExpression": expr, "ExpressionAttributeValues": vals}
    while True:
        resp = client.query(**kwargs)
        items += [from_ddb(i) for i in resp.get("Items", [])]
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def create_service_table(client, table: str) -> None:
    """Usado por tests/seed local. En la nube las tablas las crea Terraform."""
    client.create_table(
        TableName=table,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("pk", "sk", "gsi1pk", "gsi1sk")],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        GlobalSecondaryIndexes=[{
            "IndexName": "gsi1",
            "KeySchema": [{"AttributeName": "gsi1pk", "KeyType": "HASH"}, {"AttributeName": "gsi1sk", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )


def json_body(item: dict[str, Any]) -> dict[str, Any]:
    return json.loads(item["body"])
