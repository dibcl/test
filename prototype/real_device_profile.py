"""Load redacted, test-only device metadata and local golden frame fixtures."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from mswitch_protocol import ProtocolError, SerialFrameDecoder, parse_message


class RealDeviceProfileError(ValueError):
    pass


@dataclass(frozen=True)
class GoldenFrame:
    name: str
    path: Path
    encoding: str
    sha256: str
    raw: bytes
    int_msgid: int


@dataclass(frozen=True)
class RealDeviceProfile:
    schema_version: int
    vmuuid: str
    test_uuid: str
    test_vmid: str
    test_hostid: str
    software_versions: dict[str, str]
    module_ids: dict[str, int]
    golden_frames: tuple[GoldenFrame, ...]
    source_path: Path

    @classmethod
    def load(cls, path: str | Path) -> "RealDeviceProfile":
        source = Path(path).resolve()
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RealDeviceProfileError(f"cannot load profile: {exc}") from exc
        if not isinstance(value, dict):
            raise RealDeviceProfileError("profile root must be a JSON object")
        if value.get("test_mode") is not True or value.get("redacted") is not True:
            raise RealDeviceProfileError("profile requires test_mode=true and redacted=true")
        if value.get("schema_version") != 1:
            raise RealDeviceProfileError("unsupported profile schema_version")
        vmuuid = cls._uuid(value.get("vmuuid"), "vmuuid")
        test_uuid = cls._uuid(value.get("test_uuid"), "test_uuid")
        test_vmid = cls._identifier(value.get("test_vmid"), "test_vmid")
        test_hostid = cls._identifier(value.get("test_hostid"), "test_hostid")
        software_versions = cls._string_map(value.get("software_versions"), "software_versions")
        required_versions = {"vmbooster", "PVDriver", "vdagent", "usbipc", "media_redirect"}
        missing_versions = required_versions - software_versions.keys()
        if missing_versions:
            raise RealDeviceProfileError(f"software_versions missing keys: {sorted(missing_versions)}")
        module_ids = cls._module_map(value.get("module_ids"))
        frames = tuple(cls._load_frame(source.parent, item) for item in value.get("golden_frames", []))
        return cls(1, vmuuid, test_uuid, test_vmid, test_hostid, software_versions, module_ids, frames, source)

    @staticmethod
    def _uuid(value: Any, name: str) -> str:
        try:
            return str(UUID(str(value)))
        except (ValueError, AttributeError) as exc:
            raise RealDeviceProfileError(f"profile requires a valid {name}") from exc

    @staticmethod
    def _identifier(value: Any, name: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise RealDeviceProfileError(f"profile requires a non-empty {name}")
        return value

    @staticmethod
    def _string_map(value: Any, name: str) -> dict[str, str]:
        if not isinstance(value, dict) or not value:
            raise RealDeviceProfileError(f"{name} must be a non-empty object")
        result: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or not isinstance(item, str) or not item:
                raise RealDeviceProfileError(f"{name} keys and values must be non-empty strings")
            result[key] = item
        return result

    @staticmethod
    def _module_map(value: Any) -> dict[str, int]:
        if not isinstance(value, dict) or not value:
            raise RealDeviceProfileError("module_ids must be a non-empty object")
        result: dict[str, int] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or isinstance(item, bool) or not isinstance(item, int):
                raise RealDeviceProfileError("module_ids must map names to integers")
            if not 0 <= item <= 0xFFFFFFFF:
                raise RealDeviceProfileError(f"module ID out of range: {key}")
            result[key] = item
        return result

    @staticmethod
    def _load_frame(root: Path, value: Any) -> GoldenFrame:
        if not isinstance(value, dict):
            raise RealDeviceProfileError("golden frame entry must be an object")
        name = str(value.get("name", ""))
        relative = Path(str(value.get("path", "")))
        encoding = str(value.get("encoding", "raw"))
        expected_hash = str(value.get("sha256", "")).lower()
        if not name or not relative.parts or relative.is_absolute() or encoding not in {"raw", "serial"}:
            raise RealDeviceProfileError("invalid golden frame name, path or encoding")
        resolved = (root / relative).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise RealDeviceProfileError("golden frame path escapes the profile directory") from exc
        try:
            stored = resolved.read_bytes()
        except OSError as exc:
            raise RealDeviceProfileError(f"cannot read golden frame {name}: {exc}") from exc
        actual_hash = sha256(stored).hexdigest()
        if len(expected_hash) != 64 or actual_hash != expected_hash:
            raise RealDeviceProfileError(f"golden frame SHA-256 mismatch: {name}")
        raw = stored
        if encoding == "serial":
            decoder = SerialFrameDecoder()
            try:
                decoded = decoder.feed(stored)
                decoder.finish()
            except ProtocolError as exc:
                raise RealDeviceProfileError(f"invalid serial golden frame {name}: {exc}") from exc
            if len(decoded) != 1:
                raise RealDeviceProfileError(f"serial golden frame {name} must contain exactly one frame")
            raw = decoded[0]
        try:
            message = parse_message(raw)
        except ProtocolError as exc:
            raise RealDeviceProfileError(f"invalid Mswitch golden frame {name}: {exc}") from exc
        declared = value.get("int_msgid")
        if declared is not None and int(declared) != message.int_msgid:
            raise RealDeviceProfileError(f"golden frame message ID mismatch: {name}")
        return GoldenFrame(name, resolved, encoding, actual_hash, raw, message.int_msgid)

    def apply_to_frozen(self, profile: Any) -> Any:
        """Return a copy of FrozenProfile with fixture metadata injected."""
        identity = dict(profile.identity)
        identity.update({
            "test_mode": True,
            "vmuuid": self.vmuuid,
            "test_uuid": self.test_uuid,
            "test_vmid": self.test_vmid,
            "test_hostid": self.test_hostid,
            "module_ids": dict(self.module_ids),
        })
        identity["agent_version"] = self.software_versions["vmbooster"]
        identity["driver_version"] = self.software_versions["PVDriver"]
        identity["software_versions"] = dict(self.software_versions)
        return replace(profile, identity=identity)

    def frame(self, name: str) -> GoldenFrame:
        for item in self.golden_frames:
            if item.name == name:
                return item
        raise KeyError(name)
