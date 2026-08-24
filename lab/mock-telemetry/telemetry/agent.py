from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .clocks import BaseClock
from .model import TelemetrySnapshot
from .providers import BaseMetricsProvider
from .transports import BaseTransport


@dataclass(slots=True)
class AgentSettings:
    interval_seconds: float = 1.0
    duration_seconds: float | None = None
    schema_version: int = 1

    def validate(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("duration_seconds must be >= 0")
        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")


class TelemetryAgent:
    def __init__(
        self,
        provider: BaseMetricsProvider,
        transport: BaseTransport,
        clock: BaseClock,
        settings: AgentSettings,
    ) -> None:
        settings.validate()
        self._provider = provider
        self._transport = transport
        self._clock = clock
        self._settings = settings
        self._provider_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    @property
    def provider(self) -> BaseMetricsProvider:
        return self._provider

    async def set_provider(self, provider: BaseMetricsProvider) -> None:
        """Atomically replace the active metrics provider at runtime."""
        async with self._provider_lock:
            old = self._provider
            await provider.start()
            self._provider = provider
            await old.stop()

    async def stop(self) -> None:
        self._stop_event.set()

    async def _collect(self) -> TelemetrySnapshot:
        async with self._provider_lock:
            return await self._provider.snapshot(self._clock)

    async def run(self) -> None:
        self._settings.validate()
        await self._provider.start()
        await self._transport.open()
        elapsed = 0.0

        try:
            while not self._stop_event.is_set():
                snapshot = await self._collect()
                await self._transport.send(
                    snapshot.to_envelope(schema_version=self._settings.schema_version)
                )

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
