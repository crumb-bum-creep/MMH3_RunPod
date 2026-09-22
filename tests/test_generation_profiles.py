from __future__ import annotations

from pathlib import Path

import yaml

from mmh3.generation_profiles import public_profiles, resolve_profile


ROOT = Path(__file__).resolve().parents[1]


def test_every_profile_turbo_is_managed_by_model_manifest():
    manifest = yaml.safe_load((ROOT / "config" / "models.yaml").read_text()) or {}
    destinations = {
        str((row or {}).get("destination") or "").split("/")[-1]
        for row in (manifest.get("models") or {}).values()
    }

    for mode in ("t2v", "i2v", "r2v"):
        table = public_profiles(mode)
        assert table["default"] in table["profiles"]
        for spec in table["profiles"].values():
            assert spec["turbo_lora"] in destinations, (mode, spec["profile_id"], spec["turbo_lora"])


def test_r2v_tuned_and_legacy_exact_remain_distinct():
    tuned = resolve_profile("r2v", "tuned")
    legacy = resolve_profile("r2v", "legacy_exact")

    assert tuned["turbo_lora"] == legacy["turbo_lora"]
    assert tuned["sampler"] == "euler"
    assert legacy["sampler"] == "seeds_2"
    assert tuned["schedule_type"] == legacy["schedule_type"] == "beta"


def test_old_profile_aliases_map_without_rewriting_history():
    assert resolve_profile("r2v", "fast")["profile_id"] == "legacy_exact"
    assert resolve_profile("t2v", "fast")["profile_id"] == "fast4"
    assert resolve_profile("i2v", "fast")["profile_id"] == "fast4"
    try:
        resolve_profile("t2v", "legacy")
    except ValueError:
        pass
    else:
        raise AssertionError("T2V legacy profile should not exist in vNext")


def test_advanced_overrides_are_bounded():
    spec = resolve_profile("r2v", "tuned", {
        "steps": 999,
        "strength": -4,
        "shift_video": 999,
        "sampler": "euler",
        "ref_image_size": "match",
    })
    assert spec["steps"] == 50
    assert spec["strength"] == 0.0
    assert spec["shift_video"] == 30.0
    assert spec["ref_image_size"] == "match"


def test_fl2v_profiles_match_9d_and_do_not_expose_legacy_recipe():
    for mode in ("t2v", "i2v"):
        table = public_profiles(mode)
        assert set(table["profiles"]) == {"balanced8", "fast4"}
        balanced = table["profiles"]["balanced8"]
        fast = table["profiles"]["fast4"]
        assert (balanced["sampler"], balanced["scheduler"], balanced["steps"], balanced["shift_video"], balanced["shift_audio"]) == ("euler", "simple", 8, 6.0, 3.0)
        assert (fast["sampler"], fast["scheduler"], fast["steps"], fast["shift_video"], fast["shift_audio"]) == ("euler", "simple", 4, 6.0, 3.0)
