from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProcessIdentityState:
    """Keeps synthetic process identity stable across telemetry samples.

    The telemetry model should behave like repeated observations from one
    machine instead of generating unrelated process identities every cycle.
    """

    pid_map: dict[str, int] = field(default_factory=dict)

    def get_pid(self, name: str, default_pid: int) -> int:
        if name not in self.pid_map:
            self.pid_map[name] = default_pid
        return self.pid_map[name]

    def snapshot(self) -> dict[str, Any]:
        return {"process_pid_map": dict(self.pid_map)}
