"""Hold every download while the volume is still being copied in (RunPod pod migration).

When a stopped pod can't get its GPU back, RunPod offers to migrate it: a new pod
starts on another machine and the old /workspace is copied over. The container can
start before that copy has finished, so on a plain boot the provisioner sees an
empty (or half-filled) volume, starts downloading ~100 GB of models, and the copy
then lands on top: twice the disk, and both crawl.

There is no flag that says "this volume is being migrated", so this module reads
the volume itself. The supervisor runs `watch()` in a thread before anything
downloads; the provisioner, the CivitAI download worker and the first LoRA sync
call `wait_open()` first.

  - The core models are (mostly) on disk: a normal restart. Open at once.
  - Otherwise watch the volume for a while. Files appearing that nobody in this
    container is writing mean a copy is under way: stay closed until it has been
    quiet for `settle_seconds`, then open. Downloads then fetch only what the
    copy didn't bring.
  - Nothing appears during the window: a new volume. Open.

The window is short on an empty volume (only a new setup pays it) and longer
when the volume already holds Studio's own files but not the models, which is
what a half-copied volume looks like. The state lives in the container (not on
the volume, where the copy could overwrite it). Studio shows it and offers
"Download now" (`skip()`), which opens the gate immediately.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from . import paths, util

GATE_FILE = paths.RUNTIME / "bootgate.json"
SKIP_FILE = paths.RUNTIME / "bootgate.skip"
MB = 1024 * 1024

# Directories this container writes to on its own before downloads start; growth
# there is never a sign of a copy.
_OWN = ("mmh3/logs", "mmh3/state", "ComfyUI/models/.downloads", "ComfyUI/temp", "ComfyUI/user",
        ".cache", ".local", ".jupyter", ".ipython", ".ipynb_checkpoints")

log = util.setup_logging("bootgate")


def config() -> dict[str, Any]:
    cfg = {"enabled": True, "fresh_window_seconds": 90, "partial_window_seconds": 300,
           "settle_seconds": 180, "max_wait_seconds": 4 * 3600, "threshold_mb": 256, "poll_seconds": 10}
    cfg.update((util.settings().get("provisioning") or {}).get("migration_guard") or {})
    if os.environ.get("MMH3_MIGRATION_GUARD", "").lower() in ("0", "false", "no", "off"):
        cfg["enabled"] = False
    return cfg


# --------------------------------------------------------------------------- reading the volume

def tree_bytes(root: Path | None = None) -> int:
    """Bytes in files under the workspace, leaving out what this container writes itself."""
    root = root or paths.WORKSPACE
    own = {str(root / o) for o in _OWN}
    total = 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if os.path.join(dirpath, d) not in own]
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def core_share() -> float:
    """Share (by size) of the core models already on disk, 0..1."""
    from . import provision

    models = [e for e in (provision.manifest().get("models") or {}).values() if e.get("group", "core") == "core"]
    want = sum(float(e.get("min_mb", 1)) for e in models) or 1.0
    have = sum(float(e.get("min_mb", 1)) for e in models if provision.is_present(e))
    return have / want


def has_history() -> bool:
    """Studio's own files from an earlier pod are on the volume (config, library, outputs)."""
    for p in (paths.CONFIG / "loras.yaml", paths.CONFIG / "settings.yaml", paths.DATA / "jobs.json",
              paths.DATA / "kits.json", paths.DATA / "subjects.json", paths.DATA / "checkpoints.json",
              paths.DATA / "output_library.json", paths.DATA / "output_meta.json"):
        if p.is_file():
            return True
    out = paths.OUTPUT
    try:
        return out.is_dir() and any(out.rglob("*.mp4"))
    except OSError:
        return False


# --------------------------------------------------------------------------- state

def _write(state: str, **info: Any) -> None:
    util.write_json(GATE_FILE, {"state": state, "updated_at": time.time(), **info})


def status() -> dict[str, Any]:
    """{state: open|checking|copying, ...}. No gate file (dev, tests, manual runs) = open."""
    data = util.read_json(GATE_FILE, None)
    if not isinstance(data, dict) or not data.get("state"):
        return {"state": "open", "reason": "no boot check in this container"}
    return data


def is_open() -> bool:
    return status().get("state") == "open"


def wait_open(poll: float = 3.0, on_wait=None) -> None:
    """Block until the gate is open. on_wait(status) is called while waiting."""
    while True:
        st = status()
        if st.get("state") == "open":
            return
        if on_wait:
            on_wait(st)
        time.sleep(poll)


def skip() -> None:
    """'Download now': open the gate without waiting any longer."""
    paths.RUNTIME.mkdir(parents=True, exist_ok=True)
    SKIP_FILE.touch()


def begin() -> None:
    """Close the gate synchronously, before any downloader starts (supervisor)."""
    paths.RUNTIME.mkdir(parents=True, exist_ok=True)
    SKIP_FILE.unlink(missing_ok=True)
    _write("checking", reason="checking the volume")


def start() -> threading.Thread:
    """Close the gate and decide in a thread. The volume's history is read here, before
    Studio starts (and writes its own files)."""
    begin()
    history = has_history()
    t = threading.Thread(target=watch, kwargs={"history": history}, daemon=True, name="bootgate")
    t.start()
    return t


def watch(cfg: dict[str, Any] | None = None, clock=time.monotonic, sleep=time.sleep,
          measure=tree_bytes, history: bool | None = None) -> str:
    """Decide when downloads may start; returns why the gate opened."""
    cfg = cfg or config()

    def open_(reason: str, **info: Any) -> str:
        log.info("downloads allowed: %s", reason)
        _write("open", reason=reason, **info)
        return reason

    try:
        if not cfg.get("enabled", True):
            return open_("migration guard turned off")
        share = core_share()
        if share >= 0.8:
            return open_("volume already set up")
        history = has_history() if history is None else history
        window = float(cfg["partial_window_seconds"] if history else cfg["fresh_window_seconds"])
        threshold = float(cfg["threshold_mb"]) * MB
        settle, poll = float(cfg["settle_seconds"]), float(cfg["poll_seconds"])
        t0 = clock()
        base = last = measure()
        last_change = t0
        copying = False
        while True:
            now = clock()
            if SKIP_FILE.exists():
                return open_("you chose to download now")
            if now - t0 > float(cfg["max_wait_seconds"]):
                return open_("stopped waiting for the volume copy")
            size = measure()
            if size - last > 16 * MB or size < last - 16 * MB:
                last_change = now
            last = size
            if not copying and size - base >= threshold:
                copying = True
                log.info("files are arriving on the volume (%.1f GB so far): holding downloads", (size - base) / 1024 ** 3)
            quiet = now - last_change
            if copying:
                if quiet >= settle:
                    return open_("the volume copy finished", copied_bytes=size - base)
                _write("copying", reason="the volume is still being copied in", copied_bytes=size - base,
                       volume_bytes=size, quiet_seconds=round(quiet), settle_seconds=settle,
                       started_at=time.time() - (now - t0))
            else:
                if now - t0 >= window:
                    return open_("new volume" if not history else "nothing is being copied in")
                _write("checking", reason="checking whether the volume is being copied in",
                       window_left=round(window - (now - t0)), history=history, volume_bytes=size)
            sleep(poll)
    except Exception as exc:  # the guard must never keep the pod from provisioning
        log.warning("volume check failed (%s); allowing downloads", exc)
        return open_(f"volume check failed: {exc}")
