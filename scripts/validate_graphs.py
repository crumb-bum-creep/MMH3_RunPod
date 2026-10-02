"""Validate every mode x recipe graph against a running ComfyUI.

    python scripts/validate_graphs.py --comfy http://127.0.0.1:8188 [--setup-dummies /path/to/ComfyUI]

ComfyUI validates a prompt fully (node types, input names, types, enum values,
links) before queueing it. A graph that passes here will be accepted by the pod.
Valid prompts are deleted from the queue immediately; nothing is generated.
With --setup-dummies, empty placeholder model files and inputs are created so
enum checks pass on a machine without the real models (CI / local).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio import graph, recipes, util  # noqa: E402

LORA_TEST = "test_user_lora.safetensors"


def files_for(family: str, recipe: dict) -> dict[str, str]:
    models = util.image_yaml("models.yaml")["models"]
    return {
        "unet": models["fl2va_int8" if family == "fl2v" else "ref2va_int8"]["file"],
        "text_encoder": models["text_encoder"]["file"],
        "video_vae": models["video_vae"]["file"],
        "audio_vae": models["audio_vae"]["file"],
        "turbo": models[recipe["lora"]]["file"],
        "preview_vae": models["taeh3"]["file"],
    }


def jobs():
    base = dict(prompt="integrated_multimodal_description: [Shot 1] test.\n\noverall_soundscape: quiet.\n\nnon_diegetic_music: N/A",
                aspect="9:16", megapixels=0.7, duration=5, seed=123, crf=12, output_prefix="MMH3/TEST",
                loras=[{"file": LORA_TEST, "strength": 0.6}])
    for mode in ("t2v", "i2v", "r2v"):
        fam = recipes.family_for(mode)
        for rid in recipes.catalog()[fam]["recipes"]:
            job = dict(base, mode=mode, recipe=recipes.resolve(mode, rid))
            if mode == "i2v":
                job["start_image"] = "mmh3/validate_start.png"
                job["end_image"] = "mmh3/validate_end.png"
            if mode == "r2v":
                job["refs"] = [{"kind": "image", "file": "mmh3/validate_start.png"},
                               {"kind": "video", "file": "mmh3/validate_clip.mp4"},
                               {"kind": "audio", "file": "mmh3/validate_voice.wav"}]
            yield f"{mode}/{rid}", job, files_for(fam, job["recipe"])
            if mode == "i2v":  # end frame only (L2V): last_frame without first_frame
                end_only = {k: v for k, v in job.items() if k != "start_image"}
                yield f"{mode}-end-only/{rid}", end_only, files_for(fam, job["recipe"])


def setup_dummies(comfy_root: Path) -> None:
    models = util.image_yaml("models.yaml")["models"]
    for entry in models.values():
        p = comfy_root / "models" / entry["file"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    (comfy_root / "models" / "loras" / LORA_TEST).touch()
    inp = comfy_root / "input" / "mmh3"
    inp.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    for name in ("validate_start.png", "validate_end.png"):
        Image.new("RGB", (64, 64), (80, 120, 160)).save(inp / name)
    (inp / "validate_clip.mp4").touch()
    (inp / "validate_voice.wav").touch()


def post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--setup-dummies", type=Path)
    ap.add_argument("--dump", type=Path, help="write each graph as JSON into this folder")
    args = ap.parse_args()
    if args.setup_dummies:
        setup_dummies(args.setup_dummies)
    failures = 0
    for name, job, files in jobs():
        prompt = graph.build(job, files)
        if args.dump:
            args.dump.mkdir(parents=True, exist_ok=True)
            (args.dump / (name.replace("/", "_") + ".json")).write_text(json.dumps(prompt, indent=1))
        status, body = post(args.comfy + "/prompt", {"prompt": prompt, "client_id": "validate"})
        if status == 200 and not body.get("node_errors"):
            post(args.comfy + "/queue", {"delete": [body.get("prompt_id")]})
            post(args.comfy + "/interrupt", {})
            print(f"PASS {name:22} {len(prompt)} nodes")
        else:
            failures += 1
            print(f"FAIL {name:22} {json.dumps(body)[:1500]}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
