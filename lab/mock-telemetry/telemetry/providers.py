from __future__ import annotations

import asyncio
import json
import random
import socket
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .clocks import BaseClock
from .model import TelemetrySnapshot


class BaseMetricsProvider(ABC):
    name = "base"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    @abstractmethod
    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        raise NotImplementedError


class FrozenProfileProvider(BaseMetricsProvider):
    name = "frozen-profile"

    def __init__(self, profile_path: str | Path) -> None:
        self.path = Path(profile_path)
        with self.path.open("r", encoding="utf-8") as fp:
            self.profile = json.load(fp)
        self._performance_samples = self.profile.get("performance_samples", [])
        self._sample_index = 0

    def _next_performance(self) -> dict[str, Any]:
        if not self._performance_samples:
            return {}
        item = self._performance_samples[self._sample_index % len(self._performance_samples)]
        self._sample_index += 1
        return json.loads(json.dumps(item))

    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            observed_at=clock.now().isoformat(),
            provider=self.name,
            metrics={
                "environment": self.profile.get("environment", {}),
                "performance": self._next_performance(),
                "process_snapshot": self.profile.get("process_snapshot", {}),
            },
            metadata={"profile": str(self.path), "mode": "frozen"},
        )


class SyntheticMetricsProvider(BaseMetricsProvider):
    name = "synthetic"

    def __init__(self, profile_path: str | Path) -> None:
        self.path = Path(profile_path)
        with self.path.open("r", encoding="utf-8") as fp:
            self.profile = json.load(fp)
        cfg = self.profile.get("dynamics", {})
        self.random = random.Random(cfg.get("seed", 1))
        self.cpu_cfg = cfg.get("cpu", {})
        self.mem_cfg = cfg.get("memory", {})
        self.net_cfg = cfg.get("network_io", {})
        self.disk_cfg = cfg.get("disk_io", {})
        self.cpu = float(self.cpu_cfg.get("initial", 10.0))
        self.memory = float(self.mem_cfg.get("initial", 40.0))
        self.network = float(self.net_cfg.get("initial", 1.0))
        self.disk = float(self.disk_cfg.get("initial", 1.0))

    def _walk(self, value: float, cfg: dict[str, Any]) -> float:
        mean = float(cfg.get("mean", value))
        sigma = float(cfg.get("sigma", 1.0))
        smoothing = float(cfg.get("smoothing", 0.3))
        candidate = value * (1.0 - smoothing) + mean * smoothing + self.random.gauss(0, sigma)
        if self.random.random() < float(cfg.get("spike_probability", 0)):
            candidate += self.random.uniform(float(cfg.get("spike_min", 0)), float(cfg.get("spike_max", 0)))
        return max(float(cfg.get("min", 0)), min(float(cfg.get("max", 100)), candidate))

    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        self.cpu = self._walk(self.cpu, self.cpu_cfg)
        self.memory = self._walk(self.memory, self.mem_cfg)
        self.network = self._walk(self.network, self.net_cfg)
        self.disk = self._walk(self.disk, self.disk_cfg)
        return TelemetrySnapshot(
            observed_at=clock.now().isoformat(),
            provider=self.name,
            metrics={
                "cpu": {"percent": round(self.cpu, 2)},
                "memory": {"percent": round(self.memory, 2)},
                "network_io": {"synthetic_rate": round(self.network, 2)},
                "disk_io": {"synthetic_rate": round(self.disk, 2)},
                "process_snapshot": self.profile.get("process_snapshot", {}),
            },
            metadata={"profile": str(self.path), "mode": "dynamic"},
        )


class LiveSystemProvider(BaseMetricsProvider):
    name = "live-system"

    def __init__(self, process_limit: int = 20) -> None:
        self.process_limit = process_limit
        self._last_net = None
        self._last_time: float | None = None

    async def start(self) -> None:
        import psutil

        self._last_net = psutil.net_io_counters()
        self._last_time = time.monotonic()
        psutil.cpu_percent(interval=None)

    def _collect(self) -> dict[str, Any]:
        import psutil

        cpu = psutil.cpu_percent(interval=None, percpu=False)
        cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        disk_io = psutil.disk_io_counters()
        net = psutil.net_io_counters()
        now = time.monotonic()

        rx_rate = None
        tx_rate = None
        if self._last_net is not None and self._last_time is not None:
            delta = max(now - self._last_time, 0.001)
            rx_rate = (net.bytes_recv - self._last_net.bytes_recv) / delta
            tx_rate = (net.bytes_sent - self._last_net.bytes_sent) / delta
        self._last_net = net
        self._last_time = now

        processes = []
        for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent", "memory_info", "num_threads"]):
            try:
                info = proc.info
                rss = info["memory_info"].rss if info["memory_info"] else 0
                processes.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "cpu_percent": info["cpu_percent"] or 0.0,
                    "rss_bytes": rss,
                    "threads": info["num_threads"] or 0,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        processes.sort(key=lambda item: item["rss_bytes"], reverse=True)

        return {
            "host": {"hostname": socket.gethostname()},
            "cpu": {"percent": cpu, "per_core": cpu_per_core},
            "memory": {
                "total_bytes": mem.total,
                "available_bytes": mem.available,
                "used_bytes": mem.used,
                "percent": mem.percent,
            },
            "disk": {
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
                "percent": disk.percent,
            },
            "disk_io": {
                "read_bytes": getattr(disk_io, "read_bytes", 0),
                "write_bytes": getattr(disk_io, "write_bytes", 0),
            },
            "network_io": {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
                "tx_bytes_per_second": tx_rate,
                "rx_bytes_per_second": rx_rate,
            },
            "process_snapshot": processes[: self.process_limit],
        }

    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        metrics = await asyncio.to_thread(self._collect)
        return TelemetrySnapshot(
            observed_at=clock.now().isoformat(),
            provider=self.name,
            metrics=metrics,
            metadata={"mode": "live"},
        )
