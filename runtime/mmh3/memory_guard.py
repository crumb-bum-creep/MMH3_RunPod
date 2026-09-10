from __future__ import annotations

import json
import time
from typing import Any

from . import comfy
from .common import LOG_ROOT, STATE_ROOT
from .hardware import cgroup_current_bytes, cgroup_limit_bytes

GIB = 1024 ** 3


def _log(message: str) -> None:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with (LOG_ROOT / "memory-guard.log").open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {message}\n")
    print("[memory-guard]", message, flush=True)


def run(memory_cfg: dict[str, Any]) -> None:
    limit = cgroup_limit_bytes()
    if not limit:
        _log("No finite cgroup memory limit detected; guard disabled.")
        return

    cleanup = float(memory_cfg.get("cleanup_fraction", 0.84))
    resume = float(memory_cfg.get("resume_fraction", 0.76))
    critical = float(memory_cfg.get("critical_fraction", 0.92))
    min_headroom = float(memory_cfg.get("min_free_headroom_gb", 40)) * GIB
    resume_headroom = float(memory_cfg.get("resume_free_headroom_gb", 46)) * GIB
    poll = float(memory_cfg.get("poll_seconds", 5))
    idle_only = bool(memory_cfg.get("cleanup_only_when_queue_idle", True))
    interrupt_on_critical = bool(memory_cfg.get("interrupt_running_on_critical", False))
    cooldown = float(memory_cfg.get("cleanup_cooldown_seconds", 90))
    cache_first = bool(memory_cfg.get("cache_first", True))
    cache_grace = max(poll, float(memory_cfg.get("cache_grace_seconds", 15)))

    last_cycle = 0.0
    last_action = 0.0
    cleanup_stage = "idle"
    hold = False

    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    _log(
        f"limit={limit/GIB:.2f} GiB cleanup={cleanup:.0%} resume={resume:.0%} "
        f"headroom={min_headroom/GIB:.0f} GiB resume_headroom={resume_headroom/GIB:.0f} GiB "
        f"cache_first={cache_first} cache_grace={cache_grace:.0f}s"
    )

    while True:
        current = cgroup_current_bytes()
        free = max(0, limit - current)
        frac = current / limit if limit else 0.0
        q = comfy.queue_state()
        running = bool(q.get("queue_running"))
        pending = bool(q.get("queue_pending"))

        pressure = frac >= cleanup or free < min_headroom
        safe_to_resume = frac <= resume and free >= resume_headroom

        if hold and safe_to_resume:
            hold = False
            cleanup_stage = "idle"
            _log(f"memory hold cleared: {frac:.1%} used, {free/GIB:.1f} GiB free")

        state = {
            "limit_bytes": limit,
            "current_bytes": current,
            "free_bytes": free,
            "fraction": frac,
            "running": running,
            "pending": pending,
            "memory_hold": hold,
            "pressure": pressure,
            "cleanup_stage": cleanup_stage,
            "cache_first": cache_first,
            "cleanup_fraction": cleanup,
            "resume_fraction": resume,
            "critical_fraction": critical,
            "min_free_headroom_bytes": int(min_headroom),
            "resume_free_headroom_bytes": int(resume_headroom),
            "updated_at": time.time(),
        }
        (STATE_ROOT / "memory.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

        now = time.time()
        if frac >= critical and running and interrupt_on_critical:
            _log(f"critical memory {frac:.1%}; interrupting running job")
            comfy.interrupt()
            hold = True
            cleanup_stage = "critical"
        elif pressure and (not idle_only or (not running and not pending)):
            if cleanup_stage == "cache" and now - last_action >= cache_grace:
                _log(
                    f"memory pressure persisted after cache clear: {frac:.1%} used, {free/GIB:.1f} GiB free; "
                    "escalating to Comfy model unload + cache release"
                )
                comfy.free_memory(True, True)
                last_action = now
                last_cycle = now
                cleanup_stage = "models"
                hold = True
            elif cleanup_stage in {"idle", "critical"} and now - last_cycle >= cooldown:
                if cache_first:
                    _log(
                        f"memory pressure: {frac:.1%} used, {free/GIB:.1f} GiB free; "
                        "clearing Comfy cache while keeping loaded models"
                    )
                    comfy.free_memory(False, True)
                    cleanup_stage = "cache"
                else:
                    _log(
                        f"memory pressure: {frac:.1%} used, {free/GIB:.1f} GiB free; "
                        "requesting Comfy model/cache release"
                    )
                    comfy.free_memory(True, True)
                    cleanup_stage = "models"
                    last_cycle = now
                last_action = now
                hold = True
            elif cleanup_stage == "models" and now - last_action >= cooldown:
                _log(
                    f"memory pressure remains after model unload: {frac:.1%} used, {free/GIB:.1f} GiB free; "
                    "retrying full Comfy release"
                )
                comfy.free_memory(True, True)
                last_action = now
                last_cycle = now

        time.sleep(poll)
