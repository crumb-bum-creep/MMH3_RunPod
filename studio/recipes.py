"""Recipes (config/recipes.yaml) resolved into concrete sampling settings.

The image's recipes are layered with yours from /workspace/mmh3/config/recipes.yaml:

  families:
    ref2v:
      recipes: {my_steady: {label: ..., lora: ..., steps: ..., ...}}   # your own
      hidden: [upstream]          # not offered in Create (still resolvable)
      compare: [balanced, legacy_euler]
      default: balanced
      av_recipe: legacy_euler     # R2V: switch to this when a reference has video/audio; null = never
"""
from __future__ import annotations

import copy
import re
import threading
from typing import Any

from . import paths, util

FAMILY_OF_MODE = {"t2v": "fl2v", "i2v": "fl2v", "r2v": "ref2v"}
MODE_OF_FAMILY = {"fl2v": "t2v", "ref2v": "r2v"}

# Fields a user may override per generation from the Advanced drawer.
TUNABLE = {"strength", "steps", "sampler", "scheduler", "shift", "extend", "ref_image_size"}
REF_IMAGE_SIZES = ("max", "match")  # what ComfyUI's MiniMaxH3ReferenceToVideo accepts
RECIPE_FIELDS = ("label", "note", "lora", "strength", "steps", "sampler", "scheduler", "shift", "extend", "ref_image_size")
USER_FILE = paths.CONFIG / "recipes.yaml"
_lock = threading.RLock()


class RecipeError(ValueError):
    pass


def _user() -> dict[str, Any]:
    data = util.read_yaml(USER_FILE, {}) or {}
    return data if isinstance(data.get("families"), dict) else {"families": {}}


def turbo_models(family: str) -> dict[str, dict[str, Any]]:
    """Turbo LoRAs a family's recipes can use: {model id: {label, file}}."""
    models = util.image_yaml("models.yaml").get("models") or {}
    return {mid: {"label": m.get("label") or mid, "file": m["file"]} for mid, m in models.items()
            if m.get("role") == "turbo" and family in (m.get("used_by") or [])}


def catalog() -> dict[str, Any]:
    """Every family's recipes (image + yours) and your choices, with built-ins marked."""
    base = util.image_yaml("recipes.yaml").get("families") or {}
    mine = _user()["families"]
    out = {}
    for fam, spec in base.items():
        own = mine.get(fam) or {}
        recipes = {rid: {**copy.deepcopy(r), "builtin": True} for rid, r in (spec.get("recipes") or {}).items()}
        for rid, r in (own.get("recipes") or {}).items():
            if rid not in recipes and isinstance(r, dict):
                recipes[rid] = {**copy.deepcopy(r), "builtin": False}
        hidden = [h for h in own.get("hidden") or [] if h in recipes]
        default = own.get("default") if own.get("default") in recipes else spec.get("default")
        compare = own["compare"] if isinstance(own.get("compare"), list) else spec.get("compare") or []
        av = own["av_recipe"] if "av_recipe" in own else spec.get("av_recipe")
        out[fam] = {
            "default": default,
            "compare": [c for c in compare if c in recipes],
            "hidden": hidden,
            "av_recipe": av if av in recipes else None,
            "recipes": recipes,
        }
    return out


def family_for(mode: str) -> str:
    try:
        return FAMILY_OF_MODE[mode]
    except KeyError:
        raise RecipeError(f"unknown mode: {mode}") from None


def public_catalog() -> dict[str, Any]:
    """What the UI needs: per family, the ordered recipes with labels and notes, your choices,
    and the turbo LoRAs a recipe can use."""
    out = {}
    for fam, spec in catalog().items():
        out[fam] = {
            "default": spec["default"],
            "compare": spec["compare"],
            "hidden": spec["hidden"],
            "av_recipe": spec["av_recipe"],
            "turbo": turbo_models(fam),
            "recipes": [{"id": rid, **copy.deepcopy(r)} for rid, r in spec["recipes"].items()],
        }
    return out


# --------------------------------------------------------------------------- your recipes and choices

def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:32]
    return f"my_{s or 'recipe'}"


def save_recipe(family: str, fields: dict[str, Any], recipe_id: str | None = None) -> dict[str, Any]:
    """Create or edit one of your recipes. Built-ins can't be edited (duplicate them instead)."""
    if family not in MODE_OF_FAMILY:
        raise RecipeError(f"unknown family: {family}")
    r = {k: copy.deepcopy(fields[k]) for k in RECIPE_FIELDS if fields.get(k) not in (None, "")}
    r["label"] = str(r.get("label") or "").strip()[:40]
    if not r["label"]:
        raise RecipeError("give the recipe a name")
    r["note"] = str(r.get("note") or "").strip()[:200]
    if r.get("lora") in ("none", "", None):
        r["lora"] = None
    elif r["lora"] not in turbo_models(family):
        raise RecipeError("pick a turbo LoRA made for this mode, or none")
    if r.get("shift") in ("default", "off"):
        r.pop("shift")
    if r.get("extend") in ("off", False, {}):
        r.pop("extend")
    check = {**r, "family": family}
    validate(check)
    for k in ("strength", "steps", "sampler", "scheduler", "shift", "extend", "ref_image_size"):
        if k in check:
            r[k] = check[k]
    if family != "ref2v":
        r.pop("ref_image_size", None)
    with _lock:
        data = _user()
        fam = data["families"].setdefault(family, {})
        mine = fam.setdefault("recipes", {})
        builtins = (util.image_yaml("recipes.yaml").get("families") or {}).get(family, {}).get("recipes") or {}
        if recipe_id and recipe_id in builtins:
            raise RecipeError("built-in recipes can't be edited; duplicate it instead")
        rid = recipe_id if recipe_id in mine else None
        if rid is None:
            rid, n = _slug(r["label"]), 2
            while rid in mine or rid in builtins:
                rid, n = f"{_slug(r['label'])}_{n}", n + 1
        mine[rid] = r
        util.write_yaml(USER_FILE, data)
    return {"id": rid, **r, "builtin": False}


def delete_recipe(family: str, recipe_id: str) -> None:
    with _lock:
        data = _user()
        fam = data["families"].get(family) or {}
        if recipe_id not in (fam.get("recipes") or {}):
            raise RecipeError("only your own recipes can be deleted")
        del fam["recipes"][recipe_id]
        for key in ("hidden", "compare"):
            if isinstance(fam.get(key), list):
                fam[key] = [x for x in fam[key] if x != recipe_id]
        for key in ("default", "av_recipe"):
            if fam.get(key) == recipe_id:
                fam.pop(key)
        util.write_yaml(USER_FILE, data)


def save_choices(family: str, choices: dict[str, Any]) -> dict[str, Any]:
    """Which recipes Create shows, which Compare runs, the default, and the R2V audio/video switch."""
    spec = catalog().get(family)
    if spec is None:
        raise RecipeError(f"unknown family: {family}")
    ids = set(spec["recipes"])
    hidden = [h for h in choices.get("hidden", spec["hidden"]) or [] if h in ids]
    compare = [c for c in choices.get("compare", spec["compare"]) or [] if c in ids]
    default = choices.get("default", spec["default"])
    av = choices.get("av_recipe", spec["av_recipe"]) if family == "ref2v" else None
    if default not in ids:
        raise RecipeError("unknown default recipe")
    if default in hidden:
        raise RecipeError("the default recipe has to stay visible in Create")
    if av is not None and av not in ids:
        raise RecipeError("unknown recipe for video/audio references")
    with _lock:
        data = _user()
        fam = data["families"].setdefault(family, {})
        fam.update({"hidden": hidden, "compare": list(dict.fromkeys(compare)), "default": default})
        if family == "ref2v":
            fam["av_recipe"] = av
        util.write_yaml(USER_FILE, data)
    return public_catalog()[family]


def resolve(mode: str, recipe_id: str | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    fam = family_for(mode)
    spec = catalog().get(fam) or {}
    recipes = spec.get("recipes") or {}
    rid = recipe_id or spec.get("default")
    if rid not in recipes:
        raise RecipeError(f"unknown recipe '{rid}' for {mode}")
    r = copy.deepcopy(recipes[rid])
    r.pop("builtin", None)
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
    if r.get("lora") is not None and r["lora"] not in turbo_models(r["family"]):
        raise RecipeError(f"unknown turbo LoRA '{r['lora']}' for this mode")
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
        if r["ref_image_size"] == "original":  # an old Studio option ComfyUI never accepted
            r["ref_image_size"] = "max"
        if r["ref_image_size"] not in REF_IMAGE_SIZES:
            raise RecipeError(f"ref_image_size must be one of {REF_IMAGE_SIZES}")


def model_passes(r: dict[str, Any]) -> int:
    """Rough count of transformer evaluations, for the UI's speed hint."""
    passes = r["steps"]
    if r.get("extend"):
        passes += 2 * r["extend"]["steps"]
    return passes
