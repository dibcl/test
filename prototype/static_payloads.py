"""Deterministic builders for profile-backed static telemetry payloads."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus


ZERO_HOSTID = "0" * 36
NETWORK_ONLY_ENVIRONMENT_KEYS = frozenset({"gateway", "netmask", "dns", "dhcp"})


@dataclass(frozen=True)
class StaticPayloadBuilder:
    identity: dict[str, Any]
    environment: dict[str, Any]
    software_batches: tuple[tuple[dict[str, Any], ...], ...]
    protocol_options: dict[str, Any]
    process_snapshot_data: dict[str, Any]
    activity_events_data: tuple[str, ...]

    @classmethod
    def from_profile(cls, profile: Any) -> "StaticPayloadBuilder":
        return cls(
            deepcopy(profile.identity),
            deepcopy(profile.environment),
            tuple(tuple(deepcopy(item) for item in batch) for batch in profile.software_batches),
            deepcopy(profile.protocol_options),
            deepcopy(profile.process_snapshot),
            tuple(str(item) for item in profile.activity_events),
        )

    def environment_payload(self, stamp: str) -> dict[str, Any]:
        environment = {
            key: deepcopy(value)
            for key, value in self.environment.items()
            if key not in NETWORK_ONLY_ENVIRONMENT_KEYS
        }
        environment["targetversion"] = ""
        if self.protocol_options.get("url_encode_text", True):
            for key in ("computername", "os"):
                if key in environment:
                    environment[key] = quote_plus(str(environment[key]))
        return {
            "source": 4,
            "uuid": self.identity["test_uuid"],
            "hostid": ZERO_HOSTID,
            "time": stamp,
            "groupid": str(self.identity.get("user_group_id", "-1")),
            "createtime": stamp,
            "environment": environment,
        }

    def software_payloads(self, stamp: str) -> tuple[dict[str, Any], ...]:
        result = []
        for batch in self.software_batches:
            softwares = []
            for item in batch:
                encoded = deepcopy(item)
                if self.protocol_options.get("url_encode_text", True) and "name" in encoded:
                    encoded["name"] = quote_plus(str(encoded["name"]))
                softwares.append(encoded)
            result.append({
                "source": 4,
                "uuid": self.identity["test_uuid"],
                "hostid": self.identity["test_hostid"],
                "createtime": stamp,
                "mothod": "1",
                "softwares": softwares,
            })
        return tuple(result)

    def startup_time_payload(self, stamp: str) -> dict[str, Any]:
        return {
            "source": 4,
            "uuid": self.identity["test_uuid"],
            "hostid": self.identity["test_hostid"],
            "time": stamp,
            "logdatas": [{"log": f"VmStartTime:{stamp}"}],
        }

    def process_snapshot(self) -> dict[str, Any]:
        return deepcopy(self.process_snapshot_data)

    def activity_events(self) -> list[str]:
        return list(self.activity_events_data)
