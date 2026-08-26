from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from .clocks import BaseClock
from .local_env import load_local_environment
from .model import ProviderHealth, TelemetrySnapshot
from .process_state import ProcessIdentityState
from .providers import BaseMetricsProvider


class AcceleratedWindowsValidationProvider(BaseMetricsProvider):
    """Stochastic, host-isolated Windows telemetry with inertial dynamics."""

    name = "windows-validation-accelerated"

    def __init__(self, profile_path: str | Path, local_env_path: str | Path) -> None:
        self.path = Path(profile_path)
        self.local_env_path = Path(local_env_path)
        self.profile = json.loads(self.path.read_text(encoding="utf-8"))
        self.local_environment = load_local_environment(self.local_env_path)
        shape = self.profile["runtime_shape"]
        dynamics = self.profile["dynamics"]
        self.run_seed = random.SystemRandom().randrange(1, 2**63) ^ int(dynamics.get("seed", 1))
        self.random = random.Random(self.run_seed)
        self.process_pool = [dict(item) for item in dynamics["process_pool"]]
        self.process_state = ProcessIdentityState()
        self._cpu_target = self._sample_cpu_target()
        self._memory_target = self._sample_memory_target()
        self._cpu_soft_ceiling = self.random.uniform(57.0, 58.3)
        self._memory_soft_ceiling = self.random.uniform(57.0, 58.3)
        self.cpu_cores = [
            max(3.0, self._cpu_target + self.random.gauss(0.0, 1.1))
            for _ in range(int(shape["cpu_cores"]))
        ]
        self.memory = max(30.0, min(38.0, self._memory_target + self.random.gauss(-3.0, 1.2)))
        self.paged_pool = 146.7
        self.nonpaged_pool = 138.8
        self.net_tx = 0.2
        self.net_rx = 0.3
        self.disk_events = {str(item["name"]): 0.0 for item in shape["disk_layout"]}
        self.proc_cpu: dict[str, float] = {}
        self.proc_disk: dict[str, float] = {}
        self.proc_net: dict[str, float] = {}
        self.proc_rss: dict[str, float] = {}
        self.ephemeral_catalog = (
            {"name": "SearchApp", "pid_base": 7980, "rss_mb": 164.0, "handles": 1120, "cpu_weight": 0.18, "disk_weight": 0.01, "net_baseline": 4.0},
            {"name": "RuntimeBroker", "pid_base": 6840, "rss_mb": 42.0, "handles": 410, "cpu_weight": 0.24, "disk_weight": 0.015, "net_baseline": 6.0},
            {"name": "CompatTelRunner", "pid_base": 3788, "rss_mb": 18.0, "handles": 150, "cpu_weight": 0.3, "disk_weight": 0.025, "net_baseline": 2.0},
        )
        self.ephemeral_processes: dict[str, tuple[dict[str, Any], float]] = {}
        self._next_ephemeral_attempt = self.random.uniform(420.0, 1200.0)
        self._started_at: float | None = None
        self._step = 0
        self.behavior_state = "IDLE"
        self._state_deadline = self.random.uniform(180.0, 620.0)
        self._cpu_impulse = 0.0

    async def health_check(self) -> ProviderHealth:
        if not self.path.is_file() or not self.local_env_path.is_file():
            return ProviderHealth(False, "accelerated fixture missing")
        return ProviderHealth(
            True,
            "fully synthetic accelerated provider ready",
            {"live_inputs": [], "profile": str(self.path)},
        )

    def _truncated_gauss(self, mean: float, sigma: float, low: float, high: float) -> float:
        for _ in range(32):
            value = self.random.gauss(mean, sigma)
            if low < value < high:
                return value
        return self.random.uniform(low + 0.05, high - 0.05)

    def _sample_cpu_target(self) -> float:
        component = self.random.choices(
            ((21.0, 7.5), (34.5, 4.3), (44.5, 4.5), (54.0, 2.7)),
            weights=(0.13, 0.56, 0.21, 0.10),
            k=1,
        )[0]
        return self._truncated_gauss(*component, 3.0, 58.5)

    def _sample_memory_target(self) -> float:
        component = self.random.choices(
            ((31.5, 2.2), (35.0, 3.6), (44.0, 4.2), (54.0, 2.7)),
            weights=(0.15, 0.50, 0.20, 0.15),
            k=1,
        )[0]
        return self._truncated_gauss(*component, 30.0, 58.5)

    def _state(self, elapsed: float) -> str:
        durations = {
            "IDLE": (180.0, 720.0),
            "LIGHT": (150.0, 600.0),
            "NORMAL": (180.0, 720.0),
            "ACTIVE": (90.0, 420.0),
            "SHORT_BURST": (18.0, 85.0),
        }
        transitions = {
            "IDLE": (("LIGHT", 0.76), ("NORMAL", 0.18), ("SHORT_BURST", 0.06)),
            "LIGHT": (("IDLE", 0.24), ("NORMAL", 0.53), ("ACTIVE", 0.14), ("SHORT_BURST", 0.09)),
            "NORMAL": (("LIGHT", 0.31), ("ACTIVE", 0.43), ("IDLE", 0.10), ("SHORT_BURST", 0.16)),
            "ACTIVE": (("NORMAL", 0.51), ("LIGHT", 0.22), ("SHORT_BURST", 0.22), ("IDLE", 0.05)),
            "SHORT_BURST": (("LIGHT", 0.35), ("NORMAL", 0.50), ("ACTIVE", 0.15)),
        }
        if elapsed >= self._state_deadline:
            choices = transitions[self.behavior_state]
            self.behavior_state = self.random.choices(
                [item[0] for item in choices], weights=[item[1] for item in choices], k=1
            )[0]
            self._state_deadline = elapsed + self.random.uniform(*durations[self.behavior_state])
        return self.behavior_state

    def _approach(self, value: float, target: float, rate: float, noise: float) -> float:
        return value + (target - value) * rate + self.random.gauss(0.0, noise)

    def _reflect_soft_upper(self, value: float, upper: float) -> float:
        if value <= upper:
            return value
        return upper - self.random.uniform(0.25, 1.5) - (value - upper) * self.random.uniform(0.3, 0.9)

    def _wander_soft_ceiling(self, value: float) -> float:
        proposal = value + self.random.gauss(0.0, 0.012)
        if not 57.0 < proposal < 58.35:
            return self.random.uniform(57.1, 58.25)
        return proposal

    def _update_system(self, state: str) -> None:
        if self.random.random() < 0.004:
            self._cpu_target = self._sample_cpu_target()
        memory_refresh_probability = 0.001 if self._memory_target >= 50.0 else 0.0012
        if self.random.random() < memory_refresh_probability:
            self._memory_target = self._sample_memory_target()
        if self.random.random() < 0.00075:
            self._cpu_impulse += self.random.uniform(7.0, 20.0)
        self._cpu_impulse *= 0.92
        raw_cpu_target = self._cpu_target + self._cpu_impulse
        self._cpu_soft_ceiling = self._wander_soft_ceiling(self._cpu_soft_ceiling)
        self._memory_soft_ceiling = self._wander_soft_ceiling(self._memory_soft_ceiling)
        cpu_target = raw_cpu_target
        if raw_cpu_target > 58.5:
            cpu_target = (
                58.5
                - self.random.uniform(0.35, 1.5)
                - min(4.0, (raw_cpu_target - 58.5) * 0.25)
            )
        cpu_noise = 0.08 + cpu_target * 0.0045
        hot_core = (self._step // 37) % len(self.cpu_cores)
        for index, value in enumerate(self.cpu_cores):
            factor = 0.65 + ((index * 37 + self._step // 53) % 9) * 0.085
            target = cpu_target * factor
            if cpu_target > 47.0 and index in {hot_core, (hot_core + 3) % len(self.cpu_cores)}:
                target *= 1.12
            proposal = self._approach(value, target, 0.025, cpu_noise)
            self.cpu_cores[index] = max(
                2.75, self._reflect_soft_upper(proposal, self._cpu_soft_ceiling)
            )

        memory_proposal = self._approach(self.memory, self._memory_target, 0.002, 0.0045)
        self.memory = max(
            29.8, self._reflect_soft_upper(memory_proposal, self._memory_soft_ceiling)
        )
        self.paged_pool = max(142.0, min(168.0, self._approach(self.paged_pool, 144.0 + self.memory * 0.22, 0.003, 0.012)))
        self.nonpaged_pool = max(134.0, min(158.0, self._approach(self.nonpaged_pool, 134.5 + self.memory * 0.19, 0.003, 0.010)))

        tx_target, rx_target = {
            "IDLE": (0.07, 0.12),
            "LIGHT": (0.18, 0.35),
            "NORMAL": (0.34, 0.72),
            "ACTIVE": (0.65, 1.5),
            "SHORT_BURST": (3.8, 9.0),
        }[state]
        if state == "SHORT_BURST" and self.random.random() < 0.004:
            tx_target += self.random.uniform(4.0, 14.0)
            rx_target += self.random.uniform(9.0, 30.0)
        self.net_tx = max(0.0, self._approach(self.net_tx, tx_target, 0.025, 0.025))
        self.net_rx = max(0.0, self._approach(self.net_rx, rx_target, 0.025, 0.035))

        event_probability = {"IDLE": 0.0002, "LIGHT": 0.0006, "NORMAL": 0.001, "ACTIVE": 0.0025, "SHORT_BURST": 0.005}[state]
        for name in self.disk_events:
            self.disk_events[name] *= 0.968
            if self.random.random() < event_probability:
                scale = 1.0 if name == "C" else 0.28
                ceiling = {"IDLE": 3.0, "LIGHT": 16.0, "NORMAL": 32.0, "ACTIVE": 75.0, "SHORT_BURST": 150.0}[state]
                self.disk_events[name] += self.random.uniform(2.0, ceiling) * scale

    def _process_snapshot(self, state: str, elapsed: float) -> dict[str, Any]:
        rows = []
        activity_chance = {
            "IDLE": 0.00005,
            "LIGHT": 0.0001,
            "NORMAL": 0.00018,
            "ACTIVE": 0.0004,
            "SHORT_BURST": 0.0012,
        }[state]
        cpu_total = sum(self.cpu_cores) / len(self.cpu_cores)
        expired = [name for name, (_, deadline) in self.ephemeral_processes.items() if elapsed >= deadline]
        for name in expired:
            self.ephemeral_processes.pop(name, None)
            self.proc_cpu.pop(name, None)
            self.proc_disk.pop(name, None)
            self.proc_net.pop(name, None)
            self.proc_rss.pop(name, None)
        if elapsed >= self._next_ephemeral_attempt:
            self._next_ephemeral_attempt = elapsed + self.random.uniform(420.0, 1200.0)
            spawn_probability = {
                "IDLE": 0.2,
                "LIGHT": 0.38,
                "NORMAL": 0.55,
                "ACTIVE": 0.7,
                "SHORT_BURST": 0.75,
            }[state]
            available = [item for item in self.ephemeral_catalog if item["name"] not in self.ephemeral_processes]
            if available and len(self.ephemeral_processes) < 2 and self.random.random() < spawn_probability:
                item = dict(self.random.choice(available))
                duration = self.random.uniform(180.0, 660.0)
                self.ephemeral_processes[str(item["name"])] = (item, elapsed + duration)
        active_pool = [
            *self.process_pool,
            *(item for item, _ in self.ephemeral_processes.values()),
        ]
        for index, item in enumerate(active_pool):
            name = str(item["name"]).removesuffix(".exe")
            pid = self.process_state.get_pid(name, int(item.get("pid_base", 1000 + 100 * index)))
            self.proc_cpu[name] = self.proc_cpu.get(name, 0.05) * 0.9975
            disk_baseline = float(item.get("disk_weight", 0.02)) * 100_000.0
            net_baseline = float(item.get("net_baseline", 5.0))
            self.proc_disk[name] = self.proc_disk.get(name, disk_baseline) * 0.998 + disk_baseline * 0.002
            self.proc_net[name] = self.proc_net.get(name, net_baseline) * 0.997 + net_baseline * 0.003
            if self.random.random() < activity_chance:
                ceiling = 7.0 if state == "SHORT_BURST" else 4.2
                self.proc_cpu[name] += self.random.uniform(0.6, ceiling)
            disk_chance = {
                "IDLE": 0.00001,
                "LIGHT": 0.000025,
                "NORMAL": 0.00005,
                "ACTIVE": 0.00012,
                "SHORT_BURST": 0.0003,
            }[state]
            if self.random.random() < disk_chance:
                ceiling = {
                    "IDLE": 8_000.0,
                    "LIGHT": 40_000.0,
                    "NORMAL": 100_000.0,
                    "ACTIVE": 180_000.0,
                    "SHORT_BURST": 260_000.0,
                }[state]
                self.proc_disk[name] += self.random.uniform(2_000.0, ceiling)
            net_chance = {
                "IDLE": 0.00001,
                "LIGHT": 0.00003,
                "NORMAL": 0.00006,
                "ACTIVE": 0.00015,
                "SHORT_BURST": 0.0004,
            }[state]
            if self.random.random() < net_chance:
                ceiling = {
                    "IDLE": 180.0,
                    "LIGHT": 500.0,
                    "NORMAL": 900.0,
                    "ACTIVE": 2_500.0,
                    "SHORT_BURST": 5_000.0,
                }[state]
                self.proc_net[name] += self.random.uniform(80.0, ceiling)
            baseline_cpu = cpu_total * float(item.get("cpu_weight", 0.2)) / 35.0
            cpu = max(0.0, min(18.0, baseline_cpu + self.proc_cpu[name] + self.random.uniform(0, 0.04)))
            rss_target = float(item.get("rss_mb", 20.0)) * (1.0 + 0.012 * (self.memory - 15.0))
            self.proc_rss[name] = self._approach(self.proc_rss.get(name, rss_target), rss_target, 0.002, 0.006)
            rss_mb = max(0.15, self.proc_rss[name])
            handles = max(10, int(item.get("handles", 200)) + self.random.randint(-12, 12))
            disk_total = max(0.0, self.proc_disk[name])
            disk_read = disk_total * self.random.uniform(0.15, 0.82)
            net_total = max(0.0, self.proc_net[name])
            net_tx = net_total * self.random.uniform(0.18, 0.65)
            rows.append({
                "name": name,
                "pid": pid,
                "cpu_percent": round(cpu, 3),
                "rss_mb": round(rss_mb, 3),
                "handles": handles,
                "disk_io_rate": round(disk_total, 2),
                "disk_read_rate": round(disk_read, 2),
                "disk_write_rate": round(disk_total - disk_read, 2),
                "network_io_rate": round(net_total, 2),
                "network_tx_rate": round(net_tx, 2),
                "network_rx_rate": round(net_total - net_tx, 2),
            })
        return {
            "process": sorted(rows, key=lambda row: row["cpu_percent"], reverse=True)[:10],
            "process_memory": sorted(rows, key=lambda row: row["rss_mb"], reverse=True)[:10],
            "process_handle": sorted(rows, key=lambda row: row["handles"], reverse=True)[:10],
            "process_diskio": sorted(rows, key=lambda row: row["disk_io_rate"], reverse=True)[:10],
            "process_netio": sorted(rows, key=lambda row: row["network_io_rate"], reverse=True)[:10],
            "system_handles": int(58_500 + self.memory * 85 + self.random.gauss(0, 240)),
            "keyprocess": str(self.profile["runtime_shape"].get("key_process", "MMRHookService.exe")),
            "source": "synthetic-state-machine",
        }

    def _disk_snapshot(self) -> dict[str, Any]:
        layout = self.profile["runtime_shape"]["disk_layout"]
        disks = []
        for item in layout:
            activity = self.disk_events[str(item["name"])]
            used_gb = float(item.get("used_gb", float(item["size_gb"]) * float(item["used_percent"]) / 100.0))
            read_iops = activity * self.random.uniform(0.02, 0.16)
            write_iops = activity * self.random.uniform(0.02, 0.22)
            disks.append({
                "name": str(item["name"]), "size_gb": float(item["size_gb"]),
                "used_gb": used_gb, "used_percent": used_gb / float(item["size_gb"]) * 100.0,
                "activity_rate": activity,
                "read_iops": read_iops, "write_iops": write_iops,
                "read_kb_per_second": activity * self.random.uniform(0.4, 4.5),
                "write_kb_per_second": activity * self.random.uniform(0.4, 5.5),
                "read_latency_ms": min(8.0, activity * 0.003),
                "write_latency_ms": min(8.0, activity * 0.004),
                "queue_length": min(4.0, activity * 0.0015),
            })
        total_activity = sum(self.disk_events.values())
        return {"system_activity": 11.94, "activity_rate": total_activity, "per_disk": disks, "activity": total_activity, "source": "synthetic"}

    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        if self._started_at is None:
            self._started_at = clock.monotonic()
        elapsed = clock.monotonic() - self._started_at
        state = self._state(elapsed)
        self._step += 1
        self._update_system(state)
        disk = self._disk_snapshot()
        process = self._process_snapshot(state, elapsed)
        return TelemetrySnapshot(
            observed_at=clock.now().isoformat(), provider=self.name,
            metrics={
                "local_environment": dict(self.local_environment),
                "cpu": {"percent": round(sum(self.cpu_cores) / len(self.cpu_cores), 2), "per_core": [round(v, 2) for v in self.cpu_cores], "source": "synthetic"},
                "memory": {"percent": round(self.memory, 2), "paged_pool_mb": round(self.paged_pool, 1), "nonpaged_pool_mb": round(self.nonpaged_pool, 1), "source": "synthetic"},
                "network_io": {"tx_kb_per_second": round(self.net_tx, 3), "rx_kb_per_second": round(self.net_rx, 3), "tx_interval_kb": round(self.net_tx * self.random.uniform(48, 63), 2), "rx_interval_kb": round(self.net_rx * self.random.uniform(48, 63), 2), "source": "synthetic"},
                "disk_io": disk, "process_snapshot": process,
            },
            metadata={"mode": "accelerated-fully-synthetic", "behavior_state": state, "virtual_elapsed": elapsed, "run_seed": self.run_seed},
        )
