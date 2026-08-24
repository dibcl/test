"""Run the local Mock Telemetry TCP target until interrupted."""

from __future__ import annotations

import argparse
import json
import time

from mock_telemetry_test_server import LoopbackTestServer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19050)
    args = parser.parse_args()
    server = LoopbackTestServer(args.port).start()
    print(json.dumps({"listen": f"127.0.0.1:{server.port}", "framing": "uint32-be + JSON"}))
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.stop()
    print(json.dumps({"received": len(server.messages)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

