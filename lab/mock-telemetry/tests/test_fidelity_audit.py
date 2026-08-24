from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fidelity_audit import FidelityError, validate_envelope, validate_sequence
from telemetry.clocks import SimulatedClock
from telemetry.providers import HybridSyntheticNetworkProvider


CONTRACT = json.loads((ROOT / "fidelity_contract.json").read_text(encoding="utf-8"))
PROFILE = ROOT / "baseline.runtime.json"


class FidelityAuditTests(unittest.IsolatedAsyncioTestCase):
    async def _rows(self, count: int = 4):
        provider = HybridSyntheticNetworkProvider(PROFILE)
        clock = SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
        counters = [SimpleNamespace(bytes_sent=1000 + i * 250, bytes_recv=2000 + i * 500) for i in range(count + 1)]
        with patch("psutil.net_io_counters", side_effect=counters):
            await provider.start()
            rows = []
            for i in range(count):
                provider._last_time = 100.0 + i
                with patch("telemetry.providers.time.monotonic", return_value=101.0 + i):
                    snapshot = await provider.snapshot(clock)
                rows.append(snapshot.to_envelope(schema_version=1))
                await clock.sleep(1)
        return rows

    async def test_generated_sequence_meets_full_contract(self):
        rows = await self._rows()
        validate_sequence(rows, CONTRACT)

    async def test_recursive_host_identity_leak_is_rejected(self):
        row = (await self._rows(1))[0]
        row["metrics"]["process_snapshot"]["process"][0]["hostname"] = "debian-test"
        with self.assertRaises(FidelityError):
            validate_envelope(row, CONTRACT)

    async def test_undeclared_process_is_rejected(self):
        row = (await self._rows(1))[0]
        row["metrics"]["process_snapshot"]["process"][0]["name"] = "bash"
        with self.assertRaises(FidelityError):
            validate_envelope(row, CONTRACT)

    async def test_network_identity_and_totals_are_rejected(self):
        row = (await self._rows(1))[0]
        row["metrics"]["network_io"]["bytes_sent"] = 123
        with self.assertRaises(FidelityError):
            validate_envelope(row, CONTRACT)

    async def test_wrong_cpu_core_count_is_rejected(self):
        row = (await self._rows(1))[0]
        row["metrics"]["cpu"]["per_core"].pop()
        with self.assertRaises(FidelityError):
            validate_envelope(row, CONTRACT)


if __name__ == "__main__":
    unittest.main()
