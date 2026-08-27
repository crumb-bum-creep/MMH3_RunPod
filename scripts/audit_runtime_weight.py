from __future__ import annotations

import os
import subprocess
from pathlib import Path

root = Path("/opt/venv/lib/python3.12/site-packages")
print("=== roots ===")
subprocess.run(["du", "-sh", "/opt/venv", "/ComfyUI"], check=False)

print("\n=== largest remaining site-packages entries ===")
rows = []
for p in root.iterdir():
    try:
        total = 0
        if p.is_file() or p.is_symlink():
            total = p.lstat().st_size
        else:
            for base, dirs, files in os.walk(p, followlinks=False):
                for name in files:
                    q = Path(base) / name
                    try:
                        total += q.lstat().st_size
                    except OSError:
                        pass
        rows.append((total, p.name))
    except OSError:
        pass
for size, name in sorted(rows, reverse=True)[:100]:
    print(f"{size / 2**20:8.1f} MiB  {name}")

print("\n=== staged nvidia/cu13 contents ===")
cu13 = Path("/opt/mmh3-layer/nvidia-c/opt/venv/lib/python3.12/site-packages/nvidia/cu13")
if cu13.exists():
    for p in sorted(cu13.iterdir()):
        total = 0
        try:
            if p.is_file():
                total = p.stat().st_size
            else:
                for base, dirs, files in os.walk(p):
                    for name in files:
                        try:
                            total += (Path(base) / name).stat().st_size
                        except OSError:
                            pass
            print(f"{total / 2**20:8.1f} MiB  {p.name}")
        except OSError:
            pass

print("\n=== pip freeze ===")
subprocess.run(["/opt/venv/bin/pip", "freeze"], check=False)
