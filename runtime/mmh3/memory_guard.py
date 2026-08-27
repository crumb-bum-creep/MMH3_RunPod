from __future__ import annotations

import json
import time
from typing import Any

from . import comfy
from .common import LOG_ROOT, STATE_ROOT
from .hardware import cgroup_current_bytes, cgroup_limit_bytes

def _log(message: str) -> None:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}\n"
    with (LOG_ROOT / "memory-guard.log").open("a", encoding="utf-8") as f:
        f.write(line)
    print("[memory-guard]", message, flush=True)

def run(memory_cfg: dict[str, Any]) -> None:
    limit = cgroup_limit_bytes()
    if not limit:
        _log("No finite cgroup memory limit detected; guard disabled.")
        return
    cleanup = float(memory_cfg.get("cleanup_fraction", 0.84))
    resume = float(memory_cfg.get("resume_fraction", 0.76))
    critical = float(memory_cfg.get("critical_fraction", 0.92))
    poll = float(memory_cfg.get("poll_seconds", 5))
    idle_only = bool(memory_cfg.get("cleanup_only_when_queue_idle", True))
    interrupt_on_critical = bool(memory_cfg.get("interrupt_running_on_critical", False))
    cooldown = float(memory_cfg.get("cleanup_cooldown_seconds", 90))
    last_cleanup = 0.0
    hold = False

    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    _log(f"limit={limit/(1024**3):.2f} GiB cleanup={cleanup:.0%} resume={resume:.0%} critical={critical:.0%}")
    while True:
        current = cgroup_current_bytes()
        frac = current / limit if limit else 0.0
        q = comfy.queue_state()
        running = bool(q.get("queue_running"))
        pending = bool(q.get("queue_pending"))

        state = {
            "limit_bytes": limit,
            "current_bytes": current,
            "fraction": frac,
            "running": running,
            "pending": pending,
            "memory_hold": hold,
            "cleanup_fraction": cleanup,
            "resume_fraction": resume,
            "critical_fraction": critical,
            "updated_at": time.time(),
        }
        (STATE_ROOT / "memory.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

        if hold and frac <= resume:
            hold = False
            _log(f"memory hold cleared at {frac:.1%}")
        if frac >= critical and running and interrupt_on_critical:
            _log(f"critical memory {frac:.1%}; interrupting running job")
            comfy.interrupt()
            hold = True
        elif frac >= cleanup and (not idle_only or (not running and not pending)):
            if time.time() - last_cleanup >= cooldown:
                _log(f"memory {frac:.1%}; requesting Comfy model/cache release")
                comfy.free_memory(True, True)
                last_cleanup = time.time()
                hold = True
        time.sleep(poll)
