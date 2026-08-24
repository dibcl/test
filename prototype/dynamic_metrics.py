"""Deterministic synthetic metric evolution for test-only telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import random
from typing import Any


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class SmoothSignal:
    value: float
    mean: float
    minimum: float
    maximum: float
    sigma: float
    smoothing: float
    spike_probability: float
    spike_min: float
    spike_max: float

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "SmoothSignal":
        mean = float(config["mean"])
        return cls(
            value=float(config.get("initial", mean)),
            mean=mean,
            minimum=float(config["min"]),
            maximum=float(config["max"]),
            sigma=float(config.get("sigma", 1.0)),
            smoothing=float(config.get("smoothing", 0.3)),
            spike_probability=float(config.get("spike_probability", 0.02)),
            spike_min=float(config.get("spike_min", 0.0)),
            spike_max=float(config.get("spike_max", 0.0)),
        )

    def step(self, rng: random.Random) -> tuple[float, bool]:
        spike = rng.random() < self.spike_probability
        target = self.mean + rng.gauss(0.0, self.sigma)
        if spike:
            target += rng.uniform(self.spike_min, self.spike_max)
        self.value += self.smoothing * (target - self.value)
        self.value = _clamp(self.value, self.minimum, self.maximum)
        return round(self.value, 3), spike


@dataclass(frozen=True)
class DynamicSample:
    timestamp: str
    cpu: float
    memory: float
    disk_io: float
    network_io: float
    spike: bool
    keyboard_delta: int
    mouse_delta: int
    processes: tuple[dict[str, Any], ...]


class DynamicMetricsEngine:
    """Seeded AR-like signals with bounded Gaussian jitter and rare spikes."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.rng = random.Random(int(config["seed"]))
        self.cpu = SmoothSignal.from_config(config["cpu"])
        self.memory = SmoothSignal.from_config(config["memory"])
        self.disk_io = SmoothSignal.from_config(config["disk_io"])
        self.network_io = SmoothSignal.from_config(config["network_io"])
        self.process_pool = tuple(dict(item) for item in config["process_pool"])
        if not self.process_pool or not any(bool(item.get("primary")) for item in self.process_pool):
            raise ValueError("process_pool must contain at least one primary process")
        self.keyboard_total = int(config.get("initial_keyboard_count", 0))
        self.mouse_total = int(config.get("initial_mouse_count", 0))
        self.sequence = 0

    def sample(self, timestamp: datetime) -> DynamicSample:
        cpu, cpu_spike = self.cpu.step(self.rng)
        memory, memory_spike = self.memory.step(self.rng)
        disk_io, disk_spike = self.disk_io.step(self.rng)
        network_io, network_spike = self.network_io.step(self.rng)
        spike = cpu_spike or memory_spike or disk_spike or network_spike

        activity = _clamp((cpu - self.cpu.minimum) / max(1.0, self.cpu.maximum - self.cpu.minimum), 0.0, 1.0)
        keyboard_delta = max(0, round(self.rng.gauss(2.0 + 18.0 * activity, 2.0)))
        mouse_delta = max(0, round(self.rng.gauss(3.0 + 24.0 * activity, 3.0)))
        if spike:
            keyboard_delta += self.rng.randint(1, 5)
            mouse_delta += self.rng.randint(2, 8)
        self.keyboard_total += keyboard_delta
        self.mouse_total += mouse_delta

        processes: list[dict[str, Any]] = []
        weights = [max(0.01, float(item.get("cpu_weight", 1.0))) for item in self.process_pool]
        weight_total = sum(weights)
        for index, item in enumerate(self.process_pool):
            primary = bool(item.get("primary"))
            pid_base = int(item["pid_base"])
            pid_jitter = 0 if primary else self.rng.randint(0, int(item.get("pid_jitter", 8)))
            thread_base = int(item.get("threads", 10))
            thread_jitter = self.rng.randint(-1, 1) if not primary else 0
            process_cpu = max(0.0, cpu * weights[index] / weight_total + self.rng.gauss(0.0, 0.15))
            processes.append({
                "name": str(item["name"]),
                "pid": pid_base + pid_jitter,
                "cpu": round(process_cpu, 3),
                "threads": max(1, thread_base + thread_jitter),
                "primary": primary,
            })
        self.sequence += 1
        return DynamicSample(
            timestamp.isoformat(timespec="milliseconds"),
            cpu,
            memory,
            disk_io,
            network_io,
            spike,
            keyboard_delta,
            mouse_delta,
            tuple(processes),
        )
