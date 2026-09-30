"""Recipes (config/recipes.yaml) resolved into concrete sampling settings."""
from __future__ import annotations

import copy
from typing import Any

from . import util

FAMILY_OF_MODE = {"t2v": "fl2v", "i2v": "fl2v", "r2v": "ref2v"}

# Fields a user may override per generation from the Advanced drawer.
TUNABLE = {"strength", "steps", "sampler", "scheduler", "shift", "extend", "ref_image_size"}
REF_IMAGE_SIZES = ("max", "match", "original")


class RecipeError(ValueError):
    pass


def catalog() -> dict[str, Any]:
    return util.image_yaml("recipes.yaml").get("families") or {}


def family_for(mode: str) -> str:
    try:
        return FAMILY_OF_MODE[mode]
    except KeyError:
        raise RecipeError(f"unknown mode: {mode}") from None


def public_catalog() -> dict[str, Any]:
    """What the UI needs: per family, the ordered recipes with labels and notes."""
    out = {}
    for fam, spec in catalog().items():
        out[fam] = {
            "default": spec.get("default"),
            "compare": spec.get("compare") or [],
            "recipes": [{"id": rid, **copy.deepcopy(r)} for rid, r in (spec.get("recipes") or {}).items()],
        }
    return out


def resolve(mode: str, recipe_id: str | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    fam = family_for(mode)
    spec = catalog().get(fam) or {}
    recipes = spec.get("recipes") or {}
    rid = recipe_id or spec.get("default")
    if rid not in recipes:
        raise RecipeError(f"unknown recipe '{rid}' for {mode}")
    r = copy.deepcopy(recipes[rid])
    r["id"] = rid
    r["family"] = fam
    changed = []
    for key, value in (overrides or {}).items():
        if key not in TUNABLE or value is None or value == "":
            continue
        if key == "shift" and value in ("off", "default", False):
            r.pop("shift", None)
        elif key == "extend" and value in ("off", False, {}):
            r.pop("extend", None)
        else:
            r[key] = value
        changed.append(key)
    r["overridden"] = sorted(set(changed))
    validate(r)
    return r


def validate(r: dict[str, Any]) -> None:
    try:
        r["strength"] = float(r.get("strength", 1.0))
        r["steps"] = int(r.get("steps", 8))
    except (TypeError, ValueError):
        raise RecipeError("strength/steps must be numbers") from None
    if not 0.0 <= r["strength"] <= 2.0:
        raise RecipeError("turbo strength must be between 0 and 2")
    if not 1 <= r["steps"] <= 60:
        raise RecipeError("steps must be between 1 and 60")
    r["sampler"] = str(r.get("sampler") or "euler")
    r["scheduler"] = str(r.get("scheduler") or "simple")
    if "shift" in r:
        s = r["shift"]
        if not (isinstance(s, (list, tuple)) and len(s) == 2):
            raise RecipeError("shift must be [video, audio]")
        r["shift"] = [float(s[0]), float(s[1])]
        if not all(0.01 <= v <= 100 for v in r["shift"]):
            raise RecipeError("shift values must be between 0.01 and 100")
    if "extend" in r:
        e = r["extend"]
        if not isinstance(e, dict):
            raise RecipeError("extend must be a mapping")
        r["extend"] = {
            "steps": int(e.get("steps", 2)),
            "start": float(e.get("start", 0.8)),
            "end": float(e.get("end", 0.0)),
            "spacing": str(e.get("spacing", "linear")),
        }
    if r["family"] == "ref2v":
        r["ref_image_size"] = str(r.get("ref_image_size") or "max")
        if r["ref_image_size"] not in REF_IMAGE_SIZES:
            raise RecipeError(f"ref_image_size must be one of {REF_IMAGE_SIZES}")


def model_passes(r: dict[str, Any]) -> int:
    """Rough count of transformer evaluations, for the UI's speed hint."""
    passes = r["steps"]
    if r.get("extend"):
        passes += 2 * r["extend"]["steps"]
    return passes
