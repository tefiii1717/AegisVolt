"""Cliente de Feature Flags respaldado por DynamoDB (una tabla por ambiente).

Tabla aegisvolt-feature-flags-<env>; ítems pk = "FLAG#<NOMBRE>", sk = "FLAG".
El valor se cachea `ttl` segundos: un cambio hecho por la Lambda flags-admin se refleja en todos
los pods en ≤ ttl s, SIN redesplegar. Como cada ambiente tiene su propia tabla, cambiar dev no
puede afectar prod (aislamiento físico, no solo lógico).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .dynamo import from_ddb

log = logging.getLogger("aegis.flags")

KNOWN_FLAGS = (
    "ENABLE_DYNAMIC_SURGE_PRICING",
    "ENABLE_FAST_CHARGE_BOOST",
    "SIMULATE_CHARGER_HARDWARE_FAULT",
    "STRANGLER_ROUTE_STATIONS_TO_CORE",
)


class FeatureFlags:
    def __init__(self, client, table: str, *, ttl_s: float = 3.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.client, self.table, self.ttl_s, self.clock = client, table, ttl_s, clock
        self._cache: dict[str, bool] = {}
        self._loaded_at = -1e9
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        flags: dict[str, bool] = {}
        kwargs = {"TableName": self.table}
        while True:
            resp = self.client.scan(**kwargs)
            for raw in resp.get("Items", []):
                item = from_ddb(raw)
                if str(item.get("pk", "")).startswith("FLAG#"):
                    flags[item["pk"][5:]] = bool(item.get("enabled", False))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        self._cache, self._loaded_at = flags, self.clock()

    def snapshot(self) -> dict[str, bool]:
        with self._lock:
            if self.clock() - self._loaded_at > self.ttl_s:
                try:
                    self._refresh()
                except Exception:  # noqa: BLE001 - si la tabla falla, se usa el último valor conocido
                    log.warning("no se pudo refrescar flags; usando caché", exc_info=True)
            return dict(self._cache)

    def is_enabled(self, name: str, default: bool = False) -> bool:
        return self.snapshot().get(name, default)


class StaticFlags:
    """Implementación para tests."""

    def __init__(self, **flags: bool) -> None:
        self.flags = dict(flags)

    def snapshot(self) -> dict[str, bool]:
        return dict(self.flags)

    def is_enabled(self, name: str, default: bool = False) -> bool:
        return self.flags.get(name, default)
