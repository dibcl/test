from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class TelemetrySnapshot:
    """Provider-neutral observation returned to the telemetry agent."""

    observed_at: str
    provider: str
    metrics: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.observed_at:
            raise ValueError("observed_at must be non-empty")
        if not self.provider:
            raise ValueError("provider must be non-empty")
        if not isinstance(self.metrics, dict):
            raise TypeError("metrics must be a dict")
        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def to_envelope(self, schema_version: int = 1) -> dict[str, Any]:
        self.validate()
        if schema_version < 1:
            raise ValueError("schema_version must be >= 1")
        return {
            "schema_version": schema_version,
            "observed_at": self.observed_at,
            "provider": self.provider,
            "metrics": self.metrics,
            "metadata": self.metadata,
        }
