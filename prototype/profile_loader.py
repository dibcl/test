"""Render synthetic test profiles without permitting real identity injection.

This loader is intentionally limited to offline/loopback test profiles. Arbitrary
local values may be audited elsewhere, but only TEST_* variables can flow into
payload-building profiles and network identity values must use documentation/test
ranges.
"""

from __future__ import annotations

from ipaddress import ip_address, ip_network
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import UUID


PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
TEST_NAME = re.compile(r"^TEST_[A-Z0-9_]+$")
MAC = re.compile(r"^(?:[0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}$")
DOC_NETS = tuple(ip_network(value) for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))


class ProfileLoaderError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProfileLoaderError(f"JSON root must be an object: {path}")
    return value


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_template(path: Path, seen: frozenset[Path] = frozenset()) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved in seen:
        raise ProfileLoaderError("profile extends cycle detected")
    value = _load_json(resolved)
    if value.get("redacted") is True:
        raise ProfileLoaderError("redacted/reference device profiles cannot be rendered as payload profiles")
    parent = value.pop("extends", None)
    if parent is None:
        return value
    parent_path = Path(str(parent))
    if parent_path.is_absolute() or ".." in parent_path.parts:
        raise ProfileLoaderError("profile extends must be a safe relative path")
    base = _load_template(resolved.parent / parent_path, seen | {resolved})
    return _deep_merge(base, value)


def _validate_variable(name: str, value: Any) -> str:
    if not TEST_NAME.fullmatch(name):
        raise ProfileLoaderError(f"only TEST_* variables are permitted in rendered payload profiles: {name}")
    text = str(value)
    if not text or len(text) > 256:
        raise ProfileLoaderError(f"invalid test variable length: {name}")

    if name in {"TEST_UUID", "TEST_VMUUUID"}:
        try:
            UUID(text)
        except ValueError as exc:
            raise ProfileLoaderError(f"{name} must be a valid test UUID") from exc
    elif name == "TEST_IP":
        try:
            address = ip_address(text)
        except ValueError as exc:
            raise ProfileLoaderError("TEST_IP must be a valid IP address") from exc
        if not any(address in network for network in DOC_NETS):
            raise ProfileLoaderError("TEST_IP must use an RFC 5737 documentation range")
    elif name == "TEST_MAC":
        if not MAC.fullmatch(text):
            raise ProfileLoaderError("TEST_MAC must be a six-octet MAC address")
        first_octet = int(re.split(r"[-:]", text)[0], 16)
        if not (first_octet & 0x02) or (first_octet & 0x01):
            raise ProfileLoaderError("TEST_MAC must be a locally administered unicast address")
    elif name == "TEST_COMPUTERNAME":
        if not text.upper().startswith("TEST-"):
            raise ProfileLoaderError("TEST_COMPUTERNAME must start with TEST-")
    return text


def _variables(local_env_path: str | os.PathLike[str] | None, environ: Mapping[str, str]) -> dict[str, str]:
    values: dict[str, Any] = {}
    if local_env_path is not None and Path(local_env_path).exists():
        raw = _load_json(Path(local_env_path).resolve())
        for name, value in raw.items():
            if str(name).startswith("REAL_"):
                raise ProfileLoaderError("REAL_* values are audit-only and cannot render payload profiles")
            if str(name).startswith("TEST_"):
                values[str(name)] = value

    for env_name, value in environ.items():
        if env_name.startswith("ZTE_REAL_"):
            raise ProfileLoaderError("ZTE_REAL_* values are audit-only and cannot render payload profiles")
        if env_name.startswith("ZTE_TEST_"):
            values[env_name.removeprefix("ZTE_")] = value

    return {name: _validate_variable(name, value) for name, value in values.items()}


def _render(value: Any, variables: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _render(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, variables) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if not TEST_NAME.fullmatch(name):
            raise ProfileLoaderError(f"non-test placeholder is forbidden: {name}")
        if name not in variables:
            raise ProfileLoaderError(f"missing test variable: {name}")
        return variables[name]

    return PLACEHOLDER.sub(replace, value)


def load_rendered_profile(
    profile_path: str | os.PathLike[str],
    *,
    local_env_path: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    profile = _load_template(Path(profile_path))
    rendered = _render(profile, _variables(local_env_path, environ or os.environ))

    unresolved: list[str] = []
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            unresolved.extend(match.group(0) for match in PLACEHOLDER.finditer(value))
    walk(rendered)
    if unresolved:
        raise ProfileLoaderError(f"unresolved placeholders: {sorted(set(unresolved))}")

    identity = rendered.get("identity")
    if not isinstance(identity, dict) or identity.get("test_mode") is not True:
        raise ProfileLoaderError("rendered payload profiles require identity.test_mode=true")
    return rendered
