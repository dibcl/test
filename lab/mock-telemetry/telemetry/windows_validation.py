from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .clocks import BaseClock
from .local_env import load_local_environment
from .model import ProviderHealth, TelemetrySnapshot
from .providers import HybridSyntheticNetworkProvider


class WindowsValidationProvider(HybridSyntheticNetworkProvider):
    """Hybrid runtime plus the explicit current Windows machine baseline.

    Dynamic CPU/memory/disk/process data remains synthetic and host-isolated.
    Aggregate network throughput is the only live host metric. Identity and
    environment fields come from local_env.json exactly as declared so Windows
    validation logs can be compared with the captured Guest logs without any
    hostname/IP/MAC/OS discovery calls.
    """

    name = "windows-validation"

    def __init__(self, profile_path: str | Path, local_env_path: str | Path) -> None:
        super().__init__(profile_path)
        self.local_env_path = Path(local_env_path)
        self.local_environment = load_local_environment(self.local_env_path)

    async def health_check(self) -> ProviderHealth:
        base = await super().health_check()
        if not base.healthy:
            return base
        try:
            current = load_local_environment(self.local_env_path)
        except Exception as exc:
            return ProviderHealth(False, f"local environment invalid: {exc}")
        if current != self.local_environment:
            return ProviderHealth(False, "local environment changed after provider construction")
        metadata = dict(base.metadata)
        metadata.update(
            {
                "local_env": str(self.local_env_path),
                "environment_source": "declared-current-windows-machine",
            }
        )
        return ProviderHealth(True, "windows validation provider ready", metadata)

    async def snapshot(self, clock: BaseClock) -> TelemetrySnapshot:
        base = await super().snapshot(clock)
        metrics = json.loads(json.dumps(base.metrics))
        metrics["local_environment"] = dict(self.local_environment)
        metadata: dict[str, Any] = dict(base.metadata)
        metadata.update(
            {
                "local_env": str(self.local_env_path),
                "environment_source": "declared-current-windows-machine",
            }
        )
        return TelemetrySnapshot(
            observed_at=base.observed_at,
            provider=self.name,
            metrics=metrics,
            metadata=metadata,
        )
