"""Every filesystem location MMH3 Studio touches, in one place.

Persistent state lives on the network volume under /workspace and survives
image upgrades. Code lives in the image. The layout matches the previous MMH3
image so an existing volume keeps its models, outputs and catalogs.
"""
from __future__ import annotations

import os
from pathlib import Path

IMAGE_ROOT = Path(os.environ.get("MMH3_IMAGE_ROOT", Path(__file__).resolve().parent.parent))
IMAGE_CONFIG = IMAGE_ROOT / "config"
WEB_ROOT = Path(__file__).resolve().parent / "web"

WORKSPACE = Path(os.environ.get("MMH3_WORKSPACE", "/workspace"))
COMFY_CODE = Path(os.environ.get("MMH3_COMFY_CODE", "/ComfyUI"))

COMFY_DATA = WORKSPACE / "ComfyUI"
MODELS = COMFY_DATA / "models"
INPUT = COMFY_DATA / "input"
OUTPUT = COMFY_DATA / "output"
COMFY_USER = COMFY_DATA / "user"
COMFY_TEMP = COMFY_DATA / "temp"

STATE_ROOT = WORKSPACE / "mmh3"
CONFIG = STATE_ROOT / "config"          # user-editable config (yaml)
DATA = STATE_ROOT / "data"              # app-managed json
STATE = STATE_ROOT / "state"            # live status written by the supervisor
LOGS = STATE_ROOT / "logs"
THUMBS = DATA / "thumbs"

# Where studio writes files it creates for ComfyUI to read (uploads, last frames).
UPLOAD_SUBDIR = "mmh3"
KIT_SUBDIR = "mmh3/kits"


def ensure_dirs() -> None:
    for d in (MODELS, INPUT, OUTPUT, COMFY_USER, COMFY_TEMP, CONFIG, DATA, STATE, LOGS, THUMBS,
              INPUT / UPLOAD_SUBDIR, INPUT / KIT_SUBDIR):
        d.mkdir(parents=True, exist_ok=True)
    for sub in ("diffusion_models", "text_encoders", "vae", "vae_approx", "loras"):
        (MODELS / sub).mkdir(parents=True, exist_ok=True)
