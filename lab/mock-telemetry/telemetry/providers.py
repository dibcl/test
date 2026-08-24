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
from .model import ProviderHealth, TelemetrySnapshot


class BaseMetricsProvider(ABC):
    """Common asynchronous data-source contract for the telemetry runtime."""

    name = "base"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> ProviderHealth:
        """Return a side-effect-free readiness result.

        Providers with external dependencies should override this method rather
        than forcing the agent to take a sample just to determine readiness.
        """
        return ProviderHealth(True, "provider ready")

    @abstractmethod
    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        raise NotImplementedError


class FrozenProfileProvider(BaseMetricsProvider):
    """Replay the existing profile format without requiring test-mode gates."""

    name = "frozen-profile"

    def __init__(self, profile_path: str | Path) -> None:
        self.path = Path(profile_path)
        with self.path.open("r", encoding="utf-8") as fp:
            self.profile = json.load(fp)
        self._performance_samples = self.profile.get("performance_samples", [])
        self._sample_index = 0

    async def health_check(self) -> ProviderHealth:
        if not self.path.is_file():
            return ProviderHealth(False, f"profile missing: {self.path}")
        if not isinstance(self.profile, dict):
            return ProviderHealth(False, "profile root must be an object")
        return ProviderHealth(
            True,
            "profile loaded",
            {"path": str(self.path), "performance_samples": len(self._performance_samples)},
        )

    def _next_performance(self) -> dict[str, Any]:
        if not self._performance_samples:
            return {}
        item = self._performance_samples[self._sample_index % len(self._performance_samples)]
        self._sample_index += 1
        return json.loads(json.dumps(item))

    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        # Preserve legacy profile sections as generic data. This runtime does
        # not interpret them as proprietary protocol messages.
        metrics = {
            "environment": self.profile.get("environment", {}),
            "software_batches": self.profile.get("software_batches", []),
            "performance": self._next_performance(),
            "process_snapshot": self.profile.get("process_snapshot", {}),
            "activity_events": self.profile.get("activity_events", []),
            "connectivity_rows": self.profile.get("connectivity_rows", []),
            "ice_traces": self.profile.get("ice_traces", []),
        }
        return TelemetrySnapshot(
            observed_at=clock.now().isoformat(),
            provider=self.name,
            metrics=metrics,
            metadata={
                "profile": str(self.path),
                "mode": "frozen",
                "legacy_test_mode": self.profile.get("identity", {}).get("test_mode"),
            },
        )


class SyntheticMetricsProvider(BaseMetricsProvider):
    """Generate deterministic dynamic metrics from a profile's dynamics block."""

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

    async def health_check(self) -> ProviderHealth:
        if not self.path.is_file():
            return ProviderHealth(False, f"profile missing: {self.path}")
        dynamics = self.profile.get("dynamics")
        if dynamics is not None and not isinstance(dynamics, dict):
            return ProviderHealth(False, "dynamics must be an object when present")
        return ProviderHealth(True, "synthetic generator ready", {"path": str(self.path)})

    def _walk(self, value: float, cfg: dict[str, Any]) -> float:
        mean = float(cfg.get("mean", value))
        sigma = float(cfg.get("sigma", 1.0))
        smoothing = float(cfg.get("smoothing", 0.3))
        candidate = value * (1.0 - smoothing) + mean * smoothing + self.random.gauss(0, sigma)
        if self.random.random() < float(cfg.get("spike_probability", 0)):
            candidate += self.random.uniform(
                float(cfg.get("spike_min", 0)),
                float(cfg.get("spike_max", 0)),
            )
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


class HybridSyntheticNetworkProvider(SyntheticMetricsProvider):
    """Synthetic system metrics plus aggregate live network throughput.

    CPU, memory, disk and process data remain profile-backed/synthetic. The only
    live host observation is the aggregate byte counters returned by
    psutil.net_io_counters(pernic=False), which are converted to receive/send
    rates. No hostname, interface name, IP address, MAC address, route, DNS,
    package list, disk usage, CPU state, memory state or process list is read.

    This provider is intended for privacy-isolated lab/runtime testing. It does
    not construct proprietary management-plane messages.
    """

    name = "hybrid-synthetic-network"

    def __init__(self, profile_path: str | Path) -> None:
        super().__init__(profile_path)
        self._last_net = None
        self._last_time: float | None = None

    async def start(self) -> None:
        import psutil

        self._last_net = psutil.net_io_counters(pernic=False)
        self._last_time = time.monotonic()

    async def health_check(self) -> ProviderHealth:
        base = await super().health_check()
        if not base.healthy:
            return base
        try:
            import psutil

            counters = psutil.net_io_counters(pernic=False)
            if counters is None:
                raise RuntimeError("aggregate network counters unavailable")
        except Exception as exc:
            return ProviderHealth(False, f"aggregate network counters unavailable: {exc}")
        return ProviderHealth(
            True,
            "synthetic metrics + aggregate live network ready",
            {
                "path": str(self.path),
                "live_scope": "aggregate_network_counters_only",
            },
        )

    def _collect_network(self) -> dict[str, Any]:
        import psutil

        net = psutil.net_io_counters(pernic=False)
        if net is None:
            raise RuntimeError("aggregate network counters unavailable")
        now = time.monotonic()

        rx_rate = None
        tx_rate = None
        if self._last_net is not None and self._last_time is not None:
            delta = max(now - self._last_time, 0.001)
            rx_rate = max(0.0, (net.bytes_recv - self._last_net.bytes_recv) / delta)
            tx_rate = max(0.0, (net.bytes_sent - self._last_net.bytes_sent) / delta)

        self._last_net = net
        self._last_time = now
        return {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "tx_bytes_per_second": tx_rate,
            "rx_bytes_per_second": rx_rate,
            "scope": "aggregate",
        }

    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        self.cpu = self._walk(self.cpu, self.cpu_cfg)
        self.memory = self._walk(self.memory, self.mem_cfg)
        self.disk = self._walk(self.disk, self.disk_cfg)
        network = await asyncio.to_thread(self._collect_network)

        return TelemetrySnapshot(
            observed_at=clock.now().isoformat(),
            provider=self.name,
            metrics={
                "cpu": {"percent": round(self.cpu, 2), "source": "synthetic"},
                "memory": {"percent": round(self.memory, 2), "source": "synthetic"},
                "disk_io": {"synthetic_rate": round(self.disk, 2), "source": "synthetic"},
                "network_io": network,
                "process_snapshot": self.profile.get("process_snapshot", {}),
            },
            metadata={
                "profile": str(self.path),
                "mode": "synthetic-with-live-network",
                "live_scope": "aggregate_network_counters_only",
            },
        )


class LiveSystemProvider(BaseMetricsProvider):
    """Collect live host metrics through psutil for diagnostics.

    Unlike HybridSyntheticNetworkProvider, this diagnostic provider inspects
    live CPU, memory, disk, process and hostname state. Do not use it when the
    runtime must remain isolated from host identity/state.
    """

    name = "live-system"

    def __init__(self, process_limit: int = 20, disk_path: str | Path | None = None) -> None:
        if process_limit < 0:
            raise ValueError("process_limit must be >= 0")
        self.process_limit = process_limit
        self.disk_path = str(disk_path) if disk_path is not None else (Path.cwd().anchor or "/")
        self._last_net = None
        self._last_time: float | None = None

    async def start(self) -> None:
        import psutil

        self._last_net = psutil.net_io_counters()
        self._last_time = time.monotonic()
        psutil.cpu_percent(interval=None)

    async def health_check(self) -> ProviderHealth:
        try:
            import psutil

            disk = psutil.disk_usage(self.disk_path)
            cpu_count = psutil.cpu_count() or 0
        except Exception as exc:
            return ProviderHealth(False, f"live provider unavailable: {exc}")
        return ProviderHealth(
            True,
            "live collector ready",
            {
                "cpu_count": cpu_count,
                "disk_path": self.disk_path,
                "disk_total_bytes": disk.total,
                "process_limit": self.process_limit,
            },
        )

    def _collect(self) -> dict[str, Any]:
        import psutil

        cpu = psutil.cpu_percent(interval=None, percpu=False)
        cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(self.disk_path)
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
        for proc in psutil.process_iter(
            attrs=["pid", "name", "cpu_percent", "memory_info", "num_threads"]
        ):
            try:
                info = proc.info
                rss = info["memory_info"].rss if info["memory_info"] else 0
                processes.append(
                    {
                        "pid": info["pid"],
                        "name": info["name"],
                        "cpu_percent": info["cpu_percent"] or 0.0,
                        "rss_bytes": rss,
                        "threads": info["num_threads"] or 0,
                    }
                )
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
                "path": self.disk_path,
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
                "percent": disk.percent,
            },
            "disk_io": {
                "read_bytes": getattr(disk_io, "read_bytes", 0) if disk_io else 0,
                "write_bytes": getattr(disk_io, "write_bytes", 0) if disk_io else 0,
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
            metadata={"mode": "live-diagnostic"},
        )
