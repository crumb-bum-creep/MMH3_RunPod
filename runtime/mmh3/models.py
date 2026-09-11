from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from huggingface_hub import get_hf_file_metadata, hf_hub_download, hf_hub_url
from tqdm.auto import tqdm

from .common import COMFY_PERSIST, load_yaml

ProgressCallback = Callable[[str, dict[str, Any]], None]


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


def _expected_bytes(item: dict[str, Any], token: str | None) -> int | None:
    try:
        url = hf_hub_url(repo_id=item["repo_id"], filename=item["source_path"])
        meta = get_hf_file_metadata(url, token=token, timeout=15)
        return int(meta.size) if meta.size is not None else None
    except Exception:
        return None


def sync_models(
    config_path: Path,
    *,
    phase: str | None = None,
    progress: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    cfg = load_yaml(config_path, {}) or {}
    entries = list((cfg.get("models", {}) or {}).items())
    if phase is not None:
        entries = [
            (name, item or {})
            for name, item in entries
            if str((item or {}).get("phase") or "core") == phase
        ]

    root = COMFY_PERSIST / "models"
    root.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN") or None

    def emit(name: str, **values: Any) -> None:
        if progress is not None:
            progress(name, values)

    def sync_one(name: str, item: dict[str, Any]) -> dict[str, Any]:
        item = item or {}
        dest = root / item["destination"]
        minimum = float(item.get("min_size_mb", 1))
        expected = _expected_bytes(item, token)
        common = {
            "label": str(item.get("label") or name),
            "destination": str(item.get("destination") or ""),
            "phase": str(item.get("phase") or "core"),
            "show_in_ui": bool(item.get("show_in_ui", not str(item.get("destination") or "").startswith("loras/"))),
            "total_bytes": expected,
        }

        if _usable(dest, minimum):
            size = dest.stat().st_size
            emit(name, **common, status="ready", downloaded_bytes=size, total_bytes=expected or size, speed_bps=0.0)
            return {"name": name, "status": "present", "path": str(dest), "bytes": size}

        dest.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        tracker = {"last_t": started, "last_n": 0, "last_emit": 0.0}
        emit(name, **common, status="starting", downloaded_bytes=0, speed_bps=0.0)

        class ProgressTqdm(tqdm):
            def __init__(self, *args, **kwargs):
                # Hugging Face normally lets tqdm auto-disable itself when there is
                # no TTY. Provisioning is deliberately a background process, so
                # force callbacks to remain active for the Phone UI telemetry.
                kwargs["disable"] = False
                super().__init__(*args, **kwargs)
                current = int(getattr(self, "n", 0) or 0)
                total = int(getattr(self, "total", 0) or expected or 0) or expected
                tracker["last_n"] = current
                tracker["last_t"] = time.monotonic()
                emit(name, **common, status="downloading", downloaded_bytes=current, total_bytes=total, speed_bps=0.0)

            def update(self, n=1):
                out = super().update(n)
                now = time.monotonic()
                if now - tracker["last_emit"] >= 0.4:
                    current = int(getattr(self, "n", 0) or 0)
                    total = int(getattr(self, "total", 0) or expected or 0) or expected
                    dt = max(1e-6, now - tracker["last_t"])
                    speed = max(0.0, (current - tracker["last_n"]) / dt)
                    tracker["last_t"] = now
                    tracker["last_n"] = current
                    tracker["last_emit"] = now
                    emit(
                        name,
                        **common,
                        status="downloading",
                        downloaded_bytes=current,
                        total_bytes=total,
                        speed_bps=speed,
                    )
                return out

        try:
            if item.get("source") != "huggingface":
                raise ValueError(f"unsupported model source: {item.get('source')}")
            got = Path(
                hf_hub_download(
                    repo_id=item["repo_id"],
                    filename=item["source_path"],
                    local_dir=root,
                    token=token,
                    tqdm_class=ProgressTqdm,
                )
            )
            if got.resolve() != dest.resolve():
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    dest.unlink()
                got.replace(dest)
            if not _usable(dest, minimum):
                raise RuntimeError(f"download returned {got}, expected usable {dest}")
            size = dest.stat().st_size
            elapsed = max(1e-6, time.monotonic() - started)
            emit(
                name,
                **common,
                status="ready",
                downloaded_bytes=size,
                total_bytes=expected or size,
                speed_bps=size / elapsed,
            )
            return {"name": name, "status": "downloaded", "path": str(dest), "bytes": size}
        except Exception as exc:
            emit(name, **common, status="error", error=repr(exc), speed_bps=0.0)
            return {"name": name, "status": "error", "error": repr(exc), "path": str(dest)}

    workers = _workers()
    if workers == 1 or len(entries) <= 1:
        return [sync_one(name, item) for name, item in entries]

    by_name: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(entries)), thread_name_prefix="mmh3-model") as pool:
        future_to_name = {pool.submit(sync_one, name, item): name for name, item in entries}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            by_name[name] = future.result()

    return [by_name[name] for name, _ in entries]
