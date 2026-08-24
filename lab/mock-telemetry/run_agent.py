from __future__ import annotations

import argparse
import asyncio

from telemetry.config import load_config
from telemetry.runtime import TelemetryRuntime


async def async_main(config_path: str) -> None:
    runtime = TelemetryRuntime(load_config(config_path))
    await runtime.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the generic telemetry lab agent")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(async_main(args.config))


if __name__ == "__main__":
    main()
