from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from telemetry.accelerated_validation import AcceleratedWindowsValidationProvider
from telemetry.clocks import SimulatedClock


ROOT = Path(__file__).resolve().parents[1]


class AcceleratedValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_hour_seed_has_no_fixed_upper_boundary_and_changes_cpu_leader(self) -> None:
        with patch("telemetry.accelerated_validation.random.SystemRandom") as system_random:
            system_random.return_value.randrange.return_value = 101
            provider = AcceleratedWindowsValidationProvider(
                ROOT / "baseline.runtime.json",
                ROOT / "local_env.json",
            )

        clock = SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
        process_deadlines = {307 + 300 * index for index in range(23)}
        process_leaders: list[str] = []
        per_core_values: list[float] = []
        for second in range(7200):
            snapshot = await provider.snapshot(clock)
            per_core_values.extend(snapshot.metrics["cpu"]["per_core"])
            if second in process_deadlines:
                process_leaders.append(
                    snapshot.metrics["process_snapshot"]["process"][0]["name"]
                )
            await clock.sleep(1)

        leader_changes = sum(
            left != right
            for left, right in zip(process_leaders, process_leaders[1:])
        )
        self.assertEqual(len(process_leaders), 23)
        self.assertGreaterEqual(leader_changes, 5)
        self.assertLessEqual(leader_changes, 21)
        self.assertNotIn(58.49, per_core_values)
        self.assertNotIn(58.5, per_core_values)


if __name__ == "__main__":
    unittest.main()
