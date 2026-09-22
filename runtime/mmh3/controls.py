from __future__ import annotations

import json
from typing import Any

from .common import DATA_ROOT, dump_json

CONTROLS_FILE = DATA_ROOT / "runtime_controls.json"

DEFAULT_CONTROLS: dict[str, Any] = {
    "memory_protection": True,
    "output_naming_template": "MMH3/{mode}",
}


def load_controls() -> dict[str, Any]:
    value: dict[str, Any] = {}
    try:
        raw = json.loads(CONTROLS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            value = raw
    except Exception:
        pass

    out = dict(DEFAULT_CONTROLS)
    out.update({k: v for k, v in value.items() if k in DEFAULT_CONTROLS})
    out["memory_protection"] = bool(out.get("memory_protection", True))
    template = str(out.get("output_naming_template") or DEFAULT_CONTROLS["output_naming_template"]).strip()
    out["output_naming_template"] = template[:240] or DEFAULT_CONTROLS["output_naming_template"]
    return out


def save_controls(update: dict[str, Any]) -> dict[str, Any]:
    current = load_controls()
    if "memory_protection" in update:
        current["memory_protection"] = bool(update["memory_protection"])
    if "output_naming_template" in update:
        template = str(update["output_naming_template"] or "").strip()
        current["output_naming_template"] = template[:240] or DEFAULT_CONTROLS["output_naming_template"]
    dump_json(CONTROLS_FILE, current)
    return current
