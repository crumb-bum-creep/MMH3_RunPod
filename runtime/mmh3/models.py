from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from huggingface_hub import hf_hub_download

from .common import COMFY_PERSIST, load_yaml


def _usable(path: Path, min_size_mb: float) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= int(min_size_mb * 1024 * 1024)
    except OSError:
        return False


def _workers() -> int:
    try:
        return max(1, min(8, int(os.environ.get("MMH3_MODEL_DOWNLOAD_WORKERS", "3"))))
    except ValueError:
        return 3


def sync_models(config_path: Path) -> list[dict[str, Any]]:
    cfg = load_yaml(config_path, {}) or {}
    entries = list((cfg.get("models", {}) or {}).items())
    root = COMFY_PERSIST / "models"
    root.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN") or None

    def sync_one(name: str, item: dict[str, Any]) -> dict[str, Any]:
        item = item or {}
        dest = root / item["destination"]
        minimum = float(item.get("min_size_mb", 1))
        if _usable(dest, minimum):
            return {"name": name, "status": "present", "path": str(dest)}
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
            return {"name": name, "status": "downloaded", "path": str(dest)}
        except Exception as exc:
            return {"name": name, "status": "error", "error": repr(exc), "path": str(dest)}

    workers = _workers()
    if workers == 1 or len(entries) <= 1:
        return [sync_one(name, item) for name, item in entries]

    # Large H3 weights are independent files. A small amount of concurrency
    # substantially improves fresh-volume provisioning on high-bandwidth hosts
    # without launching enough parallel transfers to thrash the volume.
    by_name: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(entries)), thread_name_prefix="mmh3-model") as pool:
        future_to_name = {pool.submit(sync_one, name, item): name for name, item in entries}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            by_name[name] = future.result()

    # Keep manifest order stable in state/log output.
    return [by_name[name] for name, _ in entries]
