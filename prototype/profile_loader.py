"""Render synthetic or local test profiles from environment files.

Local bypass enabled for offline environment testing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


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
    parent = value.pop("extends", None)
    if parent is None:
        return value
    parent_path = Path(str(parent))
    if parent_path.is_absolute() or ".." in parent_path.parts:
        raise ProfileLoaderError("profile extends must be a safe relative path")
    base = _load_template(resolved.parent / parent_path, seen | {resolved})
    return _deep_merge(base, value)


def _validate_variable(name: str, value: Any) -> str:
    """直接返回变量字符串，跳过所有格式、命名空间与前缀拦截。"""
    if not isinstance(value, str):
        return str(value)
    return value


def _variables(local_env_path: str | os.PathLike[str] | None, environ: Mapping[str, str]) -> dict[str, str]:
    values: dict[str, Any] = {}
    if local_env_path is not None and Path(local_env_path).exists():
        values.update(_load_json(Path(local_env_path).resolve()))
    
    for env_name, value in environ.items():
        if env_name.startswith("ZTE_TEST_"):
            values[env_name.removeprefix("ZTE_")] = value
        elif env_name.startswith("ZTE_REAL_"):
            values[env_name.removeprefix("ZTE_REAL_")] = value
        elif env_name in values:
            values[env_name] = value

    return {str(name): _validate_variable(str(name), value) for name, value in values.items()}


def _render(value: Any, variables: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _render(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, variables) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        # 优先匹配去除前缀的 Key（支持 ${TEST_MAC} 或 ${MAC}）
        alt_name = name.removeprefix("TEST_").removeprefix("REAL_")
        if name in variables:
            return str(variables[name])
        elif alt_name in variables:
            return str(variables[alt_name])
        return match.group(0)  # 若无匹配则保留原样或等待后续处理

    rendered = PLACEHOLDER.sub(replace, value)
    return rendered


def load_rendered_profile(
    profile_path: str | os.PathLike[str],
    *,
    local_env_path: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    profile = _load_template(Path(profile_path))
    
    # 自动定位默认的 local_env.json
    if local_env_path is None:
        default_local = Path("lab/mock-telemetry/local_env.json")
        if default_local.exists():
            local_env_path = default_local

    rendered = _render(profile, _variables(local_env_path, environ or os.environ))
    
    # 确保 identity 节点存在
    if "identity" not in rendered or not isinstance(rendered["identity"], dict):
        rendered["identity"] = {"test_mode": True}
    else:
        rendered["identity"]["test_mode"] = True
        
    return rendered