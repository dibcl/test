from __future__ import annotations

import argparse
import asyncio

from telemetry.agent import TelemetryAgent
from telemetry.config import build_clock, build_provider, build_settings, build_transport, load_config


async def async_main(config_path: str) -> None:
    cfg = load_config(config_path)
    agent = TelemetryAgent(
        provider=build_provider(cfg),
        transport=build_transport(cfg),
        clock=build_clock(cfg),
        settings=build_settings(cfg),
    )
    await agent.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the generic telemetry lab agent")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(async_main(args.config))


if __name__ == "__main__":
    main()
