"""Run one Windows boot telemetry session and capture its complete output."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "lab" / "mock-telemetry"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from message_adapters.model import ProtocolMessage
from message_adapters.mswitch_frame import MswitchFrameEncoder
from telemetry.config import load_config, register_transport
from telemetry.runtime import TelemetryRuntime
from telemetry.transports import BaseTransport


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _absolute(base: Path, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("boot session path must be a non-empty string")
    path = Path(value)
    return str(path if path.is_absolute() else (base / path).resolve())


def _boot_config(path: Path, duration_seconds: float | None, simulated: bool) -> dict[str, Any]:
    config = load_config(path)
    base = path.resolve().parent
    provider = dict(config.get("provider", {}))
    provider["profile"] = _absolute(base, provider["profile"])
    provider["local_env"] = _absolute(base, provider["local_env"])
    provider.pop("state_path", None)
    config["provider"] = provider

    adapter = dict(config.get("message_adapter", {}))
    if "software_profile" in adapter:
        adapter["software_profile"] = _absolute(base, adapter["software_profile"])
    config["message_adapter"] = adapter
    config["duration_seconds"] = duration_seconds
    if simulated:
        config["clock"] = {
            "type": "simulated",
            "start": "2030-01-01T00:00:00+00:00",
        }
    return config


def _identity_uuid(config: dict[str, Any]) -> str:
    local_env = Path(config["provider"]["local_env"])
    value = json.loads(local_env.read_text(encoding="utf-8"))
    uuid = value.get("UUID") if isinstance(value, dict) else None
    if not isinstance(uuid, str):
        raise ValueError("local_env UUID is missing")
    return uuid


def _protocol_message(value: dict[str, Any]) -> ProtocolMessage:
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("boot capture protocol payload must be an object")
    return ProtocolMessage(
        int_msgid=int(value["int_msgid"]),
        source_module=int(value["source_module"]),
        destination_module=int(value["destination_module"]),
        emitted_at=str(value["emitted_at"]),
        payload=payload,
    )


class BootSessionCaptureTransport(BaseTransport):
    def __init__(
        self,
        session_dir: Path,
        uuid: str,
        on_first_message,
    ) -> None:
        self.session_dir = session_dir
        self.encoder = MswitchFrameEncoder(uuid, dst_type=1)
        self.on_first_message = on_first_message
        self.message_count = 0
        self.message_ids: list[int] = []
        self._messages = None
        self._frames = None

    async def open(self) -> None:
        self._messages = (self.session_dir / "messages.jsonl").open(
            "x", encoding="utf-8", buffering=1
        )
        self._frames = (self.session_dir / "mswitch.raw").open("xb")

    async def send(self, message: dict[str, Any]) -> None:
        if self._messages is None or self._frames is None:
            raise RuntimeError("boot capture transport not opened")
        if self.message_count == 0:
            self.on_first_message()
        protocol_message = _protocol_message(message)
        self._messages.write(
            json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self._messages.flush()
        os.fsync(self._messages.fileno())
        self._frames.write(self.encoder.encode(protocol_message))
        self._frames.flush()
        os.fsync(self._frames.fileno())
        self.message_count += 1
        self.message_ids.append(protocol_message.int_msgid)

    async def close(self) -> None:
        if self._messages is not None:
            self._messages.close()
            self._messages = None
        if self._frames is not None:
            self._frames.close()
            self._frames = None


def _session_directory(output_root: Path, session_id: str | None) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    label = session_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + f"-{os.getpid()}"
    )
    session = output_root / label
    session.mkdir(exist_ok=False)
    (output_root / "latest.txt").write_text(str(session.resolve()) + "\n", encoding="utf-8")
    return session


async def run_boot_session(
    config_path: str | Path,
    output_root: str | Path,
    *,
    duration_seconds: float | None = None,
    simulated: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    config_path = Path(config_path).resolve()
    output_root = Path(output_root).resolve()
    session_dir = _session_directory(output_root, session_id)
    start_time = datetime.now().astimezone().isoformat(timespec="milliseconds")
    (session_dir / "start_time.txt").write_text(start_time + "\n", encoding="utf-8")
    status_path = session_dir / "runtime-status.json"
    base_status = {
        "session_id": session_dir.name,
        "session_dir": str(session_dir),
        "start_time": start_time,
        "state_scope": {
            "identity": "local_env",
            "process_pid": "boot_session",
            "cpu": "boot_session",
            "memory": "boot_session",
            "io": "boot_session",
        },
    }
    _write_json(status_path, {**base_status, "state": "starting", "message_count": 0})

    config = _boot_config(config_path, duration_seconds, simulated)
    runtime: TelemetryRuntime | None = None

    def mark_running() -> None:
        _write_json(status_path, {**base_status, "state": "running", "message_count": 0})

    capture = BootSessionCaptureTransport(
        session_dir,
        _identity_uuid(config),
        mark_running,
    )
    register_transport("boot_session_capture", lambda _cfg: capture, replace=True)
    config["transport"] = {"type": "boot_session_capture"}

    try:
        runtime = TelemetryRuntime(config)
        await runtime.run()
        status = runtime.status.to_dict()
    except BaseException as exc:
        status = runtime.status.to_dict() if runtime is not None else {"state": "failed"}
        status["last_error"] = str(exc)
        raise
    finally:
        final_status = {
            **base_status,
            **status,
            "message_count": capture.message_count,
            "message_counts": {
                str(msgid): capture.message_ids.count(msgid)
                for msgid in sorted(set(capture.message_ids))
            },
        }
        _write_json(status_path, final_status)

    return {
        "session_dir": str(session_dir),
        "status": final_status,
        "message_ids": list(capture.message_ids),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(RUNTIME_ROOT / "config.windows-validation.json"),
    )
    parser.add_argument(
        "--output-root",
        default=str(RUNTIME_ROOT / "out" / "boot-session"),
    )
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--simulated", action="store_true")
    parser.add_argument("--session-id")
    args = parser.parse_args()
    result = asyncio.run(run_boot_session(
        args.config,
        args.output_root,
        duration_seconds=args.duration_seconds,
        simulated=args.simulated,
        session_id=args.session_id,
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
