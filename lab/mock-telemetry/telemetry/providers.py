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
from .process_state import ProcessIdentityState


class BaseMetricsProvider(ABC):
    """Common asynchronous data-source contract for the telemetry runtime."""

    name = "base"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> ProviderHealth:
        """Return a side-effect-free readiness result."""
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

    def __init__(self, profile_path: str | Path, state_path: str | Path | None = None) -> None:
        self.path = Path(profile_path)
        self.state_path = Path(state_path) if state_path is not None else None
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
        self.process_state = ProcessIdentityState.load(self.state_path)
        self.cpu = self.process_state.dynamic_metrics.get("cpu", self.cpu)
        self.memory = self.process_state.dynamic_metrics.get("memory", self.memory)
        self.disk = self.process_state.dynamic_metrics.get("disk_io", self.disk)

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

    def _continuous_walk(self, value: float, cfg: dict[str, Any]) -> float:
        mean = float(cfg.get("mean", value))
        sigma = max(0.0, float(cfg.get("sigma", 1.0)))
        smoothing = max(0.0, min(1.0, float(cfg.get("smoothing", 0.3))))
        target = mean + self.random.gauss(0.0, sigma)
        delta = (target - value) * smoothing
        max_step = max(0.01, float(cfg.get("max_step", sigma * max(smoothing, 0.1))))
        delta = max(-max_step, min(max_step, delta))
        candidate = value + delta
        return max(float(cfg.get("min", 0)), min(float(cfg.get("max", 100)), candidate))

    def _save_runtime_state(self) -> None:
        self.process_state.update_dynamic_metrics(
            cpu=self.cpu,
            memory=self.memory,
            disk_io=self.disk,
        )
        self.process_state.save(self.state_path)

    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        self.cpu = self._continuous_walk(self.cpu, self.cpu_cfg)
        self.memory = self._continuous_walk(self.memory, self.mem_cfg)
        self.network = self._walk(self.network, self.net_cfg)
        self.disk = self._continuous_walk(self.disk, self.disk_cfg)
        self._save_runtime_state()
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
    """Synthetic system state plus aggregate live network throughput only.

    The provider deliberately avoids host identity and host-state discovery. It
    never reads hostname, interface names, addresses, routes, DNS, CPU, memory,
    disks, packages, or processes. The only live input is the aggregate network
    byte counters used internally to calculate RX/TX rates.
    """

    name = "hybrid-synthetic-network"

    def __init__(self, profile_path: str | Path, state_path: str | Path | None = None) -> None:
        super().__init__(profile_path, state_path)
        cfg = self.profile.get("dynamics", {})
        shape = self.profile.get("runtime_shape", {})
        self.cpu_cores = max(1, int(shape.get("cpu_cores", 8)))
        self.process_pool = tuple(dict(item) for item in cfg.get("process_pool", []))
        self.key_process = str(shape.get("key_process", "MMRHookService.exe"))
        self.disk_layout = tuple(dict(item) for item in shape.get("disk_layout", []))
        self.memory_shape = dict(shape.get("memory", {}))
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
        if not self.process_pool:
            return ProviderHealth(False, "runtime profile requires dynamics.process_pool")
        try:
            import psutil

            counters = psutil.net_io_counters(pernic=False)
            if counters is None:
                raise RuntimeError("aggregate network counters unavailable")
        except Exception as exc:
            return ProviderHealth(False, f"aggregate network counters unavailable: {exc}")
        return ProviderHealth(
            True,
            "synthetic system state + aggregate live network ready",
            {
                "path": str(self.path),
                "live_scope": "aggregate_network_rate_only",
                "cpu_cores": self.cpu_cores,
                "process_templates": len(self.process_pool),
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
            "tx_bytes_per_second": tx_rate,
            "rx_bytes_per_second": rx_rate,
            "scope": "aggregate-rate-only",
            "source": "live",
        }

    def _cpu_snapshot(self) -> dict[str, Any]:
        per_core = []
        for index in range(self.cpu_cores):
            bias = self.random.gauss(0.0, max(0.15, self.cpu * 0.16))
            value = self.cpu + bias
            per_core.append(round(max(0.0, min(100.0, value)), 2))
        return {
            "percent": round(self.cpu, 2),
            "per_core": per_core,
            "source": "synthetic",
        }

    def _memory_snapshot(self) -> dict[str, Any]:
        paged_base = float(self.memory_shape.get("paged_pool_mb", 440.0))
        nonpaged_base = float(self.memory_shape.get("nonpaged_pool_mb", 228.0))
        memory_delta = self.memory - float(self.mem_cfg.get("mean", self.memory))
        return {
            "percent": round(self.memory, 2),
            "paged_pool_mb": round(max(0.0, paged_base + memory_delta * 0.8), 1),
            "nonpaged_pool_mb": round(max(0.0, nonpaged_base + memory_delta * 0.4), 1),
            "source": "synthetic",
        }

    def _disk_snapshot(self) -> dict[str, Any]:
        disks = []
        layout = self.disk_layout or ({"name": "C", "size_gb": 80.0, "used_percent": 35.0, "weight": 1.0},)
        for item in layout:
            weight = max(0.01, float(item.get("weight", 1.0)))
            activity = max(0.0, self.disk * weight)
            used = float(item.get("used_percent", 35.0))
            size = float(item.get("size_gb", 0.0))
            used_gb = float(item.get("used_gb", size * used / 100.0))
            disks.append({
                "name": str(item.get("name", "disk")),
                "size_gb": round(size, 2),
                "used_gb": round(used_gb, 2),
                "used_percent": round(max(0.0, min(100.0, used_gb / size * 100.0 if size else used)), 2),
                "activity_rate": round(activity, 3),
                "read_iops": round(activity * self.random.uniform(0.02, 0.14), 3),
                "write_iops": round(activity * self.random.uniform(0.03, 0.18), 3),
                "read_kb_per_second": round(activity * self.random.uniform(0.4, 3.5), 3),
                "write_kb_per_second": round(activity * self.random.uniform(0.5, 4.5), 3),
                "read_latency_ms": round(activity * 0.003, 3),
                "write_latency_ms": round(activity * 0.004, 3),
                "queue_length": round(activity * 0.001, 3),
            })
        return {
            "system_activity": 11.94,
            "activity_rate": round(self.disk, 3),
            "per_disk": disks,
            "source": "synthetic",
        }

    def _process_snapshot(self) -> dict[str, Any]:
        rows = []
        weights = [max(0.01, float(item.get("cpu_weight", 1.0))) for item in self.process_pool]
        total_weight = sum(weights)
        for index, item in enumerate(self.process_pool):
            primary = bool(item.get("primary", False))
            pid_base = int(item.get("pid_base", 1000 + index * 100))
            pid_jitter = 0 if primary else self.random.randint(0, max(0, int(item.get("pid_jitter", 16))))
            process_name = str(item["name"])
            pid = self.process_state.get_pid(process_name, pid_base + pid_jitter)
            cpu = max(0.0, self.cpu * weights[index] / total_weight + self.random.gauss(0.0, 0.12))
            rss_mb = max(1.0, float(item.get("rss_mb", 32.0)) * (1.0 + self.random.gauss(0.0, 0.015)))
            handles = max(1, int(item.get("handles", 200)) + self.random.randint(-8, 8))
            threads = max(1, int(item.get("threads", 8)) + (0 if primary else self.random.randint(-1, 1)))
            disk_total = max(0.0, self.disk * float(item.get("disk_weight", 0.2)) * 1024.0 + self.random.gauss(0.0, 64.0))
            net_total = max(0.0, float(item.get("net_baseline", 32.0)) + self.random.gauss(0.0, 8.0))
            rows.append({
                "name": process_name,
                "pid": pid,
                "cpu_percent": round(cpu, 3),
                "rss_mb": round(rss_mb, 2),
                "handles": handles,
                "threads": threads,
                "disk_io_rate": round(disk_total, 2),
                "network_io_rate": round(net_total, 2),
            })

        by_cpu = sorted(rows, key=lambda item: item["cpu_percent"], reverse=True)
        by_memory = sorted(rows, key=lambda item: item["rss_mb"], reverse=True)
        by_handles = sorted(rows, key=lambda item: item["handles"], reverse=True)
        by_disk = sorted(rows, key=lambda item: item["disk_io_rate"], reverse=True)
        by_network = sorted(rows, key=lambda item: item["network_io_rate"], reverse=True)

        return {
            "process": by_cpu[:10],
            "process_memory": by_memory[:10],
            "process_handle": by_handles[:10],
            "process_diskio": by_disk[:10],
            "process_netio": by_network[:10],
            "keyprocess": self.key_process,
            "source": "synthetic-declared-process-pool",
        }

    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        self.cpu = self._continuous_walk(self.cpu, self.cpu_cfg)
        self.memory = self._continuous_walk(self.memory, self.mem_cfg)
        self.disk = self._continuous_walk(self.disk, self.disk_cfg)
        network = await asyncio.to_thread(self._collect_network)
        process_snapshot = self._process_snapshot()
        self._save_runtime_state()

        return TelemetrySnapshot(
            observed_at=clock.now().isoformat(),
            provider=self.name,
            metrics={
                "cpu": self._cpu_snapshot(),
                "memory": self._memory_snapshot(),
                "disk_io": self._disk_snapshot(),
                "network_io": network,
                "process_snapshot": process_snapshot,
            },
            metadata={
                "profile": str(self.path),
                "mode": "synthetic-system-with-live-network-rate",
                "live_scope": "aggregate_network_rate_only",
            },
        )


class LiveSystemProvider(BaseMetricsProvider):
    """Collect live host metrics through psutil for diagnostics only."""

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
