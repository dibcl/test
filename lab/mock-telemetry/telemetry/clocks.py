from __future__ import annotations

import asyncio
import math
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone


class BaseClock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        raise NotImplementedError

    @abstractmethod
    async def sleep(self, seconds: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def monotonic(self) -> float:
        raise NotImplementedError

    async def sleep_until(self, deadline: float) -> None:
        await self.sleep(max(0.0, deadline - self.monotonic()))


class RealClock(BaseClock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    async def sleep(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("sleep seconds must be finite and >= 0")
        await asyncio.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()


class SimulatedClock(BaseClock):
    def __init__(self, start: datetime | None = None) -> None:
        current = start or datetime(2030, 1, 1, tzinfo=timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("simulated clock start must be timezone-aware")
        self._current = current
        self._elapsed = 0.0

    def now(self) -> datetime:
        return self._current

    async def sleep(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("sleep seconds must be finite and >= 0")
        self._current += timedelta(seconds=seconds)
        self._elapsed += seconds
        await asyncio.sleep(0)

    def monotonic(self) -> float:
        return self._elapsed


class ScaledRealClock(BaseClock):
    """Wall-clock paced virtual time for accelerated offline validation."""

    def __init__(self, time_scale: float, start: datetime | None = None) -> None:
        if not math.isfinite(time_scale) or time_scale <= 0:
            raise ValueError("scaled clock time_scale must be finite and > 0")
        current = start or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("scaled clock start must be timezone-aware")
        self.time_scale = float(time_scale)
        self._start = current
        self._wall_start = time.monotonic()

    def monotonic(self) -> float:
        return (time.monotonic() - self._wall_start) * self.time_scale

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self.monotonic())

    async def sleep(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("sleep seconds must be finite and >= 0")
        await asyncio.sleep(seconds / self.time_scale)
