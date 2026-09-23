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


def test_r2v_known_good_profiles_are_legacy_and_balanced():
    table = public_profiles("r2v")
    assert table["default"] == "legacy_exact"
    assert set(table["profiles"]) == {"legacy_exact", "balanced8"}
    legacy = table["profiles"]["legacy_exact"]
    balanced = table["profiles"]["balanced8"]
    assert (legacy["sampler"], legacy["schedule_type"], legacy["steps"], legacy["strength"]) == ("seeds_2", "beta", 4, 0.85)
    assert (balanced["sampler"], balanced["scheduler"], balanced["steps"], balanced["strength"]) == ("euler", "simple", 8, 1.0)
    assert (legacy["shift_video"], legacy["shift_audio"]) == (12.0, 3.0)
    assert (balanced["shift_video"], balanced["shift_audio"]) == (12.0, 3.0)


def test_old_profile_aliases_map_without_rewriting_history():
    assert resolve_profile("r2v", "fast")["profile_id"] == "legacy_exact"
    assert resolve_profile("t2v", "fast")["profile_id"] == "fast4"
    assert resolve_profile("i2v", "fast")["profile_id"] == "fast4"
    assert resolve_profile("t2v", "legacy")["profile_id"] == "legacy_exact"
    assert resolve_profile("i2v", "legacy")["profile_id"] == "legacy_exact"


def test_advanced_overrides_are_bounded():
    spec = resolve_profile("r2v", "legacy_exact", {
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


def test_fl2v_known_good_profiles_and_legacy_option():
    for mode in ("t2v", "i2v"):
        table = public_profiles(mode)
        assert table["default"] == "balanced8"
        assert set(table["profiles"]) == {"balanced8", "fast4", "legacy_exact"}
        balanced = table["profiles"]["balanced8"]
        fast = table["profiles"]["fast4"]
        legacy = table["profiles"]["legacy_exact"]
        assert (balanced["sampler"], balanced["scheduler"], balanced["steps"], balanced["shift_video"], balanced["shift_audio"], balanced["strength"]) == ("euler", "simple", 8, 6.0, 3.0, 1.0)
        assert (fast["sampler"], fast["scheduler"], fast["steps"], fast["shift_video"], fast["shift_audio"], fast["strength"]) == ("euler", "simple", 4, 6.0, 3.0, 1.0)
        assert (legacy["sampler"], legacy["schedule_type"], legacy["steps"], legacy["strength"]) == ("euler", "beta", 8, 0.5)
        assert (legacy["beta_alpha"], legacy["beta_beta"], legacy["extend_steps"], legacy["extend_start"], legacy["extend_end"]) == (0.79, 0.5, 3, 0.8, 0.0)
