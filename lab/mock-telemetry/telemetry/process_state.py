from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProcessIdentityState:
    """Keeps synthetic process identity stable across telemetry samples.

    The telemetry model should behave like repeated observations from one
    machine instead of generating unrelated process identities every cycle.
    """

    pid_map: dict[str, int] = field(default_factory=dict)
    dynamic_metrics: dict[str, float] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None) -> "ProcessIdentityState":
        if path is None:
            return cls()
        source = Path(path)
        if not source.exists():
            return cls()
        value = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("runtime state root must be an object")

        raw_pid_map = value.get("process_pid_map", {})
        raw_metrics = value.get("dynamic_metrics", {})
        if not isinstance(raw_pid_map, dict) or not isinstance(raw_metrics, dict):
            raise ValueError("runtime state maps must be objects")

        pid_map: dict[str, int] = {}
        for name, pid in raw_pid_map.items():
            if not isinstance(name, str) or isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                raise ValueError("runtime process PID map is invalid")
            pid_map[name] = pid

        dynamic_metrics: dict[str, float] = {}
        for name in ("cpu", "memory", "disk_io"):
            if name not in raw_metrics:
                continue
            value = raw_metrics[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"runtime dynamic metric is invalid: {name}")
            dynamic_metrics[name] = float(value)
        return cls(pid_map=pid_map, dynamic_metrics=dynamic_metrics)

    def get_pid(self, name: str, default_pid: int) -> int:
        if name not in self.pid_map:
            self.pid_map[name] = default_pid
        return self.pid_map[name]

    def update_dynamic_metrics(self, *, cpu: float, memory: float, disk_io: float) -> None:
        self.dynamic_metrics.update(cpu=cpu, memory=memory, disk_io=disk_io)

    def save(self, path: str | Path | None) -> None:
        if path is None:
            return
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.snapshot(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)

    def snapshot(self) -> dict[str, Any]:
        return {
            "process_pid_map": dict(self.pid_map),
            "dynamic_metrics": dict(self.dynamic_metrics),
        }
