from datetime import datetime, timedelta, timezone
import json
import os
import unittest

from dynamic_metrics import DynamicMetricsEngine


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "lab", "mock-telemetry", "baseline.synthetic.json")


def dynamics():
    with open(PROFILE, "r", encoding="utf-8") as handle:
        return json.load(handle)["dynamics"]


class DynamicMetricsTests(unittest.TestCase):
    def test_same_seed_produces_identical_sequence(self):
        first = DynamicMetricsEngine(dynamics())
        second = DynamicMetricsEngine(dynamics())
        start = datetime(2030, 1, 1, tzinfo=timezone.utc)
        a = [first.sample(start + timedelta(minutes=i)) for i in range(20)]
        b = [second.sample(start + timedelta(minutes=i)) for i in range(20)]
        self.assertEqual(a, b)

    def test_values_are_bounded_and_primary_pid_is_stable(self):
        engine = DynamicMetricsEngine(dynamics())
        start = datetime(2030, 1, 1, tzinfo=timezone.utc)
        samples = [engine.sample(start + timedelta(minutes=i)) for i in range(200)]
        self.assertTrue(all(8.0 <= item.cpu <= 45.0 for item in samples))
        self.assertTrue(all(35.0 <= item.memory <= 60.0 for item in samples))
        self.assertTrue(all(0.2 <= item.disk_io <= 80.0 for item in samples))
        self.assertTrue(all(0.1 <= item.network_io <= 60.0 for item in samples))
        primary_pids = {
            next(process["pid"] for process in item.processes if process["primary"])
            for item in samples
        }
        self.assertEqual(primary_pids, {100})
        self.assertTrue(any(item.spike for item in samples))
        self.assertTrue(all(
            process["name"].startswith("Synthetic_")
            for sample in samples for process in sample.processes
        ))

    def test_input_activity_has_positive_cpu_association(self):
        engine = DynamicMetricsEngine(dynamics())
        start = datetime(2030, 1, 1, tzinfo=timezone.utc)
        samples = [engine.sample(start + timedelta(minutes=i)) for i in range(400)]
        mean_cpu = sum(item.cpu for item in samples) / len(samples)
        low = [item.keyboard_delta + item.mouse_delta for item in samples if item.cpu < mean_cpu]
        high = [item.keyboard_delta + item.mouse_delta for item in samples if item.cpu >= mean_cpu]
        self.assertGreater(sum(high) / len(high), sum(low) / len(low))


if __name__ == "__main__":
    unittest.main()
