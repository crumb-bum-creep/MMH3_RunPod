"""Model provisioning: download what the manifest says is missing, with progress.

Run as its own process (`python -m studio.provision [--group eros]`) so a slow
download can never stall the UI or ComfyUI. Progress is written to
/workspace/mmh3/state/provision.json, which the UI polls. A lock file makes
concurrent runs a no-op, so it is always safe to start another.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import paths, util

log = util.setup_logging("provision")
STATUS_FILE = paths.STATE / "provision.json"
LOCK_FILE = paths.STATE / "provision.lock"
DOWNLOAD_DIR = paths.MODELS / ".downloads"
MB = 1024 * 1024


def manifest() -> dict[str, Any]:
    return util.image_yaml("models.yaml")


def model_path(entry: dict[str, Any]) -> Path:
    return paths.MODELS / entry["file"]


def is_present(entry: dict[str, Any]) -> bool:
    p = model_path(entry)
    try:
        return p.is_file() and p.stat().st_size >= float(entry.get("min_mb", 1)) * MB
    except OSError:
        return False


def status() -> dict[str, Any]:
    """Current state of every manifest model, merged with live download progress."""
    live = util.read_json(STATUS_FILE, {}) or {}
    live_models = live.get("models") or {}
    out: dict[str, Any] = {}
    for mid, entry in (manifest().get("models") or {}).items():
        row = {
            "id": mid,
            "file": entry["file"],
            "group": entry.get("group", "core"),
            "used_by": entry.get("used_by") or [],
            "present": is_present(entry),
        }
        lm = live_models.get(mid) or {}
        if not row["present"] and lm.get("state") in ("downloading", "failed", "queued"):
            row.update({k: lm.get(k) for k in ("state", "done", "total", "error")})
        else:
            row["state"] = "ready" if row["present"] else "missing"
        out[mid] = row
    return {"models": out, "running": bool(live.get("running")) and _lock_held(),
            "updated_at": live.get("updated_at")}


def family_ready(family: str, checkpoint: str = "stock", turbo: str | None = None) -> tuple[bool, list[str]]:
    """Whether every file a family needs is on disk; returns what is missing.

    Of the turbo LoRAs only `turbo` (the recipe's) counts, so one that is still
    downloading never holds up recipes that don't use it."""
    from . import loras  # local import: loras pulls in the CivitAI client

    models = manifest().get("models") or {}
    missing = [mid for mid, e in models.items()
               if family in (e.get("used_by") or []) and not is_present(e)
               and (e.get("role") != "turbo" or mid == turbo)]
    rel = loras.checkpoint_file(checkpoint, family)
    if rel is None:
        missing.append(f"checkpoint '{checkpoint}' has no {family} model")
    elif not (paths.MODELS / rel).is_file():
        mid = next((m for m, e in models.items() if e["file"] == rel), None)
        missing.append(mid or rel.split("/")[-1])
    return (not missing, list(dict.fromkeys(missing)))


def _lock_held() -> bool:
    try:
        fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return True
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    return False


class _Progress:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.models: dict[str, dict[str, Any]] = {}
        self.running = True

    def set(self, mid: str, **values: Any) -> None:
        with self.lock:
            self.models.setdefault(mid, {}).update(values)
            self.flush()

    def flush(self) -> None:
        util.write_json(STATUS_FILE, {"running": self.running, "models": self.models,
                                      "updated_at": time.time()})


def _incomplete_bytes(cache_root: Path) -> int:
    total = 0
    for p in cache_root.rglob("*.incomplete"):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def _download(mid: str, entry: dict[str, Any], prog: _Progress, retries: int) -> None:
    from huggingface_hub import get_hf_file_metadata, hf_hub_download, hf_hub_url

    token = os.environ.get("HF_TOKEN") or None
    dest = model_path(entry)
    work = DOWNLOAD_DIR / mid
    total = None
    try:
        total = get_hf_file_metadata(hf_hub_url(entry["hf"], entry["path"]), token=token).size
    except Exception as exc:  # metadata is only for the progress bar
        log.warning("metadata for %s failed: %s", mid, exc)
    prog.set(mid, state="downloading", done=0, total=total, error=None)

    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(2.0):
            prog.set(mid, done=_incomplete_bytes(work))

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                got = Path(hf_hub_download(repo_id=entry["hf"], filename=entry["path"],
                                           local_dir=work, token=token))
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(got, dest)
                shutil.rmtree(work, ignore_errors=True)
                prog.set(mid, state="ready", done=total, error=None)
                log.info("downloaded %s -> %s", mid, dest)
                return
            except Exception as exc:
                last_exc = exc
                log.warning("download %s attempt %d/%d failed: %s", mid, attempt, retries, exc)
                time.sleep(min(60, 5 * attempt))
        prog.set(mid, state="failed", error=str(last_exc)[:300])
    finally:
        stop.set()


def run(groups: list[str]) -> int:
    paths.ensure_dirs()
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.info("another provisioner is already running")
        return 0
    cfg = util.settings().get("provisioning") or {}
    prog = _Progress()
    todo = [(mid, e) for mid, e in (manifest().get("models") or {}).items()
            if e.get("group", "core") in groups and not is_present(e)]
    for mid, _ in todo:
        prog.set(mid, state="queued", done=0, total=None, error=None)
    if not todo:
        log.info("all %s models present", "+".join(groups))
    # Small files first so previews/VAEs are usable while the big ones stream.
    todo.sort(key=lambda t: float(t[1].get("min_mb", 0)))
    with ThreadPoolExecutor(max_workers=int(cfg.get("workers", 3))) as pool:
        for mid, entry in todo:
            pool.submit(_download, mid, entry, prog, int(cfg.get("retries", 4)))
    prog.running = False
    prog.flush()
    if "core" in groups:
        try:  # extra ComfyUI workflows (config/workflows.yaml); never blocks the models
            from . import workflows
            workflows.fetch()
        except Exception as exc:
            log.warning("workflow install failed: %s", exc)
    failed = [m for m, v in prog.models.items() if v.get("state") == "failed"]
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", action="append", default=None,
                        help="manifest group(s) to fetch; default core (+eros when enabled)")
    args = parser.parse_args()
    groups = args.group or ["core"]
    if args.group is None and (os.environ.get("MMH3_DOWNLOAD_EROS", "").lower() in ("1", "true", "yes")
                               or util.settings().get("eros_enabled")):
        groups.append("eros")
    raise SystemExit(run(groups))


if __name__ == "__main__":
    main()
