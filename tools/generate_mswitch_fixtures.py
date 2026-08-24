"""Generate deterministic local Mswitch wire fixtures from synthetic profiles."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE = ROOT / "prototype"
if str(PROTOTYPE) not in sys.path:
    sys.path.insert(0, str(PROTOTYPE))

from mock_guest_session import BidirectionalGuestSession, TestHostResponder
from mock_telemetry_agent import FrozenProfile, InMemoryTransport, MockTelemetryAgent
from mswitch_frame_transport import MswitchFrameEncoder
from mswitch_protocol import SerialFrameDecoder, parse_message
from profile_loader import load_rendered_profile
from real_device_profile import RealDeviceProfile


FIXTURE_IDS = (4002, 4004, 8047, 9050, 9054)


def generate(profile: FrozenProfile, output_dir: str | os.PathLike[str]) -> dict[str, object]:
    transport = InMemoryTransport(responder=TestHostResponder())
    session = BidirectionalGuestSession(transport, profile.identity, profile.environment)
    agent = MockTelemetryAgent(profile, transport, control_session=session)
    agent.start()
    agent.run_for(0)
    # Ten decimal digits reproduce the observed 81-byte 8047 Host payload
    # class while remaining a deterministic synthetic uint32 fixture value.
    session.emit_ice_client_quit(agent._stamp(), msgid=2030010100)

    outgoing = {
        item.int_msgid: item
        for item in transport.messages
        if item.int_msgid in FIXTURE_IDS and item.source_module != session.host_module
    }
    missing = set(FIXTURE_IDS) - outgoing.keys()
    if missing:
        raise RuntimeError(f"fixture generation missing IDs: {sorted(missing)}")

    encoder = MswitchFrameEncoder(str(profile.identity["test_uuid"]), test_mode=True)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    for msgid in FIXTURE_IDS:
        serial = encoder.encode(outgoing[msgid])
        decoder = SerialFrameDecoder()
        frames = decoder.feed(serial)
        decoder.finish()
        raw = frames[0]
        payload = parse_message(raw).payload
        files = {
            "serial": f"{msgid}.serial.bin",
            "raw": f"{msgid}.raw.bin",
            "payload": f"{msgid}.payload.bin",
        }
        blobs = {"serial": serial, "raw": raw, "payload": payload}
        for name, filename in files.items():
            (destination / filename).write_bytes(blobs[name])
        entries.append({
            "int_msgid": msgid,
            "files": files,
            "sha256": {name: sha256(blob).hexdigest() for name, blob in blobs.items()},
            "lengths": {name: len(blob) for name, blob in blobs.items()},
        })
    manifest = {"test_mode": True, "deterministic": True, "fixtures": entries}
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def resolve(path: str) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="lab/mock-telemetry/baseline.synthetic.json")
    parser.add_argument("--local-env")
    parser.add_argument("--device-profile")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    rendered = load_rendered_profile(
        resolve(args.profile),
        local_env_path=resolve(args.local_env) if args.local_env else None,
    )
    profile = FrozenProfile.from_dict(rendered)
    if args.device_profile:
        profile = RealDeviceProfile.load(resolve(args.device_profile)).apply_to_frozen(profile)
    manifest = generate(profile, resolve(args.output_dir))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
