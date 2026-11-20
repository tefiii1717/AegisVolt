"""Circuit Breaker con estados CERRADO → ABIERTO → SEMI-ABIERTO.

- CLOSED: las llamadas pasan; se cuentan fallos consecutivos.
- OPEN: tras `failure_threshold` fallos, se rechaza de inmediato (fail-fast) durante
  `recovery_timeout_s`, protegiendo al dependiente y al sistema que falla.
- HALF_OPEN: pasado el timeout se permite `half_open_max_calls` llamada(s) de prueba;
  si tiene éxito → CLOSED; si falla → OPEN de nuevo.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, TypeVar

log = logging.getLogger("aegis.circuit")
T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, name: str, *, failure_threshold: int = 3, recovery_timeout_s: float = 15.0,
                 half_open_max_calls: int = 1, clock: Callable[[], float] = time.monotonic) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.half_open_max_calls = half_open_max_calls
        self.clock = clock
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = asyncio.Lock()
        self.stats = {"success": 0, "failure": 0, "rejected": 0}

    @property
    def state(self) -> CircuitState:
        if self._state is CircuitState.OPEN and self.clock() - self._opened_at >= self.recovery_timeout_s:
            return CircuitState.HALF_OPEN
        return self._state

    def _transition(self, new: CircuitState) -> None:
        if new is not self._state:
            log.warning("circuit breaker cambia de estado",
                        extra={"breaker": self.name, "from": self._state.value, "to": new.value})
        self._state = new

    async def call(self, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        async with self._lock:
            state = self.state
            if state is CircuitState.OPEN:
                self.stats["rejected"] += 1
                raise CircuitOpenError(f"circuito {self.name} ABIERTO")
            if state is CircuitState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max_calls:
                    self.stats["rejected"] += 1
                    raise CircuitOpenError(f"circuito {self.name} SEMI-ABIERTO (prueba en curso)")
                self._transition(CircuitState.HALF_OPEN)
                self._half_open_inflight += 1
        try:
            result = await fn(*args, **kwargs)
        except Exception:
            async with self._lock:
                self.stats["failure"] += 1
                self._failures += 1
                if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
                    self._opened_at = self.clock()
                    self._transition(CircuitState.OPEN)
                self._half_open_inflight = max(0, self._half_open_inflight - 1)
            raise
        async with self._lock:
            self.stats["success"] += 1
            self._failures = 0
            self._half_open_inflight = max(0, self._half_open_inflight - 1)
            self._transition(CircuitState.CLOSED)
        return result

    def snapshot(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state.value, "consecutive_failures": self._failures, **self.stats}
