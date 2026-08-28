from __future__ import annotations

import os
import shutil
from pathlib import Path

SP = Path("/opt/venv/lib/python3.12/site-packages")
STAGE = Path("/opt/mmh3-layer")


def tree_size(path: Path) -> int:
    try:
        if path.is_symlink() or path.is_file():
            return path.lstat().st_size
    except OSError:
        return 0
    total = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        for name in files:
            p = Path(base) / name
            try:
                total += p.lstat().st_size
            except OSError:
                pass
        for name in dirs:
            p = Path(base) / name
            if p.is_symlink():
                try:
                    total += p.lstat().st_size
                except OSError:
                    pass
    return total


def move_entry(src: Path, root: Path, relative_parent: Path) -> None:
    dest_dir = root / relative_parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest_dir / src.name))


def greedy_move(entries: list[Path], roots: list[Path], relative_parent: Path) -> list[int]:
    loads = [0 for _ in roots]
    for src in sorted(entries, key=tree_size, reverse=True):
        index = min(range(len(roots)), key=lambda i: loads[i])
        size = tree_size(src)
        move_entry(src, roots[index], relative_parent)
        loads[index] += size
    return loads


def fmt(values: list[int]) -> str:
    return ", ".join(f"{v / 2**20:.1f} MiB" for v in values)


def main() -> None:
    STAGE.mkdir(parents=True, exist_ok=True)

    # Keep the largest GPU runtime components as independent registry blobs.
    dedicated = {
        "torch": STAGE / "torch",
        "triton": STAGE / "triton",
        "onnxruntime": STAGE / "onnxruntime",
    }
    for name, root in dedicated.items():
        src = SP / name
        if src.exists() or src.is_symlink():
            move_entry(src, root, Path("opt/venv/lib/python3.12/site-packages"))

    nvidia = SP / "nvidia"
    cudnn = nvidia / "cudnn"
    if cudnn.exists():
        move_entry(cudnn, STAGE / "cudnn", Path("opt/venv/lib/python3.12/site-packages/nvidia"))

    # The current cu130 wheels place most CUDA userspace libraries in
    # nvidia/cu13/lib. Split those actual files across three balanced blobs.
    # All buckets are restored to the same final directory before runtime.
    nroots = [STAGE / f"nvidia-{i}" for i in range(1, 4)]
    nloads = [0, 0, 0]
    cu13 = nvidia / "cu13"
    if cu13.exists():
        lib = cu13 / "lib"
        if lib.exists():
            entries = list(lib.iterdir())
            nloads = greedy_move(
                entries,
                nroots,
                Path("opt/venv/lib/python3.12/site-packages/nvidia/cu13/lib"),
            )
        for child in list(cu13.iterdir()):
            if child.name == "lib":
                continue
            idx = min(range(3), key=lambda i: nloads[i])
            size = tree_size(child)
            move_entry(
                child,
                nroots[idx],
                Path("opt/venv/lib/python3.12/site-packages/nvidia/cu13"),
            )
            nloads[idx] += size
        try:
            (cu13 / "lib").rmdir()
        except OSError:
            pass
        try:
            cu13.rmdir()
        except OSError:
            pass

    # Distribute NCCL/cuSPARSELt/NVSHMEM/etc. into those same three buckets.
    if nvidia.exists():
        remaining = [p for p in nvidia.iterdir() if p.name != "cudnn"]
        for child in sorted(remaining, key=tree_size, reverse=True):
            idx = min(range(3), key=lambda i: nloads[i])
            size = tree_size(child)
            move_entry(
                child,
                nroots[idx],
                Path("opt/venv/lib/python3.12/site-packages/nvidia"),
            )
            nloads[idx] += size
        try:
            nvidia.rmdir()
        except OSError:
            pass

    # The "everything else" part of the inherited venv was the single largest
    # compressed blob. Balance every remaining top-level site-packages entry
    # across three independent COPY layers while leaving the venv skeleton,
    # bin/, and pyvenv.cfg intact.
    vroots = [STAGE / f"venv-{i}" for i in range(1, 4)]
    remaining = list(SP.iterdir())
    vloads = greedy_move(
        remaining,
        vroots,
        Path("opt/venv/lib/python3.12/site-packages"),
    )

    print("MMH3 layer split:")
    print("  remaining venv buckets:", fmt(vloads))
    print("  NVIDIA buckets:", fmt(nloads))
    for path in sorted(STAGE.iterdir()):
        print(f"  {path.name}: {tree_size(path) / 2**20:.1f} MiB")


if __name__ == "__main__":
    main()
