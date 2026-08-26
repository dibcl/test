from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "observed-software-baseline.json"
FIELDS = {"name", "type", "publisher", "installtime", "size", "version", "operate"}


class ObservedReferenceFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.value = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_batch_counts_match_filtered_observation(self) -> None:
        batches = self.value["batches"]
        self.assertEqual([item["label"] for item in batches], ["wow6432node", "native", "kb"])
        self.assertEqual([item["observed_count"] for item in batches], [11, 13, 9])
        self.assertEqual([item["retained_count"] for item in batches], [11, 13, 9])
        self.assertEqual([len(item["softwares"]) for item in batches], [11, 13, 9])

    def test_every_retained_row_is_lossless_shape(self) -> None:
        for batch in self.value["batches"]:
            for row in batch["softwares"]:
                self.assertEqual(set(row), FIELDS)
                self.assertIn(row["type"], {"1", "2"})
                self.assertEqual(row["size"], "0")
                self.assertEqual(row["operate"], "1")

    def test_current_observed_software_is_retained(self) -> None:
        names = {
            row["name"]
            for batch in self.value["batches"]
            for row in batch["softwares"]
        }
        self.assertIn("Clash Verge", names)
        filtering = self.value["filtering"]
        self.assertEqual(filtering["sensitive_software_omitted"], [])
        self.assertEqual(filtering["omitted_count"], 0)

    def test_kb_version_space_is_preserved(self) -> None:
        kb = next(item for item in self.value["batches"] if item["label"] == "kb")
        self.assertTrue(all(row["type"] == "2" for row in kb["softwares"]))
        self.assertTrue(all(row["version"] == " " for row in kb["softwares"]))


if __name__ == "__main__":
    unittest.main()
