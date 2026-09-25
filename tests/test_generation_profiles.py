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


def test_r2v_profiles_match_post_profile_known_good_state():
    table = public_profiles("r2v")
    assert table["default"] == "balanced8"
    assert set(table["profiles"]) == {"balanced8", "fast4", "community"}
    balanced = table["profiles"]["balanced8"]
    fast = table["profiles"]["fast4"]
    community = table["profiles"]["community"]
    assert (balanced["sampler"], balanced["scheduler"], balanced["steps"], balanced["strength"]) == ("euler", "simple", 8, 1.0)
    assert (fast["sampler"], fast["schedule_type"], fast["steps"], fast["strength"]) == ("euler", "beta", 4, 0.85)
    assert (fast["beta_alpha"], fast["beta_beta"], fast["extend_steps"]) == (0.6, 0.6, 2)
    assert (balanced["shift_video"], balanced["shift_audio"]) == (12.0, 3.0)
    assert (fast["shift_video"], fast["shift_audio"]) == (12.0, 3.0)
    assert (community["sampler"], community["scheduler"], community["steps"], community["strength"]) == ("euler", "simple", 8, 0.85)
    assert community["sigma_shift_enabled"] is False
    assert community["extend_enabled"] is True
    assert (community["extend_steps"], community["extend_start"], community["extend_end"], community["extend_spacing"]) == (2, 0.8, 0.0, "linear")


def test_old_profile_aliases_map_to_current_fast_balanced_profiles():
    for mode in ("t2v", "i2v", "r2v"):
        assert resolve_profile(mode, "balanced")["profile_id"] == "balanced8"
        assert resolve_profile(mode, "fast")["profile_id"] == "fast4"
        assert resolve_profile(mode, "legacy")["profile_id"] == "fast4"


def test_advanced_overrides_are_bounded():
    spec = resolve_profile("r2v", "fast4", {
        "steps": 999,
        "strength": -4,
        "shift_video": 999,
        "sampler": "seeds_2",
        "ref_image_size": "match",
    })
    assert spec["steps"] == 50
    assert spec["strength"] == 0.0
    assert spec["shift_video"] == 30.0
    assert spec["sampler"] == "seeds_2"
    assert spec["ref_image_size"] == "match"


def test_fl2v_profiles_are_exactly_fast_and_balanced_from_9d():
    for mode in ("t2v", "i2v"):
        table = public_profiles(mode)
        assert table["default"] == "balanced8"
        assert set(table["profiles"]) == {"balanced8", "fast4"}
        balanced = table["profiles"]["balanced8"]
        fast = table["profiles"]["fast4"]
        community = table["profiles"]["community"]
        assert (balanced["sampler"], balanced["scheduler"], balanced["steps"], balanced["shift_video"], balanced["shift_audio"], balanced["strength"]) == ("euler", "simple", 8, 6.0, 3.0, 1.0)
        assert (fast["sampler"], fast["scheduler"], fast["steps"], fast["shift_video"], fast["shift_audio"], fast["strength"]) == ("euler", "simple", 4, 6.0, 3.0, 1.0)
        assert (community["sampler"], community["scheduler"], community["steps"], community["strength"]) == ("euler", "simple", 8, 0.8)
        assert community["sigma_shift_enabled"] is False
        assert community["extend_enabled"] is False
