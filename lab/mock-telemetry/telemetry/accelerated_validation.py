from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
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
        self.ecology_random = random.Random(self.run_seed ^ 0x3C6EF372FE94F82B)
        self.timestamp_random = random.Random(self.run_seed ^ 0x6A09E667F3BCC909)
        self.pair_timestamp_random = random.Random(self.run_seed ^ 0xBB67AE8584CAA73B)
        self.disk_zero_random = random.Random(self.run_seed ^ 0x510E527FADE682D1)
        self._timestamp_phase = self.timestamp_random.random()
        self._timestamp_velocity_center = self.timestamp_random.uniform(0.00060, 0.00073)
        self._timestamp_velocity = self._timestamp_velocity_center
        self._timestamp_origin: datetime | None = None
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
        self._core_biases = [self.random.gauss(0.0, 0.24) for _ in self.cpu_cores]
        self._hot_cores = {self.random.randrange(len(self.cpu_cores))}
        self._hot_core_deadline = self.random.randint(600, 1800)
        self.memory = max(30.0, min(38.0, self._memory_target + self.random.gauss(-3.0, 1.2)))
        self._pool_regimes = (
            (128.0, 136.0), (185.0, 150.0), (300.0, 160.0),
            (330.0, 175.0), (420.0, 225.0),
        )
        self._pool_regime_index = self.random.randrange(len(self._pool_regimes))
        pool_center = self._pool_regimes[self._pool_regime_index]
        self._paged_pool_target = pool_center[0] + self.random.gauss(0.0, 8.0)
        self._nonpaged_pool_target = pool_center[1] + self.random.gauss(0.0, 5.0)
        self.paged_pool = self._paged_pool_target + self.random.gauss(0.0, 3.0)
        self.nonpaged_pool = self._nonpaged_pool_target + self.random.gauss(0.0, 2.0)
        self._pool_regime_deadline = self.random.randint(1500, 4200)
        self.net_tx = 0.2
        self.net_rx = 0.3
        self.disk_events = {
            str(item["name"]): {
                "read": self.random.uniform(0.03, 0.25),
                "write": self.random.uniform(1.7, 3.8),
                "latency": self.random.uniform(0.08, 0.35),
                "queue": self.random.uniform(0.01, 0.10),
                "service_capacity": (
                    self.disk_zero_random.uniform(6.0, 7.0)
                    if str(item["name"]) == "C"
                    else self.disk_zero_random.uniform(0.95, 1.70)
                ),
            }
            for item in shape["disk_layout"]
        }
        self.proc_cpu: dict[str, float] = {}
        self.proc_disk: dict[str, float] = {}
        self.proc_net: dict[str, float] = {}
        self.proc_rss: dict[str, float] = {}
        self.proc_handles: dict[str, float] = {}
        self.multi_instance_specs = (
            {
                "name": "msedgewebview2", "pid_base": 9400,
                "rss_mb": 112.0, "handles": 180, "cpu_weight": 0.28,
                "disk_weight": 0.13, "net_baseline": 15.0,
                "min_instances": 2, "max_instances": 3,
                "lifetime_min": 900.0, "lifetime_max": 2800.0,
            },
            {
                "name": "svchost", "pid_base": 1100,
                "rss_mb": 30.0, "handles": 1000, "cpu_weight": 0.30,
                "disk_weight": 0.18, "net_baseline": 30.0,
                "min_instances": 1, "max_instances": 1,
                "lifetime_min": 1200.0, "lifetime_max": 3600.0,
            },
            {
                "name": "RuntimeBroker", "pid_base": 6500,
                "rss_mb": 48.0, "handles": 160, "cpu_weight": 0.18,
                "disk_weight": 0.09, "net_baseline": 4.0,
                "min_instances": 1, "max_instances": 2,
                "lifetime_min": 700.0, "lifetime_max": 2400.0,
            },
        )
        self.multi_processes: dict[str, dict[str, Any]] = {}
        self._multi_generation = 0
        self._multi_targets = {
            str(spec["name"]): self.random.randint(
                int(spec["min_instances"]), int(spec["max_instances"])
            )
            for spec in self.multi_instance_specs
        }
        self.ephemeral_catalog = (
            {"name": "SearchApp", "pid_base": 7980, "rss_mb": 164.0, "handles": 1120, "cpu_weight": 0.18, "disk_weight": 0.01, "net_baseline": 4.0, "lifetime": "medium"},
            {"name": "RuntimeBroker", "pid_base": 6840, "rss_mb": 42.0, "handles": 410, "cpu_weight": 0.24, "disk_weight": 0.015, "net_baseline": 6.0, "lifetime": "medium"},
            {"name": "CompatTelRunner", "pid_base": 3788, "rss_mb": 18.0, "handles": 150, "cpu_weight": 0.3, "disk_weight": 0.025, "net_baseline": 2.0, "lifetime": "short"},
            {"name": "msedgewebview2", "pid_base": 9600, "rss_mb": 118.0, "handles": 720, "cpu_weight": 0.5, "disk_weight": 0.08, "net_baseline": 45.0, "lifetime": "medium"},
            {"name": "msedgewebview2", "pid_base": 10100, "rss_mb": 76.0, "handles": 430, "cpu_weight": 0.42, "disk_weight": 0.05, "net_baseline": 28.0, "lifetime": "short"},
            {"name": "ApplicationFrameHost", "pid_base": 9500, "rss_mb": 84.0, "handles": 330, "cpu_weight": 0.32, "disk_weight": 0.025, "net_baseline": 10.0, "lifetime": "medium", "memory_candidate": True},
            {"name": "dllhost", "pid_base": 6100, "rss_mb": 24.0, "handles": 260, "cpu_weight": 0.28, "disk_weight": 0.035, "net_baseline": 3.0, "lifetime": "short"},
            {"name": "conhost", "pid_base": 7400, "rss_mb": 12.0, "handles": 140, "cpu_weight": 0.26, "disk_weight": 0.02, "net_baseline": 2.0, "lifetime": "short"},
            {"name": "taskhostw", "pid_base": 5600, "rss_mb": 31.0, "handles": 310, "cpu_weight": 0.22, "disk_weight": 0.02, "net_baseline": 4.0, "lifetime": "medium"},
            {"name": "ShellExperienceHost", "pid_base": 10200, "rss_mb": 92.0, "handles": 880, "cpu_weight": 0.34, "disk_weight": 0.03, "net_baseline": 8.0, "lifetime": "medium", "memory_candidate": True},
        )
        self.ephemeral_processes: dict[str, dict[str, Any]] = {}
        self._ephemeral_generation = 0
        self._next_ephemeral_attempt = self.random.uniform(120.0, 360.0)
        self._started_at: float | None = None
        self._step = 0
        self.behavior_state = "IDLE"
        self._state_deadline = self.random.uniform(180.0, 620.0)
        self._cpu_impulse = 0.0
        for spec in self.multi_instance_specs:
            for _ in range(self._multi_targets[str(spec["name"])]):
                self._spawn_multi_instance(spec, 0.0)

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

    def _spawn_multi_instance(self, spec: dict[str, Any], elapsed: float) -> None:
        self._multi_generation += 1
        generation = self._multi_generation
        identity = f"multi:{spec['name']}:{generation}"
        item = {
            "name": str(spec["name"]),
            "pid_base": int(spec["pid_base"]),
            "rss_mb": float(spec["rss_mb"]) * self.random.uniform(0.82, 1.20),
            "handles": round(float(spec["handles"]) * self.random.uniform(0.88, 1.13)),
            "cpu_weight": float(spec["cpu_weight"]) * self.random.uniform(0.78, 1.22),
            "disk_weight": float(spec["disk_weight"]) * self.random.uniform(0.75, 1.28),
            "net_baseline": float(spec["net_baseline"]) * self.random.uniform(0.72, 1.32),
        }
        self.multi_processes[identity] = {
            "template": item,
            "family": str(spec["name"]),
            "deadline": elapsed + self.random.uniform(
                float(spec["lifetime_min"]), float(spec["lifetime_max"])
            ),
            "generation": generation,
        }
        self.proc_cpu[identity] = self.random.uniform(0.05, 0.80)
        disk_baseline = float(item["disk_weight"]) * 100_000.0
        self.proc_disk[identity] = disk_baseline * self.random.uniform(0.72, 1.38)
        net_baseline = float(item["net_baseline"])
        self.proc_net[identity] = net_baseline * self.random.uniform(0.65, 1.45)

    def _observed_time(self, sample_index: int) -> datetime:
        if self._timestamp_origin is None:
            raise RuntimeError("accelerated timestamp origin is not initialized")
        if sample_index:
            self._timestamp_velocity_center = max(
                0.00052,
                min(
                    0.00080,
                    self._timestamp_velocity_center + self.timestamp_random.gauss(0.0, 0.0000002),
                ),
            )
            self._timestamp_velocity += (
                (self._timestamp_velocity_center - self._timestamp_velocity) * 0.0025
                + self.timestamp_random.gauss(0.0, 0.0000035)
            )
            self._timestamp_velocity = max(0.00042, min(0.00090, self._timestamp_velocity))
            self._timestamp_phase = (self._timestamp_phase + self._timestamp_velocity) % 1.0
        return self._timestamp_origin + timedelta(
            seconds=sample_index + self._timestamp_phase
        )

    def _update_system(self, state: str) -> None:
        if self.random.random() < 0.012:
            self._cpu_target = self._sample_cpu_target()
        memory_refresh_probability = 0.0050 if self._memory_target >= 50.0 else 0.0060
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
        cpu_noise = 0.13 + cpu_target * 0.006
        if self._step >= self._hot_core_deadline:
            count = 2 if self.random.random() < 0.24 else 1
            self._hot_cores = set(self.random.sample(range(len(self.cpu_cores)), count))
            self._hot_core_deadline = self._step + self.random.randint(600, 1800)
        self._core_biases = [
            bias * 0.9997 + self.random.gauss(0.0, 0.0010)
            for bias in self._core_biases
        ]
        factors = [
            max(0.60, 1.0 + bias + (0.15 if index in self._hot_cores else 0.0))
            for index, bias in enumerate(self._core_biases)
        ]
        factor_mean = sum(factors) / len(factors)
        for index, value in enumerate(self.cpu_cores):
            target = cpu_target * factors[index] / factor_mean
            proposal = self._approach(value, target, 0.050, cpu_noise)
            self.cpu_cores[index] = max(
                2.75, self._reflect_soft_upper(proposal, self._cpu_soft_ceiling)
            )

        memory_proposal = self._approach(self.memory, self._memory_target, 0.0060, 0.018)
        self.memory = max(
            29.8, self._reflect_soft_upper(memory_proposal, self._memory_soft_ceiling)
        )
        if self._step >= self._pool_regime_deadline:
            candidates = [
                index for index in range(len(self._pool_regimes))
                if index != self._pool_regime_index
            ]
            self._pool_regime_index = self.random.choice(candidates)
            pool_center = self._pool_regimes[self._pool_regime_index]
            self._paged_pool_target = pool_center[0] + self.random.gauss(0.0, 10.0)
            self._nonpaged_pool_target = pool_center[1] + self.random.gauss(0.0, 6.0)
            self._pool_regime_deadline = self._step + self.random.randint(1800, 4800)
        self.paged_pool = max(105.0, min(550.0, self._approach(
            self.paged_pool, self._paged_pool_target, 0.0015, 0.035
        )))
        self.nonpaged_pool = max(115.0, min(260.0, self._approach(
            self.nonpaged_pool, self._nonpaged_pool_target, 0.0015, 0.025
        )))

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

        read_probability = {"IDLE": 0.00015, "LIGHT": 0.00045, "NORMAL": 0.0008, "ACTIVE": 0.0018, "SHORT_BURST": 0.004}[state]
        write_probability = {"IDLE": 0.0005, "LIGHT": 0.0012, "NORMAL": 0.0022, "ACTIVE": 0.004, "SHORT_BURST": 0.006}[state]
        write_baseline = {"IDLE": 2.2, "LIGHT": 3.5, "NORMAL": 5.5, "ACTIVE": 8.0, "SHORT_BURST": 10.0}[state]
        for name, disk_state in self.disk_events.items():
            scale = 1.0 if name == "C" else 0.24
            disk_state["read"] = self._approach(disk_state["read"], 0.12 * scale, 0.018, 0.012 * scale)
            disk_state["write"] = self._approach(disk_state["write"], write_baseline * scale, 0.012, 0.035 * scale)
            if self.random.random() < read_probability:
                disk_state["read"] += self.random.uniform(2.0, 45.0) * scale
            if self.random.random() < write_probability:
                disk_state["write"] += self.random.uniform(3.0, 70.0) * scale
            latency_factor = self.random.uniform(0.025, 0.09)
            queue_factor = self.random.uniform(0.008, 0.035)
            latency_noise = self.random.gauss(0.0, 0.025)
            queue_noise = self.random.gauss(0.0, 0.012)
            active_ops = max(0.0, disk_state["read"]) + max(0.0, disk_state["write"])
            pending = max(0.0, active_ops - disk_state["service_capacity"])
            queue_pending = max(
                0.0,
                pending - disk_state["service_capacity"] * 0.02,
            )
            if pending == 0.0:
                disk_state["latency"] = 0.0
            else:
                latency_target = 0.08 + min(
                    7.0, pending * latency_factor * 2.0
                )
                disk_state["latency"] = max(
                    0.0,
                    disk_state["latency"]
                    + (latency_target - disk_state["latency"]) * 0.11
                    + latency_noise,
                )
            if queue_pending == 0.0:
                disk_state["queue"] = 0.0
            else:
                queue_target = 0.01 + min(
                    3.5, queue_pending * queue_factor * 2.2
                )
                disk_state["queue"] = max(
                    0.0,
                    disk_state["queue"]
                    + (queue_target - disk_state["queue"]) * 0.11
                    + queue_noise,
                )

    def _process_snapshot(self, state: str, elapsed: float) -> dict[str, Any]:
        rows = []
        long_lived_rows = []
        memory_rows = []
        activity_chance = {
            "IDLE": 0.00005,
            "LIGHT": 0.0001,
            "NORMAL": 0.00018,
            "ACTIVE": 0.0004,
            "SHORT_BURST": 0.0012,
        }[state]
        cpu_total = sum(self.cpu_cores) / len(self.cpu_cores)
        expired = [
            identity for identity, active in self.ephemeral_processes.items()
            if elapsed >= float(active["deadline"])
        ]
        for identity in expired:
            self.ephemeral_processes.pop(identity, None)
            self.proc_cpu.pop(identity, None)
            self.proc_disk.pop(identity, None)
            self.proc_net.pop(identity, None)
            self.proc_rss.pop(identity, None)
            self.proc_handles.pop(identity, None)
        expired_multi = [
            identity for identity, active in self.multi_processes.items()
            if elapsed >= float(active["deadline"])
        ]
        for identity in expired_multi:
            self.multi_processes.pop(identity, None)
            self.proc_cpu.pop(identity, None)
            self.proc_disk.pop(identity, None)
            self.proc_net.pop(identity, None)
            self.proc_rss.pop(identity, None)
            self.proc_handles.pop(identity, None)
        for spec in self.multi_instance_specs:
            family = str(spec["name"])
            active_count = sum(
                active["family"] == family for active in self.multi_processes.values()
            )
            for _ in range(max(0, self._multi_targets[family] - active_count)):
                self._spawn_multi_instance(spec, elapsed)
        if elapsed >= self._next_ephemeral_attempt:
            self._next_ephemeral_attempt = elapsed + self.random.uniform(120.0, 360.0)
            spawn_probability = {
                "IDLE": 0.38,
                "LIGHT": 0.58,
                "NORMAL": 0.72,
                "ACTIVE": 0.84,
                "SHORT_BURST": 0.9,
            }[state]
            available = [
                item for item in self.ephemeral_catalog
                if sum(
                    active["template"]["name"] == item["name"]
                    for active in self.ephemeral_processes.values()
                ) < 2
            ]
            if available and len(self.ephemeral_processes) < 4 and self.random.random() < spawn_probability:
                item = dict(self.random.choice(available))
                self._ephemeral_generation += 1
                identity = f"episode:{item['name']}:{self._ephemeral_generation}"
                if item.get("lifetime") == "short":
                    duration = self.random.uniform(90.0, 360.0)
                else:
                    duration = self.random.uniform(360.0, 1100.0)
                self.ephemeral_processes[identity] = {
                    "template": item,
                    "deadline": elapsed + duration,
                    "generation": self._ephemeral_generation,
                    "memory_top10": bool(
                        item.get("memory_candidate")
                        and self.ecology_random.random() < 0.35
                    ),
                }
                self.proc_cpu[identity] = self.random.uniform(0.7, 3.8)
                self.proc_disk[identity] = (
                    float(item.get("disk_weight", 0.02)) * 100_000.0
                    + self.random.uniform(3_000.0, 85_000.0)
                )
                self.proc_net[identity] = (
                    float(item.get("net_baseline", 5.0))
                    + self.random.uniform(30.0, 1_400.0)
                )
        active_pool = [
            *((
                item,
                f"core:{str(item['name']).removesuffix('.exe')}",
                0,
                "core",
            ) for item in self.process_pool),
            *((
                active["template"],
                identity,
                int(active["generation"]),
                "multi",
            ) for identity, active in self.multi_processes.items()),
            *((
                active["template"],
                identity,
                int(active["generation"]),
                "episode",
            ) for identity, active in self.ephemeral_processes.items()),
        ]
        for index, (item, identity, generation, lifecycle) in enumerate(active_pool):
            name = str(item["name"]).removesuffix(".exe")
            pid_base = int(item.get("pid_base", 1000 + 100 * index)) + generation * 37
            pid = self.process_state.get_pid(identity, pid_base)
            self.proc_cpu[identity] = self.proc_cpu.get(identity, 0.05) * 0.9975
            disk_baseline = float(item.get("disk_weight", 0.02)) * 100_000.0
            net_baseline = float(item.get("net_baseline", 5.0))
            self.proc_disk[identity] = self.proc_disk.get(identity, disk_baseline) * 0.998 + disk_baseline * 0.002
            self.proc_net[identity] = self.proc_net.get(identity, net_baseline) * 0.997 + net_baseline * 0.003
            if self.random.random() < activity_chance:
                ceiling = 7.0 if state == "SHORT_BURST" else 4.2
                self.proc_cpu[identity] += self.random.uniform(0.6, ceiling)
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
                self.proc_disk[identity] += self.random.uniform(2_000.0, ceiling)
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
                self.proc_net[identity] += self.random.uniform(80.0, ceiling)
            baseline_cpu = cpu_total * float(item.get("cpu_weight", 0.2)) / 35.0
            if lifecycle == "episode":
                baseline_cpu *= 0.70
                self.proc_cpu[identity] *= 0.999
            elif float(item.get("handles", 200)) >= 800:
                baseline_cpu *= 0.30
                self.proc_cpu[identity] *= 0.998
            cpu = max(0.0, min(18.0, baseline_cpu + self.proc_cpu[identity] + self.random.uniform(0, 0.04)))
            if lifecycle == "episode":
                cpu = min(cpu, 3.50)
            elif lifecycle == "multi":
                cpu *= 0.60
            declared_handles = float(item.get("handles", 200))
            if declared_handles >= 800:
                cpu *= 0.07
            elif declared_handles >= 300:
                cpu *= 0.15
            cpu *= 0.42
            if lifecycle == "episode":
                cpu = min(cpu, 0.40)
            rss_target = float(item.get("rss_mb", 20.0)) * 1.90 * (1.0 + 0.012 * (self.memory - 15.0))
            self.proc_rss[identity] = self._approach(self.proc_rss.get(identity, rss_target), rss_target, 0.002, 0.006)
            rss_mb = max(0.15, self.proc_rss[identity])
            handle_target = float(item.get("handles", 200)) * 1.80
            self.proc_handles[identity] = self._approach(
                self.proc_handles.get(identity, handle_target),
                handle_target,
                0.01,
                0.08,
            )
            handles = max(10, round(self.proc_handles[identity]))
            disk_total = max(0.0, self.proc_disk[identity]) * 1.35
            disk_read = disk_total * self.random.uniform(0.15, 0.82)
            net_total = max(0.0, self.proc_net[identity])
            net_tx = net_total * self.random.uniform(0.18, 0.65)
            row = {
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
            }
            rows.append(row)
            if lifecycle != "episode":
                long_lived_rows.append(row)
                memory_rows.append(row)
            elif bool(self.ephemeral_processes.get(identity, {}).get("memory_top10", False)):
                memory_rows.append(row)
        return {
            "process": sorted(rows, key=lambda row: row["cpu_percent"], reverse=True)[:10],
            "process_memory": sorted(memory_rows, key=lambda row: row["rss_mb"], reverse=True)[:10],
            "process_handle": sorted(long_lived_rows, key=lambda row: row["handles"], reverse=True)[:10],
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
            disk_state = self.disk_events[str(item["name"])]
            used_gb = float(item.get("used_gb", float(item["size_gb"]) * float(item["used_percent"]) / 100.0))
            read_iops = max(0.0, disk_state["read"])
            write_iops = max(0.0, disk_state["write"])
            activity = read_iops + write_iops
            disks.append({
                "name": str(item["name"]), "size_gb": float(item["size_gb"]),
                "used_gb": used_gb, "used_percent": used_gb / float(item["size_gb"]) * 100.0,
                "activity_rate": activity,
                "read_iops": read_iops, "write_iops": write_iops,
                "read_kb_per_second": read_iops * self.random.uniform(24.0, 112.0),
                "write_kb_per_second": write_iops * self.random.uniform(10.0, 70.0),
                "read_latency_ms": disk_state["latency"] * self.random.uniform(0.75, 1.15),
                "write_latency_ms": disk_state["latency"] * self.random.uniform(0.85, 1.30),
                "queue_length": disk_state["queue"],
            })
        total_activity = sum(
            max(0.0, value["read"]) + max(0.0, value["write"])
            for value in self.disk_events.values()
        )
        return {"system_activity": 11.94, "activity_rate": total_activity, "per_disk": disks, "activity": total_activity, "source": "synthetic"}

    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        if self._started_at is None:
            self._started_at = clock.monotonic()
            self._timestamp_origin = clock.now() - timedelta(seconds=clock.monotonic())
        elapsed = clock.monotonic() - self._started_at
        sample_index = self._step
        state = self._state(elapsed)
        self._step += 1
        self._update_system(state)
        disk = self._disk_snapshot()
        process = self._process_snapshot(state, elapsed)
        return TelemetrySnapshot(
            observed_at=self._observed_time(sample_index).isoformat(), provider=self.name,
            metrics={
                "local_environment": dict(self.local_environment),
                "cpu": {"percent": round(sum(self.cpu_cores) / len(self.cpu_cores), 2), "per_core": [round(v, 2) for v in self.cpu_cores], "source": "synthetic"},
                "memory": {"percent": round(self.memory, 2), "paged_pool_mb": round(self.paged_pool, 1), "nonpaged_pool_mb": round(self.nonpaged_pool, 1), "source": "synthetic"},
                "network_io": {"tx_kb_per_second": round(self.net_tx, 3), "rx_kb_per_second": round(self.net_rx, 3), "tx_interval_kb": round(self.net_tx * self.random.uniform(48, 63), 2), "rx_interval_kb": round(self.net_rx * self.random.uniform(48, 63), 2), "source": "synthetic"},
                "disk_io": disk, "process_snapshot": process,
            },
            metadata={
                "mode": "accelerated-fully-synthetic",
                "behavior_state": state,
                "virtual_elapsed": elapsed,
                "run_seed": self.run_seed,
                "timestamp_phase_seconds": self._timestamp_phase,
                "process_pair_delay_seconds": 7.03 + max(
                    -0.012,
                    min(0.012, self.pair_timestamp_random.gauss(0.0, 0.005)),
                ),
            },
        )
