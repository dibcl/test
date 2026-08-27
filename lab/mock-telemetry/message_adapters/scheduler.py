from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from datetime import datetime, timedelta
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
        self.heartbeat_startup_delay_seconds = _interval(
            config, "heartbeat_startup_delay_seconds", 9.0
        )
        self.version_seconds = _interval(config, "version_seconds", 7200.0)
        self.version_startup_delay_seconds = _interval(
            config, "version_startup_delay_seconds", 25.0
        )
        self.performance_sample_seconds = _interval(
            config, "performance_sample_seconds", 60.0
        )
        self.qoe_batch_seconds = _interval(config, "qoe_batch_seconds", 300.0)
        self.process_seconds = _interval(config, "process_seconds", 300.0)
        self.process_offset_seconds = _interval(config, "process_offset_seconds", 7.0)
        jitter = config.get("process_jitter_seconds", 0.0)
        if isinstance(jitter, bool) or not isinstance(jitter, (int, float)):
            raise ValueError("message_adapter.schedule.process_jitter_seconds must be a number")
        self.process_jitter_seconds = max(0.0, float(jitter))
        self._started = False
        self._next_heartbeat = self.heartbeat_startup_delay_seconds
        self._next_version = self.version_startup_delay_seconds
        self._next_performance_sample = self.performance_sample_seconds
        self._next_qoe_batch = self.qoe_batch_seconds
        self._next_process = self.process_seconds + self.process_offset_seconds
        self._process_sequence = 1
        self._performance_samples: deque[dict[str, Any]] = deque(maxlen=5)
        self._last_qoe_emitted_at: str | None = None
        self.class_a = encoder.class_a
        self._next_8007 = 312.0
        self._next_8059_startup = self.heartbeat_startup_delay_seconds
        self._next_8059_status = 22.0
        self._next_9053 = self.process_seconds + self.process_offset_seconds
        self._next_9056 = 293.0

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
        self.class_a.observe(snapshot, elapsed_seconds)
        if not self._started:
            if self.class_a.enabled:
                messages.extend(
                    [
                        self.class_a.message_9055(snapshot),
                        self.class_a.message_9053(snapshot, startup=True),
                    ]
                )
            messages.extend(
                [
                    self.encoder.environment_9050(snapshot),
                    *self.encoder.software_9054(snapshot),
                ]
            )
            self._started = True
            self._next_version = self.version_startup_delay_seconds

        if self.class_a.enabled and elapsed_seconds >= self._next_8059_startup:
            messages.append(self.class_a.message_8059(snapshot, startup=True))
            self._next_8059_startup = math.inf

        if self.class_a.enabled and elapsed_seconds >= self._next_8059_status:
            messages.append(self.class_a.message_8059(snapshot, startup=False))
            self._next_8059_status = self._advance(
                self._next_8059_status, 300.0, elapsed_seconds
            )

        if self.class_a.enabled and elapsed_seconds >= self._next_9056:
            messages.append(self.class_a.message_9056(snapshot))
            self._next_9056 = self._advance(self._next_9056, 304.0, elapsed_seconds)

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
                performance_message = self.encoder.performance_9051(
                    snapshot,
                    list(self._performance_samples),
                )
                messages.append(performance_message)
                self._last_qoe_emitted_at = performance_message.emitted_at
            self._next_qoe_batch = self._advance(
                self._next_qoe_batch, self.qoe_batch_seconds, elapsed_seconds
            )

        if elapsed_seconds >= self._next_process:
            process_observed_at = None
            paired_delay = snapshot.metadata.get("process_pair_delay_seconds")
            if (
                self._last_qoe_emitted_at is not None
                and isinstance(paired_delay, (int, float))
                and not isinstance(paired_delay, bool)
                and math.isfinite(float(paired_delay))
            ):
                process_observed_at = (
                    datetime.fromisoformat(self._last_qoe_emitted_at)
                    + timedelta(seconds=float(paired_delay))
                ).isoformat()
            messages.append(
                self.encoder.process_9052(snapshot, observed_at=process_observed_at)
            )
            self._process_sequence += 1
            jitter = self.process_jitter_seconds * math.sin(self._process_sequence * 2.399)
            self._next_process = (
                self._process_sequence * self.process_seconds
                + self.process_offset_seconds
                + jitter
            )

        if self.class_a.enabled and elapsed_seconds >= self._next_9053:
            if self.class_a.should_emit_log_batch(snapshot):
                messages.append(self.class_a.message_9053(snapshot))
            self._next_9053 = self._advance(self._next_9053, 300.0, elapsed_seconds)

        if self.class_a.enabled and elapsed_seconds >= self._next_8007:
            messages.append(self.class_a.message_8007(snapshot))
            self._next_8007 = self._advance(self._next_8007, 301.0, elapsed_seconds)
        return messages
