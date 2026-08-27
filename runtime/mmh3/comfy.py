from __future__ import annotations

import os
import shutil
import time
from typing import Any

import requests

from .common import COMFY_DIR, COMFY_PERSIST

def configure_persistent_paths() -> None:
    for sub in ("models", "user", "input", "output"):
        (COMFY_PERSIST / sub).mkdir(parents=True, exist_ok=True)

    for sub in ("user", "input", "output"):
        src = COMFY_PERSIST / sub
        dst = COMFY_DIR / sub
        if dst.is_symlink():
            dst.unlink()
        elif dst.exists():
            if sub == "user":
                shutil.copytree(dst, src, dirs_exist_ok=True)
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        dst.symlink_to(src, target_is_directory=True)

    extra = COMFY_DIR / "extra_model_paths.yaml"
    extra.write_text(
        "network_volume:\n"
        f"    base_path: {COMFY_PERSIST}\n"
        '    checkpoints: "models/checkpoints"\n'
        '    configs: "models/configs"\n'
        '    loras: "models/loras"\n'
        '    vae: "models/vae"\n'
        '    text_encoders: "models/text_encoders\\nmodels/clip"\n'
        '    diffusion_models: "models/unet\\nmodels/diffusion_models"\n'
        '    clip_vision: "models/clip_vision"\n'
        '    style_models: "models/style_models"\n'
        '    embeddings: "models/embeddings"\n'
        '    diffusers: "models/diffusers"\n'
        '    vae_approx: "models/vae_approx"\n'
        '    controlnet: "models/controlnet\\nmodels/t2i_adapter"\n'
        '    gligen: "models/gligen"\n'
        '    upscale_models: "models/upscale_models"\n'
        '    latent_upscale_models: "models/latent_upscale_models"\n'
        '    hypernetworks: "models/hypernetworks"\n'
        '    photomaker: "models/photomaker"\n'
        '    classifiers: "models/classifiers"\n'
        '    model_patches: "models/model_patches"\n'
        '    audio_encoders: "models/audio_encoders"\n'
        '    background_removal: "models/background_removal"\n'
        '    frame_interpolation: "models/frame_interpolation"\n'
        '    geometry_estimation: "models/geometry_estimation"\n'
        '    optical_flow: "models/optical_flow"\n'
        '    detection: "models/detection"\n',
        encoding="utf-8",
    )

def base_url() -> str:
    port = int(os.environ.get("MMH3_COMFY_PORT", "8188"))
    return f"http://127.0.0.1:{port}"

def wait_ready(timeout: float = 180.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = requests.get(base_url() + "/system_stats", timeout=2)
            if r.ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False

def queue_state() -> dict[str, Any]:
    try:
        r = requests.get(base_url() + "/queue", timeout=3)
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        return {"queue_running": [], "queue_pending": []}

def queue_idle() -> bool:
    q = queue_state()
    return not q.get("queue_running") and not q.get("queue_pending")

def free_memory(unload_models: bool = True, free_memory_flag: bool = True) -> bool:
    try:
        r = requests.post(
            base_url() + "/free",
            json={"unload_models": unload_models, "free_memory": free_memory_flag},
            timeout=5,
        )
        return r.ok
    except requests.RequestException:
        return False

def interrupt() -> bool:
    try:
        r = requests.post(base_url() + "/interrupt", timeout=5)
        return r.ok
    except requests.RequestException:
        return False
