from __future__ import annotations

import argparse
import asyncio
import json

from telemetry.runtime import TelemetryRuntime


async def async_main(config_path: str) -> None:
    runtime = TelemetryRuntime.from_file(config_path)
    try:
        await runtime.run()
    finally:
        print(json.dumps(runtime.status.to_dict(), ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the generic telemetry lab agent")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(async_main(args.config))


if __name__ == "__main__":
    main()
