"""Transporte de mensajes: Kafka (producción) e InMemoryBus (tests deterministas)."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Protocol

from .envelope import Envelope, InvalidMessage, parse_envelope
from .logs import correlation_scope
from .topics import DEAD_LETTER

log = logging.getLogger("aegis.bus")

Handler = Callable[[Envelope], Awaitable[None]]


class MessageBus(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, topic: str, key: str, value: bytes) -> None: ...


class KafkaBus:
    """Productor idempotente (acks=all, enable_idempotence) sobre aiokafka."""

    def __init__(self, bootstrap: str, client_id: str) -> None:
        self._bootstrap = bootstrap
        self._client_id = client_id
        self._producer = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            client_id=self._client_id,
            acks="all",
            enable_idempotence=True,
            linger_ms=5,
        )
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()

    async def publish(self, topic: str, key: str, value: bytes) -> None:
        assert self._producer, "KafkaBus no iniciado"
        await self._producer.send_and_wait(topic, value=value, key=key.encode("utf-8"))

    @property
    def connected(self) -> bool:
        return self._producer is not None


async def dispatch_safely(raw: bytes, handler: Handler, bus: MessageBus, *, attempts: int = 3) -> None:
    """Valida, procesa con reintentos y manda a DLQ lo que no se puede procesar (poison messages)."""
    try:
        envelope = parse_envelope(raw)
    except InvalidMessage as exc:
        log.warning("mensaje inválido enviado a DLQ", extra={"error": str(exc)})
        await bus.publish(DEAD_LETTER, "invalid", raw)
        return
    with correlation_scope(envelope.correlation_id):
        for attempt in range(1, attempts + 1):
            try:
                await handler(envelope)
                return
            except Exception:  # noqa: BLE001 - reintento genérico de infraestructura
                log.exception("fallo procesando mensaje", extra={"type": envelope.type, "attempt": attempt})
                await asyncio.sleep(0.2 * 2**attempt)
        await bus.publish(DEAD_LETTER, envelope.correlation_id, raw)


class KafkaSubscriber:
    """Consumidor con commit manual: el offset se confirma solo después de procesar (at-least-once)."""

    def __init__(self, bootstrap: str, group_id: str, topics: list[str], handler: Handler, bus: MessageBus) -> None:
        self._bootstrap, self._group_id, self._topics = bootstrap, group_id, topics
        self._handler, self._bus = handler, bus
        self.healthy = False

    async def run(self) -> None:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._bootstrap,
            group_id=self._group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        while True:
            try:
                await consumer.start()
                break
            except Exception:  # noqa: BLE001
                log.warning("Kafka no disponible, reintentando en 3 s")
                await asyncio.sleep(3)
        self.healthy = True
        try:
            async for msg in consumer:
                await dispatch_safely(msg.value, self._handler, self._bus)
                await consumer.commit()
        finally:
            self.healthy = False
            await consumer.stop()


class InMemoryBus:
    """Bus en memoria: permite probar la SAGA completa sin Kafka, de forma determinista."""

    def __init__(self) -> None:
        self.queues: dict[str, deque[tuple[str, bytes]]] = defaultdict(deque)
        self.subscribers: dict[str, list[Handler]] = defaultdict(list)
        self.published: list[tuple[str, str, bytes]] = []

    async def start(self) -> None:  # pragma: no cover - trivial
        return None

    async def stop(self) -> None:  # pragma: no cover - trivial
        return None

    async def publish(self, topic: str, key: str, value: bytes) -> None:
        self.queues[topic].append((key, value))
        self.published.append((topic, key, value))

    def subscribe(self, topic: str, handler: Handler) -> None:
        self.subscribers[topic].append(handler)

    async def deliver_all(self) -> int:
        delivered = 0
        for topic in list(self.queues):
            queue = self.queues[topic]
            while queue:
                _, raw = queue.popleft()
                for handler in self.subscribers.get(topic, []):
                    await dispatch_safely(raw, handler, self, attempts=1)
                delivered += 1
        return delivered

    def messages(self, topic: str) -> list[Envelope]:
        return [parse_envelope(v) for t, _, v in self.published if t == topic]
