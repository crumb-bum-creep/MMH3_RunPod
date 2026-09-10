from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .common import load_yaml

@dataclass
class HardwareInfo:
    gpu_name: str = "unknown"
    vram_mib: int = 0
    driver_version: str = "unknown"
    cgroup_memory_limit_bytes: int = 0
    cgroup_memory_current_bytes: int = 0
    cgroup_memory_working_set_bytes: int = 0
    cgroup_memory_inactive_file_bytes: int = 0
    host_mem_total_bytes: int = 0
    runpod_mem_gb: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def _read_int(path: str) -> int | None:
    try:
        raw = Path(path).read_text().strip()
        if not raw or raw == "max":
            return None
        return int(raw)
    except (OSError, ValueError):
        return None

def host_mem_total_bytes() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0

def cgroup_limit_bytes() -> int:
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        value = _read_int(p)
        if value and value < (1 << 60):
            return value
    raw = os.environ.get("RUNPOD_MEM_GB", "").strip()
    try:
        if raw:
            return int(float(raw) * (1024**3))
    except ValueError:
        pass
    return host_mem_total_bytes()

def cgroup_current_bytes() -> int:
    for p in ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        value = _read_int(p)
        if value is not None:
            return value
    return 0

def cgroup_memory_stat() -> dict[str, int]:
    for p in ("/sys/fs/cgroup/memory.stat", "/sys/fs/cgroup/memory/memory.stat"):
        try:
            values: dict[str, int] = {}
            for line in Path(p).read_text().splitlines():
                parts = line.split()
                if len(parts) != 2:
                    continue
                try:
                    values[parts[0]] = int(parts[1])
                except ValueError:
                    continue
            if values:
                return values
        except OSError:
            continue
    return {}

def cgroup_inactive_file_bytes() -> int:
    stat = cgroup_memory_stat()
    value = stat.get("inactive_file")
    if value is None:
        value = stat.get("total_inactive_file", 0)
    return max(0, int(value or 0))

def cgroup_working_set_bytes() -> int:
    current = cgroup_current_bytes()
    inactive_file = min(current, cgroup_inactive_file_bytes())
    return max(0, current - inactive_file)

def gpu_info() -> tuple[str, int, str]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ]
    try:
        line = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).splitlines()[0]
        name, mem, driver = [x.strip() for x in line.split(",", 2)]
        return name, int(float(mem)), driver
    except Exception:
        return os.environ.get("RUNPOD_GPU_NAME", "unknown"), 0, "unknown"

def detect() -> HardwareInfo:
    name, vram, driver = gpu_info()
    try:
        rp_mem = float(os.environ.get("RUNPOD_MEM_GB", "0") or 0)
    except ValueError:
        rp_mem = 0
    current = cgroup_current_bytes()
    inactive_file = min(current, cgroup_inactive_file_bytes())
    return HardwareInfo(
        gpu_name=name,
        vram_mib=vram,
        driver_version=driver,
        cgroup_memory_limit_bytes=cgroup_limit_bytes(),
        cgroup_memory_current_bytes=current,
        cgroup_memory_working_set_bytes=max(0, current - inactive_file),
        cgroup_memory_inactive_file_bytes=inactive_file,
        host_mem_total_bytes=host_mem_total_bytes(),
        runpod_mem_gb=rp_mem,
    )

def select_profile(hardware: HardwareInfo, profiles_path: Path) -> tuple[str, dict[str, Any]]:
    data = load_yaml(profiles_path, {}) or {}
    profiles = data.get("profiles", {})
    name_lower = hardware.gpu_name.lower()
    for profile_name, profile in profiles.items():
        if profile_name == "fallback":
            continue
        match = ((profile or {}).get("match") or {})
        needles = match.get("gpu_name_contains") or []
        if needles and not any(str(n).lower() in name_lower for n in needles):
            continue
        vram_gb = hardware.vram_mib / 1024 if hardware.vram_mib else 0.0
        min_vram = float(match.get("min_vram_gb", 0) or 0)
        max_vram = float(match.get("max_vram_gb", 0) or 0)
        if min_vram and vram_gb and vram_gb < min_vram:
            continue
        if max_vram and vram_gb and vram_gb > max_vram:
            continue
        if needles or min_vram or max_vram:
            return profile_name, profile or {}
    return "fallback", profiles.get("fallback", {}) or {}
