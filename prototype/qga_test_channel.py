"""Deterministic QGA-style JSON-RPC state machine for Fake Host tests.

The default command surface is intentionally narrower than a normal QEMU guest
agent. Only the observed time-query behavior and framing/sync helpers are
accepted by default. Optional network-interface fixtures are disabled unless a
test explicitly enables them; no OS, file, exec, shutdown, or host-identity
emulation is provided.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


class QgaTestError(ValueError):
    pass


@dataclass
class QgaCounters:
    requests: int = 0
    time_requests: int = 0
    invalid_requests: int = 0
    errors: int = 0


class QgaTestStateMachine:
    """Handle only evidence-backed/safe test commands by default."""

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        network_interfaces: list[dict[str, Any]] | None = None,
        *,
        allow_fixture_network: bool = False,
    ) -> None:
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.network_interfaces = copy.deepcopy(network_interfaces or [])
        self.allow_fixture_network = bool(allow_fixture_network)
        self.counters = QgaCounters()

    def _not_found(self, request_id: Any) -> dict[str, Any]:
        self.counters.errors += 1
        return {
            "error": {
                "class": "CommandNotFound",
                "desc": "unsupported command in test QGA state machine",
            },
            "id": request_id,
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        self.counters.requests += 1
        request_id = request.get("id")
        command = request.get("execute")
        if not isinstance(command, str) or not command:
            self.counters.invalid_requests += 1
            self.counters.errors += 1
            return {
                "error": {
                    "class": "InvalidParameter",
                    "desc": "test QGA request requires a non-empty execute string",
                },
                "id": request_id,
            }

        if command in {"host-get-time", "guest-get-time"}:
            moment = self.clock()
            if moment.tzinfo is None:
                raise QgaTestError("QGA test clock must be timezone-aware")
            self.counters.time_requests += 1
            return {
                "return": int(moment.timestamp() * 1_000_000_000),
                "id": request_id,
            }

        if command == "guest-network-get-interfaces":
            if not self.allow_fixture_network:
                return self._not_found(request_id)
            return {
                "return": copy.deepcopy(self.network_interfaces),
                "id": request_id,
            }

        if command in {"guest-sync", "guest-sync-delimited"}:
            arguments = request.get("arguments")
            if not isinstance(arguments, dict) or not isinstance(arguments.get("id"), int):
                self.counters.invalid_requests += 1
                self.counters.errors += 1
                return {
                    "error": {
                        "class": "InvalidParameter",
                        "desc": "guest-sync fixture requires an integer arguments.id",
                    },
                    "id": request_id,
                }
            return {"return": arguments["id"], "id": request_id}

        return self._not_found(request_id)


class QgaJsonLineCodec:
    """Encode QGA test responses as compact JSON lines.

    guest-sync-delimited responses carry the standard leading 0xff sentinel so
    an isolated host harness can recover framing after a timeout.
    """

    @staticmethod
    def decode_request(line: bytes) -> dict[str, Any]:
        if line.startswith(b"\xff"):
            line = line[1:]
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QgaTestError("invalid QGA JSON request") from exc
        if not isinstance(value, dict):
            raise QgaTestError("QGA request must be a JSON object")
        return value

    @staticmethod
    def encode_response(request: dict[str, Any], response: dict[str, Any]) -> bytes:
        prefix = b"\xff" if request.get("execute") == "guest-sync-delimited" else b""
        body = json.dumps(response, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        return prefix + body + b"\n"


class QgaPeriodicTestHarness:
    """Drive the observed ten-minute time-query cadence without real I/O."""

    def __init__(self, state_machine: QgaTestStateMachine, start: datetime, interval_seconds: int = 600) -> None:
        if start.tzinfo is None:
            raise QgaTestError("QGA harness start must be timezone-aware")
        if interval_seconds <= 0:
            raise QgaTestError("QGA interval must be positive")
        self.state_machine = state_machine
        self.interval = timedelta(seconds=interval_seconds)
        self.next_due = start + self.interval
        self.sequence = 0
        self.transcript: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def advance_to(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise QgaTestError("QGA harness time must be timezone-aware")
        while self.next_due <= now:
            self.sequence += 1
            request = {"execute": "host-get-time", "id": self.sequence}
            response = self.state_machine.handle(request)
            self.transcript.append((request, response))
            self.next_due += self.interval
