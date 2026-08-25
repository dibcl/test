from __future__ import annotations

import asyncio
import math
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
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("sleep seconds must be finite and >= 0")
        await asyncio.sleep(seconds)


class SimulatedClock(BaseClock):
    def __init__(self, start: datetime | None = None) -> None:
        current = start or datetime(2030, 1, 1, tzinfo=timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("simulated clock start must be timezone-aware")
        self._current = current

    def now(self) -> datetime:
        return self._current

    async def sleep(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("sleep seconds must be finite and >= 0")
        self._current += timedelta(seconds=seconds)
        await asyncio.sleep(0)
