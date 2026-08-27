from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

IMAGE_ROOT = Path(os.environ.get("MMH3_IMAGE_ROOT", "/opt/mmh3"))
WORKSPACE = Path(os.environ.get("MMH3_WORKSPACE", "/workspace"))
PERSIST_ROOT = Path(os.environ.get("MMH3_PERSIST_ROOT", WORKSPACE / "mmh3"))
CONFIG_ROOT = Path(os.environ.get("MMH3_CONFIG_ROOT", PERSIST_ROOT / "config"))
DATA_ROOT = Path(os.environ.get("MMH3_DATA_ROOT", PERSIST_ROOT / "data"))
STATE_ROOT = Path(os.environ.get("MMH3_STATE_ROOT", PERSIST_ROOT / "state"))
LOG_ROOT = Path(os.environ.get("MMH3_LOG_ROOT", PERSIST_ROOT / "logs"))
COMFY_DIR = Path(os.environ.get("MMH3_COMFY_DIR", "/ComfyUI"))
COMFY_PERSIST = Path(os.environ.get("MMH3_COMFY_PERSIST", WORKSPACE / "ComfyUI"))

def ensure_dirs() -> None:
    for p in (PERSIST_ROOT, CONFIG_ROOT, DATA_ROOT, STATE_ROOT, LOG_ROOT, COMFY_PERSIST):
        p.mkdir(parents=True, exist_ok=True)

def load_yaml(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        value = yaml.safe_load(f)
    return default if value is None else value

def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)

def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
