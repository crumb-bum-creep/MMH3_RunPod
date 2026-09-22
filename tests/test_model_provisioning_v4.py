from __future__ import annotations

import json
from pathlib import Path

import yaml

from mmh3 import comfy, models


def _manifest(tmp_path: Path, destination: str = "diffusion_models/core.bin") -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "models": {
                    "core": {
                        "source": "huggingface",
                        "repo_id": "example/repo",
                        "source_path": "core.bin",
                        "destination": destination,
                        "min_size_mb": 0.000001,
                        "phase": "core",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_local_inventory_reuses_verified_persistent_model(monkeypatch, tmp_path):
    comfy = tmp_path / "ComfyUI"
    data = tmp_path / "data"
    dest = comfy / "models" / "diffusion_models" / "core.bin"
    dest.parent.mkdir(parents=True)
    data.mkdir()
    dest.write_bytes(b"12345678")

    (data / "provisioning_report.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "name": "core",
                        "path": str(dest),
                        "bytes": 8,
                        "expected_bytes": 8,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(models, "COMFY_PERSIST", comfy)
    monkeypatch.setattr(models, "DATA_ROOT", data)
    progress = models.local_model_progress(_manifest(tmp_path))

    assert progress["core"]["status"] == "ready"
    assert models.phase_ready(progress, "core") is True


def test_local_inventory_flags_size_drift_even_when_above_minimum(monkeypatch, tmp_path):
    comfy = tmp_path / "ComfyUI"
    data = tmp_path / "data"
    dest = comfy / "models" / "diffusion_models" / "core.bin"
    dest.parent.mkdir(parents=True)
    data.mkdir()
    dest.write_bytes(b"123456789")

    (data / "provisioning_report.json").write_text(
        json.dumps({"models": [{"name": "core", "path": str(dest), "bytes": 8}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(models, "COMFY_PERSIST", comfy)
    monkeypatch.setattr(models, "DATA_ROOT", data)
    progress = models.local_model_progress(_manifest(tmp_path))

    assert progress["core"]["status"] == "verify"
    assert models.phase_ready(progress, "core") is False


def test_local_inventory_detects_broken_symlink(monkeypatch, tmp_path):
    comfy = tmp_path / "ComfyUI"
    data = tmp_path / "data"
    dest = comfy / "models" / "diffusion_models" / "core.bin"
    dest.parent.mkdir(parents=True)
    data.mkdir()
    dest.symlink_to(tmp_path / "does-not-exist.bin")

    monkeypatch.setattr(models, "COMFY_PERSIST", comfy)
    monkeypatch.setattr(models, "DATA_ROOT", data)
    progress = models.local_model_progress(_manifest(tmp_path))

    assert progress["core"]["status"] == "broken"


def test_usable_requires_exact_expected_size_when_known(tmp_path):
    path = tmp_path / "model.bin"
    path.write_bytes(b"12345678")

    assert models._usable(path, 0.000001, 8)
    assert not models._usable(path, 0.000001, 9)


def test_comfy_visibility_detects_ready_models_missing_from_selector_cache(monkeypatch):
    monkeypatch.setattr(comfy, "object_info", lambda timeout=10.0: {
        "CLIPLoader": {
            "input": {
                "required": {
                    "clip_name": [["other-clip.safetensors"]]
                }
            }
        },
        "UNETLoader": {
            "input": {
                "required": {
                    "unet_name": [["model-ok.safetensors"]]
                }
            }
        },
        "VAELoader": {
            "input": {
                "required": {
                    "vae_name": [["video-vae.safetensors"]]
                }
            }
        },
        "LoraLoaderModelOnly": {
            "input": {
                "required": {
                    "lora_name": [["turbo-ok.safetensors"]]
                }
            }
        },
    })
    progress = {
        "clip": {
            "destination": "text_encoders/qwen.safetensors",
            "status": "ready",
        },
        "unet": {
            "destination": "diffusion_models/model-ok.safetensors",
            "status": "ready",
        },
        "vae": {
            "destination": "vae/video-vae.safetensors",
            "status": "ready",
        },
        "turbo": {
            "destination": "loras/turbo-ok.safetensors",
            "status": "ready",
        },
        "waiting": {
            "destination": "loras/not-ready.safetensors",
            "status": "waiting",
        },
    }

    assert comfy.missing_ready_model_choices(progress) == ["qwen.safetensors"]
