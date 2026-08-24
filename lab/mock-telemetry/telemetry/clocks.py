from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone


class BaseClock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        raise NotImplementedError

    @abstractmethod
    async def sleep(self, seconds: float) -> None:
        raise NotImplementedError


class RealClock(BaseClock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class SimulatedClock(BaseClock):
    def __init__(self, start: datetime | None = None) -> None:
        self._current = start or datetime(2030, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._current

    async def sleep(self, seconds: float) -> None:
        self._current += timedelta(seconds=seconds)
        await asyncio.sleep(0)
