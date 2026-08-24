from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .agent import TelemetryAgent
from .config import build_clock, build_provider, build_settings, build_transport


class TelemetryRuntime:
    """High-level runtime facade built from generic configuration.

    The agent itself stays unaware of configuration syntax. This facade owns the
    translation from config objects to concrete providers/transports/clocks and
    can replace the active provider at runtime through the same registry-backed
    factory used during initialization.
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.agent = TelemetryAgent(
            provider=build_provider(self.config),
            transport=build_transport(self.config),
            clock=build_clock(self.config),
            settings=build_settings(self.config),
        )

    async def switch_provider(self, provider_config: Mapping[str, Any]) -> None:
        """Build and activate a new provider from a provider config object."""
        new_provider = build_provider({"provider": dict(provider_config)})
        await self.agent.set_provider(new_provider)
        self.config["provider"] = dict(provider_config)
        self.config.pop("profile", None)

    async def run(self) -> None:
        await self.agent.run()

    async def stop(self) -> None:
        await self.agent.stop()
