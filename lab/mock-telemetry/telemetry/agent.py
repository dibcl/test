from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .clocks import BaseClock
from .providers import BaseMetricsProvider
from .transports import BaseTransport


@dataclass(slots=True)
class AgentSettings:
    interval_seconds: float = 1.0
    duration_seconds: float | None = None
    schema_version: int = 1


class TelemetryAgent:
    def __init__(
        self,
        provider: BaseMetricsProvider,
        transport: BaseTransport,
        clock: BaseClock,
        settings: AgentSettings,
    ) -> None:
        self._provider = provider
        self._transport = transport
        self._clock = clock
        self._settings = settings
        self._provider_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    async def set_provider(self, provider: BaseMetricsProvider) -> None:
        """Atomically replace the active metrics provider at runtime."""
        async with self._provider_lock:
            old = self._provider
            await provider.start()
            self._provider = provider
            await old.stop()

    async def stop(self) -> None:
        self._stop_event.set()

    async def _collect(self):
        async with self._provider_lock:
            return await self._provider.snapshot(self._clock)

    async def run(self) -> None:
        await self._provider.start()
        await self._transport.open()
        elapsed = 0.0

        try:
            while not self._stop_event.is_set():
                snapshot = await self._collect()
                envelope: dict[str, Any] = {
                    "schema_version": self._settings.schema_version,
                    "observed_at": snapshot.observed_at,
                    "provider": snapshot.provider,
                    "metrics": snapshot.metrics,
                    "metadata": snapshot.metadata,
                }
                await self._transport.send(envelope)

                if (
                    self._settings.duration_seconds is not None
                    and elapsed >= self._settings.duration_seconds
                ):
                    break

                interval = self._settings.interval_seconds
                await self._clock.sleep(interval)
                elapsed += interval
        finally:
            await self._provider.stop()
            await self._transport.close()
