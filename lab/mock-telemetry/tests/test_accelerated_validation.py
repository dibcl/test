from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from telemetry.accelerated_validation import AcceleratedWindowsValidationProvider
from telemetry.clocks import SimulatedClock


ROOT = Path(__file__).resolve().parents[1]


class AcceleratedValidationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _provider_for_seed(seed: int) -> tuple[AcceleratedWindowsValidationProvider, SimulatedClock]:
        with patch("telemetry.accelerated_validation.random.SystemRandom") as system_random:
            system_random.return_value.randrange.return_value = seed
            provider = AcceleratedWindowsValidationProvider(
                ROOT / "baseline.runtime.json",
                ROOT / "local_env.json",
            )
        return provider, SimulatedClock(datetime(2030, 1, 1, tzinfo=timezone.utc))

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

    async def test_seeded_timestamp_phase_is_rich_bounded_and_reproducible(self) -> None:
        first_provider, first_clock = await self._provider_for_seed(707)
        second_provider, second_clock = await self._provider_for_seed(707)
        first: list[str] = []
        second: list[str] = []
        for _ in range(620):
            first.append((await first_provider.snapshot(first_clock)).observed_at)
            second.append((await second_provider.snapshot(second_clock)).observed_at)
            await first_clock.sleep(1)
            await second_clock.sleep(1)

        self.assertEqual(first, second)
        milliseconds = [datetime.fromisoformat(value).microsecond // 1000 for value in first]
        self.assertGreater(len(set(milliseconds)), 100)
        self.assertGreater(max(milliseconds) - min(milliseconds), 500)
        elapsed = [
            (datetime.fromisoformat(value) - datetime.fromisoformat(first[0])).total_seconds()
            for value in first
        ]
        self.assertLess(abs(elapsed[-1] - 619.0), 1.0)

    async def test_process_ecology_changes_membership_without_replacing_core_pids(self) -> None:
        seed_jaccards: list[float] = []
        memory_jaccards: list[float] = []
        for seed in (1103, 2207, 3301):
            provider, clock = await self._provider_for_seed(seed)
            sets: list[set[str]] = []
            ranked_sets: dict[str, list[set[str]]] = {
                group: [] for group in (
                    "process", "process_memory", "process_handle",
                    "process_diskio", "process_netio",
                )
            }
            duplicate_snapshots: dict[str, int] = {group: 0 for group in ranked_sets}
            duplicate_slots: dict[str, list[int]] = {group: [] for group in ranked_sets}
            core_pids: dict[str, set[int]] = {
                name: set() for name in ("IceDisplay", "IceTunnel", "VmQoEAgent", "MswitchWin")
            }
            unique_names: set[str] = set()
            for second in range(7200):
                snapshot = await provider.snapshot(clock)
                if second in {307 + 300 * index for index in range(23)}:
                    process = snapshot.metrics["process_snapshot"]
                    names = {row["name"] for row in process["process"]}
                    sets.append(names)
                    unique_names.update(names)
                    for group in (
                        "process", "process_memory", "process_handle",
                        "process_diskio", "process_netio",
                    ):
                        rows = process[group]
                        ranked_sets[group].append({row["name"] for row in rows})
                        name_pids: dict[str, set[int]] = {}
                        for row in rows:
                            name_pids.setdefault(row["name"], set()).add(row["pid"])
                        duplicate_snapshots[group] += any(
                            len(pids) > 1 for pids in name_pids.values()
                        )
                        duplicate_slots[group].append(sum(
                            max(0, len(pids) - 1) for pids in name_pids.values()
                        ))
                        for row in process[group]:
                            if row["name"] in core_pids:
                                core_pids[row["name"]].add(row["pid"])
                await clock.sleep(1)

            similarities = [
                len(left & right) / len(left | right)
                for left, right in zip(sets, sets[1:])
            ]
            seed_jaccards.append(sum(similarities) / len(similarities))
            self.assertGreaterEqual(len(unique_names), 15)
            self.assertLess(sum(similarities) / len(similarities), 0.95)
            for pids in core_pids.values():
                self.assertLessEqual(len(pids), 1)
            for group, lower_bound in (
                ("process_memory", 0.889),
                ("process_handle", 0.873),
            ):
                group_sets = ranked_sets[group]
                ranked_jaccards = [
                    len(left & right) / len(left | right)
                    for left, right in zip(group_sets, group_sets[1:])
                ]
                self.assertGreaterEqual(
                    sum(ranked_jaccards) / len(ranked_jaccards), lower_bound
                )
                if group == "process_memory":
                    memory_jaccards.append(sum(ranked_jaccards) / len(ranked_jaccards))
                self.assertLessEqual(len(set().union(*group_sets)), 15)
                self.assertGreaterEqual(duplicate_snapshots[group] / len(group_sets), 0.75)
            self.assertGreaterEqual(
                sum(duplicate_slots["process_diskio"]) / len(duplicate_slots["process_diskio"]),
                2.0,
            )

        self.assertGreater(max(seed_jaccards) - min(seed_jaccards), 0.005)
        self.assertTrue(any(value < 1.0 for value in memory_jaccards))


if __name__ == "__main__":
    unittest.main()
