from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

OUTPUT_DIR = Path(os.environ.get("MMH3_COMFY_PERSIST", "/workspace/ComfyUI")) / "output"
DATA_ROOT = Path(os.environ.get("MMH3_DATA_ROOT", "/workspace/mmh3/data"))
META_FILE = DATA_ROOT / "output_meta.json"
INDEX_DIR = OUTPUT_DIR / ".mmh3"
INDEX_FILE = INDEX_DIR / "output-index.json"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _family_stem(path: Path) -> str:
    stem = path.stem
    return re.sub(r"(?i)(?:[-_. ]?audio)$", "", stem)


def _preview_for(video: Path, siblings: list[Path]) -> str | None:
    family = _family_stem(video).casefold()
    exact: list[Path] = []
    prefix: list[Path] = []
    for candidate in siblings:
        if candidate.suffix.lower() not in IMAGE_EXT:
            continue
        stem = candidate.stem.casefold()
        if stem == family:
            exact.append(candidate)
        elif stem.startswith(family + "_") or stem.startswith(family + "-"):
            prefix.append(candidate)
    choices = exact or prefix
    if not choices:
        return None
    choices.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return choices[0].relative_to(OUTPUT_DIR).as_posix()


def build_index() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    global_meta = _load_json(META_FILE, {})
    if not isinstance(global_meta, dict):
        global_meta = {}

    items: list[dict[str, Any]] = []
    watched_dirs: dict[str, int] = {}

    for root, dirs, files in os.walk(OUTPUT_DIR):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if d != ".mmh3" and d != "MMH3Director"]
        if ".mmh3" in root_path.parts or "MMH3Director" in root_path.parts:
            continue
        try:
            watched_dirs[root_path.as_posix()] = root_path.stat().st_mtime_ns
        except OSError:
            continue

        siblings = [root_path / name for name in files]
        for path in siblings:
            if path.suffix.lower() != ".mp4" or "audio" not in path.name.lower():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            rel = path.relative_to(OUTPUT_DIR).as_posix()
            sidecar = path.with_suffix(path.suffix + ".h3.json")
            metadata = global_meta.get(rel)
            if not isinstance(metadata, dict):
                metadata = _load_json(sidecar, {})
                if not isinstance(metadata, dict):
                    metadata = {}
            items.append({
                "file": rel,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "metadata": metadata,
                "preview_file": _preview_for(path, siblings),
            })

    items.sort(key=lambda item: item["mtime"], reverse=True)
    return {
        "version": 1,
        "updated_at": time.time(),
        "count": len(items),
        "items": items,
        "watched_dirs": watched_dirs,
    }


def _dirs_changed(watched: dict[str, int]) -> bool:
    if not watched:
        return True
    for raw, old_mtime in watched.items():
        path = Path(raw)
        try:
            if path.stat().st_mtime_ns != old_mtime:
                return True
        except OSError:
            return True
    return False


async def run_forever(poll_seconds: float = 5.0, safety_rescan_seconds: float = 300.0) -> None:
    # Reuse the last persisted directory mtimes so a warm pod can serve the
    # existing output index immediately instead of recursively walking a large
    # persistent output tree while Comfy/provisioning are also starting.
    prior = _load_json(INDEX_FILE, {})
    watched = dict(prior.get("watched_dirs") or {}) if isinstance(prior, dict) else {}
    last_scan = time.time() if watched else 0.0
    try:
        startup_delay = max(0.0, float(os.environ.get("MMH3_OUTPUT_INDEX_STARTUP_DELAY_SECONDS", "45")))
    except ValueError:
        startup_delay = 45.0

    # Even a modern persisted index can represent hundreds/thousands of watched
    # directories. Delay the first directory-stat sweep on every warm start so
    # the UI/Comfy startup path gets uncontested I/O.
    if INDEX_FILE.exists() and startup_delay:
        await asyncio.sleep(startup_delay)

    while True:
        try:
            now = time.time()
            if _dirs_changed(watched) or now - last_scan >= safety_rescan_seconds:
                snapshot = await asyncio.to_thread(build_index)
                watched = dict(snapshot.get("watched_dirs") or {})
                await asyncio.to_thread(_atomic_json, INDEX_FILE, snapshot)
                last_scan = now
            await asyncio.sleep(poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Preserve the previous usable index when a background refresh
            # fails. A transient disk/migration error should not blank Outputs.
            previous = _load_json(INDEX_FILE, {})
            if not isinstance(previous, dict):
                previous = {}
            previous["error"] = str(exc)
            previous["updated_at"] = float(previous.get("updated_at") or time.time())
            try:
                _atomic_json(INDEX_FILE, previous)
            except Exception:
                pass
            await asyncio.sleep(max(5.0, poll_seconds))
