from __future__ import annotations

from copy import deepcopy
from typing import Any

from .common import IMAGE_ROOT, load_yaml

PROFILE_FILE = IMAGE_ROOT / "config" / "generation_profiles.yaml"

ALIASES = {
    "t2v": {
        "balanced": "balanced8",
        "quality": "balanced8",
        "8step": "balanced8",
        "fast": "fast4",
        "4step": "fast4",
        "legacy": "legacy_exact",
    },
    "i2v": {
        "balanced": "balanced8",
        "quality": "balanced8",
        "8step": "balanced8",
        "fast": "fast4",
        "4step": "fast4",
        "legacy": "legacy_exact",
    },
    "r2v": {
        "balanced": "balanced8",
        "quality": "balanced8",
        "8step": "balanced8",
        # Preserve old 9d metadata semantics: R2V "fast" meant the exact
        # seeds_2 + Beta/Extend v0.1 recipe.
        "fast": "legacy_exact",
        "4step": "legacy_exact",
        "legacy": "legacy_exact",
    },
}

ALLOWED_OVERRIDE_KEYS = {
    "strength",
    "steps",
    "sampler",
    "schedule_type",
    "scheduler",
    "shift_video",
    "shift_audio",
    "beta_alpha",
    "beta_beta",
    "extend_enabled",
    "extend_steps",
    "extend_start",
    "extend_end",
    "extend_spacing",
    "ref_image_size",
}


def _config() -> dict[str, Any]:
    value = load_yaml(PROFILE_FILE, {}) or {}
    return value if isinstance(value, dict) else {}


def profile_table(mode: str) -> tuple[str, dict[str, dict[str, Any]]]:
    mode = str(mode or "").lower()
    raw = ((_config().get("modes") or {}).get(mode) or {})
    profiles = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
    default = str(raw.get("default") or next(iter(profiles), ""))
    return default, {str(k): deepcopy(v) for k, v in profiles.items() if isinstance(v, dict)}


def canonical_profile_id(mode: str, requested: str | None) -> str:
    default, profiles = profile_table(mode)
    raw = str(requested or default).strip().lower()
    raw = ALIASES.get(str(mode).lower(), {}).get(raw, raw)
    if raw not in profiles:
        raise ValueError(f"unknown generation profile for {mode}: {requested}")
    return raw


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _integer(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def resolve_profile(mode: str, requested: str | None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    profile_id = canonical_profile_id(mode, requested)
    _, profiles = profile_table(mode)
    spec = deepcopy(profiles[profile_id])
    spec["profile_id"] = profile_id

    overrides = overrides if isinstance(overrides, dict) else {}
    for key, value in overrides.items():
        if key in ALLOWED_OVERRIDE_KEYS:
            spec[key] = value

    spec["strength"] = _num(spec.get("strength"), 1.0, 0.0, 2.0)
    spec["steps"] = _integer(spec.get("steps"), 8, 1, 50)
    spec["shift_video"] = _num(spec.get("shift_video"), 6.0, 0.0, 30.0)
    spec["shift_audio"] = _num(spec.get("shift_audio"), 3.0, 0.0, 30.0)
    spec["beta_alpha"] = _num(spec.get("beta_alpha"), 0.6, 0.0, 2.0)
    spec["beta_beta"] = _num(spec.get("beta_beta"), 0.6, 0.0, 2.0)
    spec["extend_steps"] = _integer(spec.get("extend_steps"), 2, 1, 20)
    spec["extend_start"] = _num(spec.get("extend_start"), 0.8, 0.0, 1000.0)
    spec["extend_end"] = _num(spec.get("extend_end"), 0.0, 0.0, 1000.0)
    spec["extend_enabled"] = bool(spec.get("extend_enabled", False))
    spec["sampler"] = str(spec.get("sampler") or "euler")
    spec["schedule_type"] = str(spec.get("schedule_type") or "basic").lower()
    if spec["schedule_type"] not in {"basic", "beta"}:
        spec["schedule_type"] = "basic"
    spec["scheduler"] = str(spec.get("scheduler") or "simple")
    spec["extend_spacing"] = str(spec.get("extend_spacing") or "linear")
    spec["ref_image_size"] = str(spec.get("ref_image_size") or "max")
    if spec["ref_image_size"] not in {"max", "match"}:
        spec["ref_image_size"] = "max"
    spec["turbo_lora"] = str(spec.get("turbo_lora") or "")
    spec["label"] = str(spec.get("label") or profile_id)
    return spec


def public_profiles(mode: str) -> dict[str, Any]:
    default, profiles = profile_table(mode)
    out = {}
    for profile_id in profiles:
        spec = resolve_profile(mode, profile_id)
        out[profile_id] = spec
    return {"default": default, "profiles": out}
