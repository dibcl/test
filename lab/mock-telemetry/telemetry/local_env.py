from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID


REQUIRED_FIELDS = (
    "VMID",
    "UUID",
    "HOSTID",
    "COMPUTERNAME",
    "MAC",
    "IP",
    "CPU",
    "OS",
    "MEM",
    "DISK",
)

_MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}$")
_MEM_RE = re.compile(r"^\d+(?:\.\d+)?[KMGTP]?$", re.IGNORECASE)
_DISK_PART_RE = re.compile(r"^[A-Za-z]:\d+(?:\.\d+)?(?:KB|MB|GB|TB)$", re.IGNORECASE)


class LocalEnvironmentError(ValueError):
    pass


def _require_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise LocalEnvironmentError(f"{key} must be a non-empty string")
    return item


def load_local_environment(path: str | Path) -> dict[str, str]:
    """Load the explicitly declared current-machine Windows baseline.

    This loader does not discover the host. The file is authoritative for the
    validation run and is kept byte-for-value faithful after validation.
    """
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalEnvironmentError(f"cannot load local environment: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise LocalEnvironmentError("local environment root must be an object")

    missing = [key for key in REQUIRED_FIELDS if key not in value]
    extra = [key for key in value if key not in REQUIRED_FIELDS]
    if missing:
        raise LocalEnvironmentError(f"local environment missing fields: {missing}")
    if extra:
        raise LocalEnvironmentError(f"local environment contains unsupported fields: {extra}")

    result = {key: _require_string(value, key) for key in REQUIRED_FIELDS}
    result["OS"] = result["OS"].replace("鐗堟湰", "版本")

    for key in ("VMID", "UUID"):
        try:
            UUID(result[key])
        except ValueError as exc:
            raise LocalEnvironmentError(f"{key} must be a valid UUID") from exc

    if not re.fullmatch(r"[0-9A-Fa-f]{36}", result["HOSTID"]):
        raise LocalEnvironmentError("HOSTID must contain exactly 36 hexadecimal characters")
    if not _MAC_RE.fullmatch(result["MAC"]):
        raise LocalEnvironmentError("MAC must be a six-octet MAC address")
    try:
        ipaddress.ip_address(result["IP"])
    except ValueError as exc:
        raise LocalEnvironmentError("IP must be a valid IP address") from exc
    if not _MEM_RE.fullmatch(result["MEM"]):
        raise LocalEnvironmentError("MEM must use a compact numeric size such as 16383M")

    disk_parts = result["DISK"].split(",")
    if not disk_parts or any(not _DISK_PART_RE.fullmatch(part) for part in disk_parts):
        raise LocalEnvironmentError("DISK must use comma-separated drive sizes such as C:79.95GB,D:500.00GB")

    return result
