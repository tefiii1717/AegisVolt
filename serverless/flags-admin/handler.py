"""AWS Lambda `flags-admin` — administra las Feature Flags del ambiente SIN redesplegar.

Expuesta por una REST API PRIVADA de API Gateway (endpoint_type = PRIVATE) a la que solo se llega
desde la VPC a través del Interface VPC Endpoint `execute-api` (resource policy aws:SourceVpce).

Rutas (evento proxy de API Gateway REST, formato 1.0):
  GET  /flags            → lista flags del ambiente
  GET  /flags/{name}     → una flag
  PUT  /flags/{name}     → {"enabled": bool}

Cada ambiente tiene su PROPIA tabla (aegisvolt-feature-flags-<env>): cambiar dev nunca toca prod.
Las flags con `prod_locked = true` (p. ej. inyección de fallos) no se pueden activar en prod.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

log = logging.getLogger()
log.setLevel(logging.INFO)

TABLE = os.environ.get("FLAGS_TABLE", "aegisvolt-feature-flags-local")
ENV = os.environ.get("AEGIS_ENV", "local")
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

_table = None


def table():
    global _table
    if _table is None:
        endpoint = os.environ.get("DYNAMODB_ENDPOINT") or None
        _table = boto3.resource("dynamodb", endpoint_url=endpoint).Table(TABLE)
    return _table


def _resp(status: int, body: Any) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json"},
            "body": json.dumps(body, default=str, ensure_ascii=False)}


def _view(item: dict) -> dict:
    return {"name": item["pk"].removeprefix("FLAG#"), "enabled": bool(item.get("enabled")),
            "description": item.get("description"), "prod_locked": bool(item.get("prod_locked")),
            "environment": ENV, "updated_at": item.get("updated_at"), "updated_by": item.get("updated_by")}


def _audit(event: str, **fields: Any) -> None:
    log.info(json.dumps({"service": "flags-admin", "environment": ENV, "event": event, **fields}))


def list_flags() -> dict:
    items = table().scan(FilterExpression=Attr("sk").eq("FLAG")).get("Items", [])
    return _resp(200, {"environment": ENV, "flags": sorted((_view(i) for i in items), key=lambda f: f["name"])})


def get_flag(name: str) -> dict:
    item = table().get_item(Key={"pk": f"FLAG#{name}", "sk": "FLAG"}).get("Item")
    return _resp(200, _view(item)) if item else _resp(404, {"error": "flag not found"})


def put_flag(name: str, raw_body: str | None, actor: str) -> dict:
    try:
        body = json.loads(raw_body or "{}")  # solo JSON: nunca pickle/yaml
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid json"})
    if not isinstance(body, dict) or set(body) != {"enabled"} or not isinstance(body["enabled"], bool):
        return _resp(400, {"error": 'body must be exactly {"enabled": true|false}'})
    current = table().get_item(Key={"pk": f"FLAG#{name}", "sk": "FLAG"}).get("Item")
    if not current:
        return _resp(404, {"error": "flag not found"})
    if ENV == "prod" and current.get("prod_locked") and body["enabled"]:
        _audit("flag_change_denied", flag=name, actor=actor)
        return _resp(403, {"error": f"{name} está bloqueada en prod"})
    now = datetime.now(UTC).isoformat()
    updated = table().update_item(
        Key={"pk": f"FLAG#{name}", "sk": "FLAG"},
        UpdateExpression="SET enabled = :e, updated_at = :t, updated_by = :a ADD revision :one",
        ConditionExpression="attribute_exists(pk)",
        ExpressionAttributeValues={":e": body["enabled"], ":t": now, ":a": actor[:128], ":one": 1},
        ReturnValues="ALL_NEW",
    )["Attributes"]
    _audit("flag_changed", flag=name, enabled=body["enabled"], actor=actor, previous=bool(current.get("enabled")))
    return _resp(200, _view(updated))


def lambda_handler(event: dict, _context: Any = None) -> dict:
    method = event.get("httpMethod", "GET")
    path = (event.get("path") or "/").rstrip("/") or "/"
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    actor = headers.get("x-aegis-actor", "unknown")
    if path == "/flags" and method == "GET":
        return list_flags()
    match = re.fullmatch(r"/flags/([^/]+)", path)
    if not match:
        return _resp(404, {"error": "not found"})
    name = match.group(1)
    if not NAME_RE.match(name):
        return _resp(400, {"error": "invalid flag name"})
    if method == "GET":
        return get_flag(name)
    if method == "PUT":
        return put_flag(name, event.get("body"), actor)
    return _resp(405, {"error": "method not allowed"})
