from __future__ import annotations

import json
import time
from typing import Any

from . import comfy
from .common import LOG_ROOT, STATE_ROOT
from .hardware import cgroup_current_bytes, cgroup_inactive_file_bytes, cgroup_limit_bytes

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
    pressure_grace = max(0.0, float(memory_cfg.get("pressure_grace_seconds", 10)))
    cache_grace = max(poll, float(memory_cfg.get("cache_grace_seconds", 15)))

    last_cycle = 0.0
    last_action = 0.0
    cleanup_stage = "idle"
    pressure_since: float | None = None
    hold = False

    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    _log(
        f"limit={limit/GIB:.2f} GiB cleanup={cleanup:.0%} resume={resume:.0%} "
        f"working_headroom={min_headroom/GIB:.0f} GiB resume_headroom={resume_headroom/GIB:.0f} GiB "
        f"cache_first={cache_first} pressure_grace={pressure_grace:.0f}s cache_grace={cache_grace:.0f}s"
    )

    while True:
        now = time.time()
        raw_current = cgroup_current_bytes()
        inactive_file = min(raw_current, cgroup_inactive_file_bytes())
        current = max(0, raw_current - inactive_file)
        free = max(0, limit - current)
        frac = current / limit if limit else 0.0
        raw_free = max(0, limit - raw_current)
        raw_frac = raw_current / limit if limit else 0.0

        comfy_ready = comfy.is_ready(timeout=min(2.0, max(0.5, poll / 2)))
        q = comfy.queue_state() if comfy_ready else {"queue_running": [], "queue_pending": []}
        running = bool(q.get("queue_running"))
        pending = bool(q.get("queue_pending"))

        pressure = frac >= cleanup or free < min_headroom
        safe_to_resume = frac <= resume and free >= resume_headroom
        cleanup_idle = not idle_only or (not running and not pending)
        raw_critical = raw_frac >= critical and pressure

        # Normal pressure is based on cgroup working set: raw memory.current less
        # inactive file cache. Linux is allowed to use spare RAM for page cache;
        # reclaimable inactive_file must not look like application memory pressure.
        if pressure and comfy_ready and cleanup_idle:
            if pressure_since is None:
                pressure_since = now
        else:
            pressure_since = None
        pressure_for = (now - pressure_since) if pressure_since is not None else 0.0
        cleanup_armed = pressure_since is not None and pressure_for >= pressure_grace

        if hold and safe_to_resume:
            hold = False
            cleanup_stage = "idle"
            _log(
                f"memory hold cleared: working={frac:.1%} ({current/GIB:.1f} GiB), "
                f"raw={raw_frac:.1%} ({raw_current/GIB:.1f} GiB), cache={inactive_file/GIB:.1f} GiB"
            )

        state = {
            "limit_bytes": limit,
            # Preserve these legacy keys for the Phone UI, but make them represent
            # the actionable working-set metric rather than raw cgroup charge.
            "current_bytes": current,
            "free_bytes": free,
            "fraction": frac,
            "memory_metric": "working_set",
            "working_set_bytes": current,
            "working_set_fraction": frac,
            "raw_current_bytes": raw_current,
            "raw_free_bytes": raw_free,
            "raw_fraction": raw_frac,
            "reclaimable_cache_bytes": inactive_file,
            "running": running,
            "pending": pending,
            "comfy_ready": comfy_ready,
            "memory_hold": hold,
            "pressure": pressure,
            "raw_critical": raw_critical,
            "pressure_for_seconds": round(pressure_for, 3),
            "cleanup_armed": cleanup_armed,
            "cleanup_stage": cleanup_stage,
            "cache_first": cache_first,
            "cleanup_fraction": cleanup,
            "resume_fraction": resume,
            "critical_fraction": critical,
            "min_free_headroom_bytes": int(min_headroom),
            "resume_free_headroom_bytes": int(resume_headroom),
            "updated_at": now,
        }
        (STATE_ROOT / "memory.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

        # Raw usage is retained only as an emergency signal, and it must coincide
        # with real working-set pressure so page cache alone cannot interrupt work.
        if comfy_ready and raw_critical and running and interrupt_on_critical:
            _log(
                f"critical memory: working={frac:.1%} ({current/GIB:.1f} GiB), "
                f"raw={raw_frac:.1%} ({raw_current/GIB:.1f} GiB), cache={inactive_file/GIB:.1f} GiB; "
                "interrupting running job"
            )
            if comfy.interrupt():
                hold = True
                cleanup_stage = "critical"
        elif pressure and comfy_ready and cleanup_idle and cleanup_armed:
            if cleanup_stage == "cache" and now - last_action >= cache_grace:
                _log(
                    f"working-set pressure persisted after cache clear: {frac:.1%} used, "
                    f"{free/GIB:.1f} GiB working headroom (raw {raw_current/GIB:.1f} GiB, "
                    f"reclaimable cache {inactive_file/GIB:.1f} GiB); escalating to model unload + cache release"
                )
                ok = comfy.free_memory(True, True)
                last_action = now
                if ok:
                    last_cycle = now
                    cleanup_stage = "models"
                    hold = True
                else:
                    _log("Comfy rejected/unavailable for full memory release; keeping cache stage and retrying later")
            elif cleanup_stage in {"idle", "critical"} and now - last_cycle >= cooldown:
                if cache_first:
                    _log(
                        f"sustained idle working-set pressure: {frac:.1%} used, {free/GIB:.1f} GiB headroom "
                        f"(raw {raw_current/GIB:.1f} GiB, reclaimable cache {inactive_file/GIB:.1f} GiB); "
                        "clearing Comfy cache while keeping loaded models"
                    )
                    ok = comfy.free_memory(False, True)
                    if ok:
                        cleanup_stage = "cache"
                        hold = True
                    else:
                        _log("Comfy rejected/unavailable for cache clear; cleanup stage unchanged")
                else:
                    _log(
                        f"sustained idle working-set pressure: {frac:.1%} used, {free/GIB:.1f} GiB headroom; "
                        "requesting Comfy model/cache release"
                    )
                    ok = comfy.free_memory(True, True)
                    if ok:
                        cleanup_stage = "models"
                        hold = True
                last_action = now
                last_cycle = now
            elif cleanup_stage == "models" and now - last_action >= cooldown:
                _log(
                    f"working-set pressure remains after model unload: {frac:.1%} used, {free/GIB:.1f} GiB headroom; "
                    "retrying full Comfy release"
                )
                if comfy.free_memory(True, True):
                    hold = True
                last_action = now
                last_cycle = now

        time.sleep(poll)
