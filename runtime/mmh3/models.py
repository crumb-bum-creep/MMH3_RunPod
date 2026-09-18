from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from huggingface_hub import get_hf_file_metadata, hf_hub_download, hf_hub_url
from tqdm.auto import tqdm

from .common import COMFY_PERSIST, DATA_ROOT, load_yaml

ProgressCallback = Callable[[str, dict[str, Any]], None]


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _usable(path: Path, min_size_mb: float, expected_bytes: int | None = None) -> bool:
    size = _file_size(path)
    minimum = int(min_size_mb * 1024 * 1024)
    if size < minimum:
        return False
    return expected_bytes is None or size == expected_bytes


def _prior_model_rows() -> dict[str, dict[str, Any]]:
    try:
        report = json.loads((DATA_ROOT / "provisioning_report.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in report.get("models") or []:
        if isinstance(row, dict) and row.get("name"):
            out[str(row["name"])] = row
    return out


def local_model_progress(config_path: Path) -> dict[str, dict[str, Any]]:
    """Fast, network-free startup inventory for persistent /workspace volumes."""
    cfg = load_yaml(config_path, {}) or {}
    root = COMFY_PERSIST / "models"
    prior = _prior_model_rows()
    out: dict[str, dict[str, Any]] = {}

    for name, raw in (cfg.get("models") or {}).items():
        item = raw or {}
        dest_rel = str(item.get("destination") or "")
        dest = root / dest_rel
        minimum = int(float(item.get("min_size_mb", 1)) * 1024 * 1024)
        size = _file_size(dest)
        broken = dest.is_symlink() and not dest.exists()

        old = prior.get(str(name), {})
        old_path = str(old.get("path") or "")
        old_size = int(old.get("bytes") or old.get("expected_bytes") or 0)
        known_size = old_size if old_path == str(dest) and old_size > 0 else 0

        ready = size >= minimum and (not known_size or size == known_size)
        if ready:
            status = "ready"
        elif broken:
            status = "broken"
        elif size:
            status = "verify"
        else:
            status = "waiting"

        out[str(name)] = {
            "label": str(item.get("label") or name),
            "destination": dest_rel,
            "phase": str(item.get("phase") or "core"),
            "show_in_ui": bool(item.get("show_in_ui", not dest_rel.startswith("loras/"))),
            "status": status,
            "downloaded_bytes": size,
            "total_bytes": known_size or (size if ready else None),
            "speed_bps": 0.0,
        }
    return out


def phase_ready(progress: dict[str, dict[str, Any]], phase: str) -> bool:
    rows = [row for row in progress.values() if str(row.get("phase") or "core") == phase]
    return bool(rows) and all(row.get("status") == "ready" for row in rows)


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


def _stable_for_repair(path: Path) -> bool:
    """Avoid deleting a file that RunPod is still restoring into the volume."""
    if not path.exists() or not path.is_file():
        return True
    try:
        delay = max(0.0, min(10.0, float(os.environ.get("MMH3_MODEL_SETTLE_SECONDS", "3"))))
    except ValueError:
        delay = 3.0
    if delay <= 0:
        return True
    before = _file_size(path)
    time.sleep(delay)
    after = _file_size(path)
    return bool(after) and before == after


def _touch_parent(path: Path) -> None:
    # Comfy's filename cache is directory-mtime based. Touching the model
    # directory makes newly restored/downloaded files visible without a restart.
    try:
        os.utime(path.parent, None)
    except OSError:
        pass


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
    prior = _prior_model_rows()

    def emit(name: str, **values: Any) -> None:
        if progress is not None:
            progress(name, values)

    def sync_one(name: str, item: dict[str, Any]) -> dict[str, Any]:
        item = item or {}
        dest = root / item["destination"]
        minimum = float(item.get("min_size_mb", 1))
        remote_expected = _expected_bytes(item, token)

        old = prior.get(name, {})
        old_path = str(old.get("path") or "")
        old_expected = int(old.get("expected_bytes") or old.get("bytes") or 0)
        prior_expected = old_expected if old_path == str(dest) and old_expected > 0 else None
        expected = remote_expected or prior_expected

        common = {
            "label": str(item.get("label") or name),
            "destination": str(item.get("destination") or ""),
            "phase": str(item.get("phase") or "core"),
            "show_in_ui": bool(item.get("show_in_ui", not str(item.get("destination") or "").startswith("loras/"))),
        }

        if _usable(dest, minimum, expected):
            size = dest.stat().st_size
            _touch_parent(dest)
            emit(name, **common, status="ready", downloaded_bytes=size, total_bytes=expected or size, speed_bps=0.0)
            return {
                "name": name,
                "status": "present",
                "path": str(dest),
                "bytes": size,
                "expected_bytes": expected or size,
            }

        current = _file_size(dest)
        if current and expected and current != expected and not _stable_for_repair(dest):
            exc = RuntimeError(
                f"{dest} is still changing ({current} bytes); persistent volume restore may still be in progress"
            )
            emit(
                name,
                **common,
                status="waiting",
                downloaded_bytes=_file_size(dest),
                total_bytes=expected,
                error=str(exc),
                speed_bps=0.0,
            )
            return {
                "name": name,
                "status": "error",
                "retryable": True,
                "error": repr(exc),
                "path": str(dest),
                "bytes": _file_size(dest),
                "expected_bytes": expected,
            }

        dest.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(dest) and not _usable(dest, minimum, expected):
            if dest.is_dir() and not dest.is_symlink():
                exc = RuntimeError(f"model destination is unexpectedly a directory: {dest}")
                emit(name, **common, status="error", error=repr(exc), total_bytes=expected, speed_bps=0.0)
                return {"name": name, "status": "error", "error": repr(exc), "path": str(dest)}
            try:
                dest.unlink()
            except OSError as exc:
                emit(name, **common, status="error", error=repr(exc), total_bytes=expected, speed_bps=0.0)
                return {"name": name, "status": "error", "error": repr(exc), "path": str(dest)}

        started = time.monotonic()
        tracker = {"last_t": started, "last_n": 0, "last_emit": 0.0}
        emit(name, **common, status="starting", downloaded_bytes=0, total_bytes=expected, speed_bps=0.0)

        class ProgressTqdm(tqdm):
            def __init__(self, *args, **kwargs):
                kwargs["disable"] = False
                super().__init__(*args, **kwargs)
                current_n = int(getattr(self, "n", 0) or 0)
                total = int(getattr(self, "total", 0) or expected or 0) or expected
                tracker["last_n"] = current_n
                tracker["last_t"] = time.monotonic()
                emit(name, **common, status="downloading", downloaded_bytes=current_n, total_bytes=total, speed_bps=0.0)

            def update(self, n=1):
                out = super().update(n)
                now = time.monotonic()
                if now - tracker["last_emit"] >= 0.4:
                    current_n = int(getattr(self, "n", 0) or 0)
                    total = int(getattr(self, "total", 0) or expected or 0) or expected
                    dt = max(1e-6, now - tracker["last_t"])
                    speed = max(0.0, (current_n - tracker["last_n"]) / dt)
                    tracker["last_t"] = now
                    tracker["last_n"] = current_n
                    tracker["last_emit"] = now
                    emit(
                        name,
                        **common,
                        status="downloading",
                        downloaded_bytes=current_n,
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
                if os.path.lexists(dest):
                    dest.unlink()
                got.replace(dest)

            if not _usable(dest, minimum, expected):
                size = _file_size(dest)
                raise RuntimeError(
                    f"download returned {size} bytes at {dest}; expected "
                    f"{expected if expected is not None else 'a usable model file'}"
                )

            size = dest.stat().st_size
            _touch_parent(dest)
            elapsed = max(1e-6, time.monotonic() - started)
            emit(
                name,
                **common,
                status="ready",
                downloaded_bytes=size,
                total_bytes=expected or size,
                speed_bps=size / elapsed,
            )
            return {
                "name": name,
                "status": "downloaded",
                "path": str(dest),
                "bytes": size,
                "expected_bytes": expected or size,
            }
        except Exception as exc:
            emit(name, **common, status="error", error=repr(exc), total_bytes=expected, speed_bps=0.0)
            return {
                "name": name,
                "status": "error",
                "error": repr(exc),
                "path": str(dest),
                "bytes": _file_size(dest),
                "expected_bytes": expected,
            }

    workers = _workers()
    if workers == 1 or len(entries) <= 1:
        return [sync_one(name, item) for name, item in entries]

    by_name: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(entries)), thread_name="mmh3-model") as pool:
        future_to_name = {pool.submit(sync_one, name, item): name for name, item in entries}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            by_name[name] = future.result()

    return [by_name[name] for name, _ in entries]
