from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from telemetry.model import TelemetrySnapshot

from .model import ProtocolMessage
from .windows import WindowsMessageEncoder


def _interval(config: Mapping[str, Any], key: str, default: float) -> float:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"message_adapter.schedule.{key} must be a number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"message_adapter.schedule.{key} must be finite and > 0")
    return result


class TelemetryMessageScheduler:
    def __init__(self, encoder: WindowsMessageEncoder, config: Mapping[str, Any]) -> None:
        self.encoder = encoder
        self.heartbeat_seconds = _interval(config, "heartbeat_seconds", 30.0)
        self.version_seconds = _interval(config, "version_seconds", 7200.0)
        self.performance_sample_seconds = _interval(
            config, "performance_sample_seconds", 60.0
        )
        self.qoe_batch_seconds = _interval(config, "qoe_batch_seconds", 300.0)
        self.process_seconds = _interval(config, "process_seconds", 300.0)
        self._started = False
        self._next_heartbeat = 0.0
        self._next_version = 0.0
        self._next_performance_sample = self.performance_sample_seconds
        self._next_qoe_batch = self.qoe_batch_seconds
        self._next_process = self.process_seconds
        self._performance_samples: deque[dict[str, Any]] = deque(maxlen=5)

    @staticmethod
    def _advance(next_due: float, interval: float, elapsed: float) -> float:
        skipped = math.floor((elapsed - next_due) / interval) + 1
        return next_due + max(1, skipped) * interval

    def messages_for(
        self,
        snapshot: TelemetrySnapshot,
        elapsed_seconds: float,
    ) -> list[ProtocolMessage]:
        messages: list[ProtocolMessage] = []
        if not self._started:
            messages.extend(
                [
                    self.encoder.version_4004(snapshot),
                    self.encoder.environment_9050(snapshot),
                    self.encoder.heartbeat_4002(snapshot),
                ]
            )
            self._started = True
            self._next_heartbeat = self.heartbeat_seconds
            self._next_version = self.version_seconds

        if elapsed_seconds >= self._next_heartbeat:
            messages.append(self.encoder.heartbeat_4002(snapshot))
            self._next_heartbeat = self._advance(
                self._next_heartbeat, self.heartbeat_seconds, elapsed_seconds
            )

        if elapsed_seconds >= self._next_version:
            messages.append(self.encoder.version_4004(snapshot))
            self._next_version = self._advance(
                self._next_version, self.version_seconds, elapsed_seconds
            )

        if elapsed_seconds >= self._next_performance_sample:
            self._performance_samples.append(self.encoder.performance_sample(snapshot))
            self._next_performance_sample = self._advance(
                self._next_performance_sample,
                self.performance_sample_seconds,
                elapsed_seconds,
            )

        if elapsed_seconds >= self._next_qoe_batch:
            if len(self._performance_samples) == self._performance_samples.maxlen:
                messages.append(
                    self.encoder.performance_9051(
                        snapshot,
                        list(self._performance_samples),
                    )
                )
            self._next_qoe_batch = self._advance(
                self._next_qoe_batch, self.qoe_batch_seconds, elapsed_seconds
            )

        if elapsed_seconds >= self._next_process:
            messages.append(self.encoder.process_9052(snapshot))
            self._next_process = self._advance(
                self._next_process, self.process_seconds, elapsed_seconds
            )
        return messages
