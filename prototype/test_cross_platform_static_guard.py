import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import unittest

from mock_guest_session import BidirectionalGuestSession, TestHostResponder as HostResponder
from mock_telemetry_agent import FrozenProfile, InMemoryTransport, MockTelemetryAgent


ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROTOTYPE = ROOT / "prototype"
PROFILE = ROOT / "lab" / "mock-telemetry" / "baseline.synthetic.json"
FORBIDDEN_IMPORTS = {
    "ctypes",
    "psutil",
    "win32api",
    "win32com",
    "win32con",
    "win32file",
    "win32pipe",
    "wmi",
}


class CrossPlatformStaticGuardTests(unittest.TestCase):
    def test_prototype_runtime_modules_have_no_platform_bound_imports(self):
        violations = []
        for path in sorted(PROTOTYPE.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [item.name.split(".", 1)[0] for item in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".", 1)[0]]
                for name in names:
                    if name in FORBIDDEN_IMPORTS:
                        violations.append(f"{path.name}:{node.lineno}:{name}")
        self.assertEqual(violations, [])

    def test_same_profile_and_clock_produce_identical_envelopes(self):
        def run_once():
            profile = FrozenProfile.load(str(PROFILE))
            transport = InMemoryTransport(responder=HostResponder())
            session = BidirectionalGuestSession(transport, profile.identity, profile.environment)
            agent = MockTelemetryAgent(
                profile,
                transport,
                start_time=datetime(2030, 1, 1, tzinfo=timezone.utc),
                control_session=session,
            )
            agent.start()
            agent.run_for(600)
            return [
                (item.as_dict(), item.wire_payload)
                for item in transport.messages
            ]

        self.assertEqual(run_once(), run_once())
