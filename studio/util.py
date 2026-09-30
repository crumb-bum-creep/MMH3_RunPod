"""Small shared helpers: atomic json/yaml IO, config merging, logging."""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from . import paths


def setup_logging(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    return logging.getLogger(name)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return copy.deepcopy(default)


def write_json(path: Path, value: Any, *, indent: int | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=indent, separators=None if indent else (",", ":"))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_yaml(path: Path, default: Any = None) -> Any:
    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return copy.deepcopy(default)
    return copy.deepcopy(default) if value is None else value


def write_yaml(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")
    os.replace(tmp, path)


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            out[k] = deep_merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(override) if override is not None else copy.deepcopy(base)


def settings() -> dict[str, Any]:
    """Image defaults merged with the user's sparse overrides on the volume."""
    base = read_yaml(paths.IMAGE_CONFIG / "settings.yaml", {}) or {}
    user = read_yaml(paths.CONFIG / "settings.yaml", {}) or {}
    return deep_merge(base, user)


def save_setting(dotted: str, value: Any) -> None:
    """Persist one override (e.g. 'memory.trim_after_job_above') on the volume."""
    path = paths.CONFIG / "settings.yaml"
    user = read_yaml(path, {}) or {}
    node = user
    keys = dotted.split(".")
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value
    write_yaml(path, user)


def image_yaml(name: str) -> Any:
    return read_yaml(paths.IMAGE_CONFIG / name, {}) or {}


def now() -> float:
    return time.time()
