"""Relay del Transactional Outbox: DynamoDB (GSI disperso) → Kafka.

Puede ejecutarse dentro del servicio (OUTBOX_RELAY_MODE=inprocess) o como contenedor SIDECAR
del mismo pod (`python -m aegis_common.outbox`), desacoplando la infraestructura de mensajería
del código de dominio.

Garantía: at-least-once. Si el proceso muere entre publicar y marcar como publicado, el mensaje
se reenvía; por eso todos los consumidores son idempotentes.
"""

from __future__ import annotations

import asyncio
import logging

from .bus import MessageBus
from .dynamo import OUTBOX_PENDING, from_ddb, now_iso, to_ddb, values

log = logging.getLogger("aegis.outbox")


class OutboxRelay:
    def __init__(self, client, table: str, bus: MessageBus, *, batch_size: int = 25) -> None:
        self.client, self.table, self.bus, self.batch_size = client, table, bus, batch_size
        self.published_total = 0
        self.last_error: str | None = None

    def _pending(self) -> list[dict]:
        resp = self.client.query(
            TableName=self.table,
            IndexName="gsi1",
            KeyConditionExpression="gsi1pk = :p",
            ExpressionAttributeValues=values(p=OUTBOX_PENDING),
            Limit=self.batch_size,
            ScanIndexForward=True,  # orden de creación → orden por SAGA
        )
        return [from_ddb(i) for i in resp.get("Items", [])]

    def _mark_published(self, item: dict) -> None:
        try:
            self.client.update_item(
                TableName=self.table,
                Key=to_ddb({"pk": item["pk"], "sk": item["sk"]}),
                UpdateExpression="REMOVE gsi1pk, gsi1sk SET published_at = :t",
                ConditionExpression="attribute_exists(gsi1pk)",
                ExpressionAttributeValues=values(t=now_iso()),
            )
        except self.client.exceptions.ConditionalCheckFailedException:
            pass  # otra réplica del relay ya lo marcó

    async def run_once(self) -> int:
        items = await asyncio.to_thread(self._pending)
        sent = 0
        for item in items:
            try:
                await self.bus.publish(item["topic"], item["partition_key"], item["body"].encode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - se detiene el lote para preservar el orden
                self.last_error = str(exc)
                log.warning("publicación fallida, se reintentará", extra={"error": str(exc)})
                break
            await asyncio.to_thread(self._mark_published, item)
            sent += 1
        self.published_total += sent
        return sent

    async def run_forever(self, interval_s: float = 0.5) -> None:
        while True:
            try:
                sent = await self.run_once()
                if sent == 0:
                    await asyncio.sleep(interval_s)
            except Exception:  # noqa: BLE001
                log.exception("error en el relay del outbox")
                await asyncio.sleep(2)


async def _main() -> None:  # pragma: no cover - entrada del sidecar
    from .bus import KafkaBus
    from .config import get_settings
    from .dynamo import make_client
    from .logs import configure_logging

    s = get_settings()
    configure_logging(f"{s.service_name}-outbox-relay", s.environment, s.log_level)
    bus = KafkaBus(s.kafka_bootstrap, f"{s.service_name}-relay")
    while True:
        try:
            await bus.start()
            break
        except Exception:  # noqa: BLE001
            log.warning("Kafka no disponible, reintentando")
            await asyncio.sleep(3)
    relay = OutboxRelay(make_client(s.aws_region, s.dynamodb_endpoint), s.table_name, bus)
    log.info("outbox relay sidecar iniciado", extra={"table": s.table_name})
    await relay.run_forever(s.outbox_poll_interval_s)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
