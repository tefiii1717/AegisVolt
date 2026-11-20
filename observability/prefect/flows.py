"""Flujos de Prefect para la observabilidad de AegisVolt.

  * platform_health  — salud de los tres ambientes (dev, test, prod): BFF y microservicios.
  * saga_audit       — consistencia de las SAGA en DynamoDB: huérfanas, retenciones y reservas
                       colgadas de SAGAs ya compensadas, y desfase de la proyección CQRS.
  * observability    — flujo padre programado cada 5 minutos.

Seguridad: los resultados se serializan como JSON (Prefect usa pickle por defecto) y no se persisten.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import boto3
import httpx
from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact, create_table_artifact

from audit import AuditReport, audit

TASK_OPTS = {"retries": 2, "retry_delay_seconds": 5, "persist_result": False, "result_serializer": "json"}
SERVICES = ("station-reservation", "billing-wallet", "energy-dispatch", "saga-orchestrator", "query-projection")


def env_url(kind: str, env: str) -> str | None:
    return os.getenv(f"{kind}_URL_{env.upper()}")


def ddb():
    return boto3.client("dynamodb", region_name=os.getenv("AWS_REGION", "us-east-1"),
                        endpoint_url=os.getenv("DYNAMODB_ENDPOINT") or None)


def scan(table: str, prefix: str) -> list[dict]:
    from boto3.dynamodb.types import TypeDeserializer

    de, client, items = TypeDeserializer(), ddb(), []
    kwargs = {"TableName": table, "FilterExpression": "begins_with(pk, :p)",
              "ExpressionAttributeValues": {":p": {"S": prefix}}}
    while True:
        resp = client.scan(**kwargs)
        items += [{k: de.deserialize(v) for k, v in i.items()} for i in resp.get("Items", [])]
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


@task(name="check-endpoint", **TASK_OPTS)
def check_endpoint(env: str, name: str, url: str) -> dict:
    started = datetime.now(UTC)
    try:
        resp = httpx.get(url, timeout=3)
        status = "UP" if resp.status_code == 200 else f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        status = f"DOWN ({type(exc).__name__})"
    latency = round((datetime.now(UTC) - started).total_seconds() * 1000)
    return {"environment": env, "component": name, "status": status, "latency_ms": latency}


@flow(name="platform-health", log_prints=True)
def platform_health(environments: list[str]) -> list[dict]:
    logger = get_run_logger()
    futures = []
    for env in environments:
        bff = env_url("BFF", env)
        if bff:
            futures.append(check_endpoint.submit(env, "graphql-bff", f"{bff}/health/live"))
        for svc in SERVICES:
            base = os.getenv(f"{svc.replace('-', '_').upper()}_URL_{env.upper()}")
            if base:
                futures.append(check_endpoint.submit(env, svc, f"{base}/health/ready"))
    rows = [f.result() for f in futures]
    down = [r for r in rows if r["status"] != "UP"]
    create_table_artifact(key="aegisvolt-health", table=rows, description="Salud por ambiente")
    for r in down:
        logger.error(json.dumps({"event": "component_down", **r}))
    logger.info(f"{len(rows) - len(down)}/{len(rows)} componentes en línea")
    return rows


@task(name="load-saga-state", **TASK_OPTS)
def load_state(env: str) -> dict:
    sagas = httpx.get(f"{env_url('ORCHESTRATOR', env)}/internal/sagas", params={"limit": 200}, timeout=10).json()
    projected = {}
    query = env_url("QUERY", env)
    if query:
        projected = {s["saga_id"]: s["state"] for s in httpx.get(f"{query}/sagas", params={"limit": 100}, timeout=10).json()}
    holds = [{"saga_id": h["pk"].split("#", 1)[1], **h} for h in scan(f"aegisvolt-billing-{env}", "HOLD#")]
    reservations = [{"saga_id": r["pk"].split("#", 1)[1], **r} for r in scan(f"aegisvolt-reservations-{env}", "RESERVATION#")]
    return {"sagas": sagas, "holds": holds, "reservations": reservations, "projected": projected}


@task(name="publish-metrics", **TASK_OPTS)
def publish_metrics(report: AuditReport) -> None:
    if os.getenv("CLOUDWATCH_METRICS", "false") != "true":
        return
    cw = boto3.client("cloudwatch", region_name=os.getenv("AWS_REGION", "us-east-1"))
    cw.put_metric_data(Namespace="AegisVolt/Saga", MetricData=[
        {"MetricName": k, "Value": v, "Unit": "Count", "Dimensions": [{"Name": "Environment", "Value": report.environment}]}
        for k, v in report.metrics().items()])


@flow(name="saga-audit", log_prints=True)
def saga_audit(env: str, stale_after_s: int = 120) -> dict:
    logger = get_run_logger()
    state = load_state(env)
    report = audit(env, state["sagas"], state["holds"], state["reservations"], state["projected"],
                   stale_after_s=stale_after_s)
    for kind in ("orphans", "orphan_holds", "orphan_reservations"):
        for row in getattr(report, kind):
            # correlation_id = saga_id → se puede rastrear el mismo ID en CloudWatch Logs Insights
            logger.warning(json.dumps({"event": kind, "environment": env, "correlation_id": row["saga_id"], **row}))
    publish_metrics(report)
    m = report.metrics()
    create_markdown_artifact(key=f"saga-audit-{env}", description=f"Auditoría SAGA {env}", markdown="\n".join([
        f"# Auditoría de SAGAs ({env})",
        "| Métrica | Valor |", "|---|---|", *[f"| {k} | {v} |" for k, v in m.items()],
        "", "Estado: **consistente**" if report.healthy else "Estado: **requiere atención**",
    ]))
    return m


@flow(name="aegisvolt-observability")
def observability(environments: list[str] | None = None) -> None:
    envs = environments or os.getenv("AEGIS_ENVIRONMENTS", "dev,test,prod").split(",")
    platform_health(envs)
    for env in envs:
        if env_url("ORCHESTRATOR", env):
            saga_audit(env)


if __name__ == "__main__":
    observability.serve(name="cada-5-minutos", cron="*/5 * * * *")
