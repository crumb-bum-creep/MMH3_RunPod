"""Build a throwaway /workspace that looks like an existing MMH3 volume.

    python scripts/dev_workspace.py /tmp/ws [--comfy-port 8189]

Sparse placeholder model files (no real disk use), the seed LoRA catalog with a
few "installed" files, legacy library/meta files from the previous build, a few
legacy outputs, and some input media. Used for local UI work and tests.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def sparse(path: Path, mb: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.truncate(int(mb * 1024 * 1024))


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--comfy-port", type=int, default=8189)
    ap.add_argument("--missing", action="append", default=[], help="model ids to leave missing")
    a = ap.parse_args()
    ws: Path = a.root
    if ws.exists():
        shutil.rmtree(ws)
    import yaml

    models = yaml.safe_load((ROOT / "config/models.yaml").read_text())["models"]
    for mid, e in models.items():
        if mid in a.missing or e.get("group") == "eros":
            continue
        sparse(ws / "ComfyUI/models" / e["file"], e.get("min_mb", 1) + 1)
    loras = ws / "ComfyUI/models/loras"
    for name in ("minimax-h3-digicam.safetensors", "MysticXXX_MMH3-V4.safetensors", "HMNSFW-AIO-V2.5.safetensors",
                 "my_local_experiment.safetensors"):
        sparse(loras / name, 2)

    cfg = ws / "mmh3/config"
    cfg.mkdir(parents=True)
    shutil.copy(ROOT / "config/loras.seed.yaml", cfg / "loras.yaml")
    (cfg / "settings.yaml").write_text(yaml.safe_dump({"ports": {"comfy": a.comfy_port}}))

    out = ws / "ComfyUI/output/MMH3"
    out.mkdir(parents=True)
    legacy_meta = {}
    for i, (mode, hue) in enumerate([("T2V", 10), ("I2V", 140), ("R2V", 260), ("T2V", 320)], start=1):
        stem = out / f"{mode}_{i:05d}"
        src = ["-f", "lavfi", "-i", "testsrc2=size=360x640:rate=24:duration=2"]
        ff(*src, "-vf", f"hue=h={hue}:s=1.4", "-pix_fmt", "yuv420p", f"{stem}.mp4")
        ff(*src, "-f", "lavfi", "-i", "sine=frequency=220:duration=2", "-vf", f"hue=h={hue}:s=1.4", "-pix_fmt", "yuv420p",
           "-shortest", f"{stem}-audio.mp4")
        ff("-i", f"{stem}.mp4", "-frames:v", "1", f"{stem}.png")
        legacy_meta[f"MMH3/{mode}_{i:05d}-audio.mp4"] = {
            "mode": mode.lower(), "prompt_mode": "auto" if i % 2 else "custom", "prompt_idea": f"legacy idea {i}",
            "prompt": "" if i % 2 else f"integrated_multimodal_description: legacy prompt {i}",
            "actual_prompt": f"integrated_multimodal_description: [Shot 1] legacy clip {i}.",
            "aspect_ratio": "9:16 (Portrait Widescreen)", "megapixels": 0.7, "duration": 5, "seed": 1000 + i,
            "loras": [{"filename": "minimax-h3-digicam.safetensors", "strength": 1.1}],
            "base_checkpoint": "stock_convrot_int8", "generation_profile": "fast" if mode == "R2V" else "balanced",
        }
    data = ws / "mmh3/data"
    data.mkdir(parents=True)
    (data / "output_meta.json").write_text(json.dumps(legacy_meta))
    (data / "output_library.json").write_text(json.dumps({
        "groups": ["Keepers", "Tests"],
        "videos": {"MMH3/T2V_00001-audio.mp4": {"group": "Keepers", "tags": ["night"], "favorite": True}}}))

    inp = ws / "ComfyUI/input/mmh3/2026-09"
    inp.mkdir(parents=True)
    from PIL import Image, ImageDraw

    for i, col in enumerate([(200, 120, 90), (70, 130, 190), (150, 180, 90)], start=1):
        im = Image.new("RGB", (768, 1152), col)
        ImageDraw.Draw(im).ellipse([200, 300, 568, 668], fill=(240, 230, 210))
        im.save(inp / f"portrait_{i}.png")
    ff("-f", "lavfi", "-i", "testsrc2=size=480x270:rate=24:duration=3", "-f", "lavfi", "-i",
       "sine=frequency=440:duration=3", "-pix_fmt", "yuv420p", "-shortest", str(inp / "camera_move.mp4"))
    ff("-f", "lavfi", "-i", "testsrc2=size=480x270:rate=24:duration=3", "-pix_fmt", "yuv420p", str(inp / "silent_clip.mp4"))
    ff("-f", "lavfi", "-i", "sine=frequency=180:duration=3", str(inp / "voice.wav"))
    (data / "assets.json").write_text(json.dumps({"mmh3/2026-09/portrait_1.png": {"nickname": "Maya front"}}))
    print(f"workspace ready at {ws}")


if __name__ == "__main__":
    main()
