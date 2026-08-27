from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from huggingface_hub import hf_hub_download

from .common import COMFY_PERSIST, load_yaml

def _usable(path: Path, min_size_mb: float) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= int(min_size_mb * 1024 * 1024)
    except OSError:
        return False

def sync_models(config_path: Path) -> list[dict[str, Any]]:
    cfg = load_yaml(config_path, {}) or {}
    entries = cfg.get("models", {}) or {}
    root = COMFY_PERSIST / "models"
    root.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN") or None
    results: list[dict[str, Any]] = []

    for name, item in entries.items():
        item = item or {}
        dest = root / item["destination"]
        minimum = float(item.get("min_size_mb", 1))
        if _usable(dest, minimum):
            results.append({"name": name, "status": "present", "path": str(dest)})
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if item.get("source") != "huggingface":
                raise ValueError(f"unsupported model source: {item.get('source')}")
            got = Path(
                hf_hub_download(
                    repo_id=item["repo_id"],
                    filename=item["source_path"],
                    local_dir=root,
                    token=token,
                )
            )
            # Some upstream repositories keep a model at repository root while
            # Comfy expects it under a model-type subdirectory (e.g. loras/).
            # The manifest's destination is authoritative.
            if got.resolve() != dest.resolve():
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    dest.unlink()
                got.replace(dest)
            if not _usable(dest, minimum):
                raise RuntimeError(f"download returned {got}, expected usable {dest}")
            results.append({"name": name, "status": "downloaded", "path": str(dest)})
        except Exception as exc:
            results.append({"name": name, "status": "error", "error": repr(exc), "path": str(dest)})
    return results
