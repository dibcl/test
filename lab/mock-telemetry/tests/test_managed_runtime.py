from __future__ import annotations

import unittest
from pathlib import Path

from telemetry.runtime import RuntimeState, TelemetryRuntime


FIXTURE = Path(__file__).resolve().parents[1] / "baseline.synthetic.json"


class ManagedRuntimeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_reload_recovers_degraded_state(self) -> None:
        config = {
            "profile": str(FIXTURE),
            "transport": {"type": "memory"},
            "duration_seconds": 0,
        }
        runtime = TelemetryRuntime(config)
        runtime.record_reload_failure("temporary parse error")
        self.assertEqual(runtime.state, RuntimeState.DEGRADED)
        self.assertIsNotNone(runtime.status.last_error)

        await runtime.apply_config(config)

        self.assertEqual(runtime.state, RuntimeState.READY)
        self.assertIsNone(runtime.status.last_error)
        self.assertEqual(runtime.status.successful_reloads, 1)
        self.assertEqual(runtime.status.failed_reloads, 1)

    async def test_restart_required_is_status_only_until_restart(self) -> None:
        runtime = TelemetryRuntime(
            {
                "profile": str(FIXTURE),
                "transport": {"type": "memory"},
                "interval_seconds": 1,
                "duration_seconds": 0,
            }
        )

        await runtime.apply_config(
            {
                "profile": str(FIXTURE),
                "transport": {"type": "memory"},
                "interval_seconds": 3,
                "duration_seconds": 0,
            }
        )

        self.assertTrue(runtime.status.restart_required)
        self.assertEqual(runtime.config["interval_seconds"], 1)
        self.assertEqual(runtime.desired_config["interval_seconds"], 3)


if __name__ == "__main__":
    unittest.main()
