from __future__ import annotations

from pathlib import Path

import yaml

from mmh3.bootstrap import _migrate_runtime_config


def test_v1_runtime_config_migrates_to_reproducible_dynamic_vram_policy(tmp_path: Path):
    src = tmp_path / "image-runtime.yaml"
    dst = tmp_path / "persistent-runtime.yaml"

    src.write_text(
        yaml.safe_dump({
            "version": 3,
            "comfy": {"disable_dynamic_vram": True, "use_sage_attention": True},
            "memory": {"poll_seconds": 5, "warn_fraction": 0.78},
            "provisioning": {"warm_start_grace_seconds": 8},
        }),
        encoding="utf-8",
    )
    dst.write_text(
        yaml.safe_dump({
            "version": 1,
            "comfy": {"disable_dynamic_vram": True},
            "memory": {"poll_seconds": 11},
        }),
        encoding="utf-8",
    )

    _migrate_runtime_config(src, dst)
    value = yaml.safe_load(dst.read_text())

    assert value["version"] == 3
    assert value["comfy"]["disable_dynamic_vram"] is True
    assert value["comfy"]["use_sage_attention"] is True
    assert value["memory"]["poll_seconds"] == 11
    assert value["memory"]["warn_fraction"] == 0.78
    assert value["provisioning"]["warm_start_grace_seconds"] == 8
    assert (tmp_path / "persistent-runtime.yaml.v1.bak").is_file()


def test_v2_dynamic_vram_default_is_migrated_back_to_known_good_mode(tmp_path: Path):
    src = tmp_path / "image-runtime.yaml"
    dst = tmp_path / "persistent-runtime.yaml"

    src.write_text(
        yaml.safe_dump({"version": 3, "comfy": {"disable_dynamic_vram": True}}),
        encoding="utf-8",
    )
    dst.write_text(
        yaml.safe_dump({"version": 2, "comfy": {"disable_dynamic_vram": False}}),
        encoding="utf-8",
    )

    _migrate_runtime_config(src, dst)
    value = yaml.safe_load(dst.read_text())

    assert value["version"] == 3
    assert value["comfy"]["disable_dynamic_vram"] is True
    assert (tmp_path / "persistent-runtime.yaml.v2.bak").is_file()


def test_v3_user_dynamic_vram_override_is_preserved(tmp_path: Path):
    src = tmp_path / "image-runtime.yaml"
    dst = tmp_path / "persistent-runtime.yaml"

    src.write_text(
        yaml.safe_dump({"version": 3, "comfy": {"disable_dynamic_vram": True}}),
        encoding="utf-8",
    )
    dst.write_text(
        yaml.safe_dump({"version": 3, "comfy": {"disable_dynamic_vram": False}}),
        encoding="utf-8",
    )

    _migrate_runtime_config(src, dst)
    value = yaml.safe_load(dst.read_text())
    assert value["comfy"]["disable_dynamic_vram"] is False
