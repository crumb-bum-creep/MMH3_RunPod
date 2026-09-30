"""Container memory, read the way the kernel's OOM killer sees it.

On RunPod, /proc/meminfo and `free` report the host. The limit that actually
kills the pod is the cgroup's memory.max. "Working set" is usage minus
inactive file cache (page cache the kernel can drop without asking), which is
the same number ComfyUI >= 0.34 uses internally.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

GIB = 1024 ** 3


def _read_int(path: str) -> int | None:
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return None
    if raw in ("", "max"):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_stat(path: str, key: str) -> int | None:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                name, _, value = line.partition(" ")
                if name == key:
                    return int(value)
    except (OSError, ValueError):
        return None
    return None


def _cgroup_dirs() -> list[str]:
    """This process's cgroup directories and their ancestors, v2 and v1 memory,
    nearest first (same walk ComfyUI's system_memory uses)."""
    dirs: list[str] = []
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        controllers, path = parts[1], parts[2]
        if controllers == "":
            root = "/sys/fs/cgroup"
        elif "memory" in controllers.split(","):
            root = "/sys/fs/cgroup/memory"
        else:
            continue
        chain = [root]
        for piece in path.split("/"):
            if piece:
                chain.append(os.path.join(chain[-1], piece))
        for d in reversed(chain):
            if d not in dirs:
                dirs.append(d)
    return dirs or ["/sys/fs/cgroup", "/sys/fs/cgroup/memory"]


@dataclass
class Memory:
    limit: int            # bytes the container may use
    used: int             # working set (what counts toward an OOM kill)
    raw: int              # including reclaimable page cache
    limited: bool         # False when no cgroup limit was found (host numbers)

    @property
    def fraction(self) -> float:
        return self.used / self.limit if self.limit else 0.0

    @property
    def free(self) -> int:
        return max(0, self.limit - self.used)

    def as_dict(self) -> dict:
        return {
            "limit_gb": round(self.limit / GIB, 1),
            "used_gb": round(self.used / GIB, 1),
            "raw_gb": round(self.raw / GIB, 1),
            "free_gb": round(self.free / GIB, 1),
            "fraction": round(self.fraction, 3),
            "limited": self.limited,
        }


def read() -> Memory:
    host_total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    best: Memory | None = None
    for d in _cgroup_dirs():
        limit = _read_int(os.path.join(d, "memory.max")) or _read_int(os.path.join(d, "memory.limit_in_bytes"))
        if not limit or limit >= host_total:
            continue
        usage = _read_int(os.path.join(d, "memory.current"))
        key = "inactive_file"
        if usage is None:
            usage, key = _read_int(os.path.join(d, "memory.usage_in_bytes")), "total_inactive_file"
        if usage is None:
            continue
        inactive = _read_stat(os.path.join(d, "memory.stat"), key) or 0
        m = Memory(limit=limit, used=max(0, usage - inactive), raw=usage, limited=True)
        # The tightest limit is the one that kills the pod.
        if best is None or m.free < best.free:
            best = m
    if best:
        return best
    try:
        import psutil  # host fallback (local dev)

        vm = psutil.virtual_memory()
        return Memory(limit=vm.total, used=vm.total - vm.available, raw=vm.used, limited=False)
    except Exception:
        return Memory(limit=host_total, used=0, raw=0, limited=False)


def cache_headroom_gb(setting) -> float:
    """ComfyUI --cache-ram headroom: 12% of the container limit, 12..32 GB."""
    if setting not in (None, "", "auto"):
        return float(setting)
    limit_gb = read().limit / GIB
    return round(min(32.0, max(12.0, limit_gb * 0.12)), 1)


def gpu() -> dict:
    """Best-effort GPU name and VRAM via nvidia-smi (empty dict when absent)."""
    import subprocess

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return {}
    if not out:
        return {}
    name, total, used, util = [p.strip() for p in out[0].split(",")[:4]]
    try:
        return {
            "name": name,
            "vram_total_gb": round(float(total) / 1024, 1),
            "vram_used_gb": round(float(used) / 1024, 1),
            "util": int(float(util)),
        }
    except ValueError:
        return {"name": name}
