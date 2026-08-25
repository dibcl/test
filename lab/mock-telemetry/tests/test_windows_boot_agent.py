from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools"
RUNTIME_ROOT = ROOT / "lab" / "mock-telemetry"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from windows_boot_agent import run_boot_session


def _messages(session: Path) -> list[dict]:
    return [json.loads(line) for line in (session / "messages.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()]


class WindowsBootAgentTests(unittest.IsolatedAsyncioTestCase):
    def _config(self, root: Path, state_path: Path) -> Path:
        config = json.loads(
            (RUNTIME_ROOT / "config.windows-validation.json").read_text(encoding="utf-8")
        )
        config["provider"] = dict(config["provider"])
        config["provider"].update({
            "profile": str(RUNTIME_ROOT / "baseline.runtime.json"),
            "local_env": str(RUNTIME_ROOT / "local_env.json"),
            "state_path": str(state_path),
        })
        config["message_adapter"] = dict(config["message_adapter"])
        config["message_adapter"]["software_profile"] = str(
            RUNTIME_ROOT / "fixtures" / "observed-software-baseline.json"
        )
        path = root / "boot-config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    async def test_manual_start_captures_complete_startup_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "persistent-state.json"
            state_path.write_text(json.dumps({
                "process_pid_map": {"explorer.exe": 99999},
                "dynamic_metrics": {"cpu": 99, "memory": 99, "disk_io": 99},
            }), encoding="utf-8")
            original_state = state_path.read_bytes()
            result = await run_boot_session(
                self._config(root, state_path),
                root / "out" / "boot-session",
                duration_seconds=30,
                simulated=True,
                session_id="manual-start",
            )
            session = Path(result["session_dir"])
            rows = _messages(session)

            self.assertEqual(
                [row["int_msgid"] for row in rows[:6]],
                [9050, 9054, 9054, 9054, 4002, 4004],
            )
            for name in ("messages.jsonl", "mswitch.raw", "runtime-status.json", "start_time.txt"):
                self.assertTrue((session / name).is_file(), name)
            self.assertGreater((session / "mswitch.raw").stat().st_size, 0)
            self.assertEqual(json.loads((session / "runtime-status.json").read_text())["state"], "stopped")
            self.assertEqual(state_path.read_bytes(), original_state)
            self.assertEqual(
                (root / "out" / "boot-session" / "latest.txt").read_text().strip(),
                str(session),
            )

    async def test_two_boot_sessions_reset_dynamic_state_and_keep_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "persistent-state.json"
            state_path.write_text(json.dumps({
                "process_pid_map": {"explorer.exe": 99999},
                "dynamic_metrics": {"cpu": 99, "memory": 99, "disk_io": 99},
            }), encoding="utf-8")
            config = self._config(root, state_path)
            output = root / "out" / "boot-session"
            first = await run_boot_session(
                config,
                output,
                duration_seconds=307,
                simulated=True,
                session_id="boot-1",
            )
            second = await run_boot_session(
                config,
                output,
                duration_seconds=307,
                simulated=True,
                session_id="boot-2",
            )
            first_rows = _messages(Path(first["session_dir"]))
            second_rows = _messages(Path(second["session_dir"]))

        for rows in (first_rows, second_rows):
            ids = [row["int_msgid"] for row in rows]
            self.assertEqual(ids[:6], [9050, 9054, 9054, 9054, 4002, 4004])
            self.assertIn(9051, ids[6:])
            self.assertIn(9052, ids[6:])

        first_environment = next(row for row in first_rows if row["int_msgid"] == 9050)
        second_environment = next(row for row in second_rows if row["int_msgid"] == 9050)
        self.assertEqual(first_environment["payload"]["uuid"], second_environment["payload"]["uuid"])

        first_performance = next(row for row in first_rows if row["int_msgid"] == 9051)
        second_performance = next(row for row in second_rows if row["int_msgid"] == 9051)
        for key in ("cpu", "mem", "disk"):
            self.assertEqual(
                first_performance["payload"]["performance"][0][key],
                second_performance["payload"]["performance"][0][key],
            )

        first_process = next(row for row in first_rows if row["int_msgid"] == 9052)
        second_process = next(row for row in second_rows if row["int_msgid"] == 9052)
        first_pids = [item["data"].split("|")[1] for item in first_process["payload"]["process"]]
        second_pids = [item["data"].split("|")[1] for item in second_process["payload"]["process"]]
        self.assertEqual(first_pids, second_pids)
        self.assertNotIn("99999", first_pids)

    def test_task_script_contains_boot_delay_and_lifecycle_actions(self) -> None:
        script = (ROOT / "tools" / "windows_boot_task.ps1").read_text(encoding="utf-8")
        self.assertIn("New-ScheduledTaskTrigger -AtStartup", script)
        self.assertIn("$trigger.Delay = 'PT30S'", script)
        self.assertIn("$env:VIRTUAL_ENV", script)
        self.assertIn("'Stop'", script)
        self.assertIn("'Uninstall'", script)


if __name__ == "__main__":
    unittest.main()
