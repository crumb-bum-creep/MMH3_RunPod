from __future__ import annotations

from pathlib import Path

import yaml

from mmh3.bootstrap import _migrate_runtime_config


def test_v1_runtime_config_migrates_dynamic_vram_and_preserves_overrides(tmp_path: Path):
    src = tmp_path / "image-runtime.yaml"
    dst = tmp_path / "persistent-runtime.yaml"

    src.write_text(
        yaml.safe_dump({
            "version": 2,
            "comfy": {"disable_dynamic_vram": False, "use_sage_attention": True},
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

    assert value["version"] == 2
    assert value["comfy"]["disable_dynamic_vram"] is False
    assert value["comfy"]["use_sage_attention"] is True
    assert value["memory"]["poll_seconds"] == 11
    assert value["memory"]["warn_fraction"] == 0.78
    assert value["provisioning"]["warm_start_grace_seconds"] == 8
    assert (tmp_path / "persistent-runtime.yaml.v1.bak").is_file()


def test_v2_user_dynamic_vram_override_is_preserved(tmp_path: Path):
    src = tmp_path / "image-runtime.yaml"
    dst = tmp_path / "persistent-runtime.yaml"

    src.write_text(
        yaml.safe_dump({"version": 2, "comfy": {"disable_dynamic_vram": False}}),
        encoding="utf-8",
    )
    dst.write_text(
        yaml.safe_dump({"version": 2, "comfy": {"disable_dynamic_vram": True}}),
        encoding="utf-8",
    )

    _migrate_runtime_config(src, dst)
    value = yaml.safe_load(dst.read_text())
    assert value["comfy"]["disable_dynamic_vram"] is True
