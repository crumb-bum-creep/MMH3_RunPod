"""LoRA catalog + user checkpoints from CivitAI, with multi-file versions.

Catalog: /workspace/mmh3/config/loras.yaml. The old fields are kept
(version_id, enabled, recommended_strength, nickname, tags, trigger_words,
notes, filename) and a `files` list is added:

  files:
    - {id: 3150341, name: MysticXXX_MMH3-V4.safetensors,       family: fl2v,  role: main,   install: true}
    - {id: 3156453, name: MysticXXX_MMH3-V4-ref2va.safetensors, family: ref2v, role: main,   install: true}

family: fl2v (T2V/I2V), ref2v (R2V) or any. role: main or helper (helpers,
like a motion LoRA, are loaded alongside the main file at the same strength
times `scale`). At generation time `files_for(entry, family)` picks what to load.

User checkpoints: /workspace/mmh3/data/checkpoints.json, merged into the
base-checkpoint picker next to the stock model and Eros.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import requests

from . import civitai, paths, util

CATALOG = paths.CONFIG / "loras.yaml"
CHECKPOINTS = paths.DATA / "checkpoints.json"
STATUS = paths.STATE / "lora_sync.json"
LORA_DIR = paths.MODELS / "loras"
UNET_DIR = paths.MODELS / "diffusion_models"
TURBO_PREFIXES = ("minimax_h3_fl2v_turbo", "minimax_h3_ref2v_turbo", "minimax_h3_hyperflow")
_lock = threading.RLock()
_worker: threading.Thread | None = None
_queue: list[tuple[str, dict[str, Any]]] = []


def ensure_seeded() -> None:
    if not CATALOG.exists():
        seed = paths.IMAGE_CONFIG / "loras.seed.yaml"
        if seed.exists():
            CATALOG.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(seed, CATALOG)


# --------------------------------------------------------------------------- catalog

def _entry_files(e: dict[str, Any]) -> list[dict[str, Any]]:
    """An entry's files; old single-file entries get one inferred from `filename`."""
    files = e.get("files")
    if isinstance(files, list) and files:
        return files
    if e.get("filename"):
        guess = civitai.classify_files([{"name": e["filename"]}])[0]
        return [{"id": None, "name": e["filename"], "family": guess["family"], "role": "main", "install": True}]
    return []


def catalog() -> list[dict[str, Any]]:
    return list((util.read_yaml(CATALOG, {}) or {}).get("loras") or [])


def _save(items: list[dict[str, Any]]) -> None:
    util.write_yaml(CATALOG, {"loras": items})


def _key(e: dict[str, Any]) -> str:
    return str(e.get("version_id") or e.get("filename") or "")


def _disk(folder: Path) -> set[str]:
    if not folder.exists():
        return set()
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*.safetensors")}


def files_for(entry: dict[str, Any], family: str) -> list[dict[str, Any]]:
    """Files to load for a family: ONE main file plus any helpers.

    Exactly one main file, so alternate copies of the same LoRA (fp16 / fp32,
    pruned / full) can never be stacked. A mode-specific file beats an
    all-modes one; the primary file beats the rest."""
    disk = _disk(LORA_DIR)
    usable = [f for f in _entry_files(entry)
              if f.get("name") in disk and f.get("family", "any") in ("any", family)]
    mains = [f for f in usable if f.get("role", "main") == "main"]
    helpers = [f for f in usable if f.get("role") == "helper"]
    mains.sort(key=lambda f: (f.get("family") != family, not f.get("primary"),
                              f.get("name") != entry.get("filename")))
    return mains[:1] + helpers


def resolve_selection(selected: list[dict[str, Any]], family: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Expand Create's LoRA picks ({key, strength}) into concrete files for a family.

    Returns (files to load [{file, strength, nickname}], warnings)."""
    by_key = {_key(e): e for e in catalog()}
    out, warnings = [], []
    for s in selected:
        strength = float(s.get("strength", 1.0))
        entry = by_key.get(str(s.get("key") or ""))
        if entry is None:  # an untracked file picked directly
            fn = s.get("file") or s.get("key")
            if fn:
                out.append({"file": fn, "strength": strength, "nickname": s.get("nickname") or fn})
            continue
        files = files_for(entry, family)
        if not files:
            warnings.append(f"{entry.get('nickname') or _key(entry)} has no installed file for "
                            f"{'R2V' if family == 'ref2v' else 'T2V/I2V'}")
            continue
        parts = s.get("parts") or {}
        for f in files:
            scale = float(f.get("scale", 1.0))
            own = parts.get(f["name"])  # an independent per-file strength beats strength x scale
            value = min(2.0, max(0.0, float(own))) if own is not None else strength * scale
            out.append({"file": f["name"], "strength": round(value, 3), "key": _key(entry),
                        "nickname": entry.get("nickname") or f["name"], "role": f.get("role", "main")})
    return out, warnings


def listing() -> dict[str, Any]:
    disk = _disk(LORA_DIR)
    status = (util.read_json(STATUS, {}) or {}).get("items") or {}
    items, known = [], set()
    for e in catalog():
        files = []
        for f in _entry_files(e):
            known.add(f["name"])
            st = status.get(f"{e.get('version_id')}:{f.get('id')}") or status.get(str(e.get("version_id"))) or {}
            installed = f["name"] in disk
            files.append({**f, "installed": installed,
                          "state": "ready" if installed else st.get("state") or ("queued" if f.get("install", True) else "skipped"),
                          "error": None if installed else st.get("error"),
                          "done": st.get("done"), "total": st.get("total")})
        families = sorted({f.get("family", "any") for f in files if f["installed"]})
        active = {fam: [{"name": f["name"], "role": f.get("role", "main"), "scale": float(f.get("scale", 1.0))}
                        for f in files_for(e, fam)] for fam in ("fl2v", "ref2v")}
        items.append({**{k: v for k, v in e.items() if k != "files"}, "key": _key(e), "files": files, "active": active,
                      "installed": any(f["installed"] for f in files),
                      "families": ["fl2v", "ref2v"] if "any" in families else families})
    for fn in sorted(disk - known):
        if Path(fn).name.startswith(TURBO_PREFIXES):
            continue
        items.append({"key": fn, "filename": fn, "nickname": Path(fn).stem, "enabled": True, "installed": True,
                      "untracked": True, "recommended_strength": 1.0, "trigger_words": [], "tags": [],
                      "families": ["fl2v", "ref2v"],
                      "files": [{"name": fn, "family": "any", "role": "main", "installed": True, "state": "ready"}]})
    return {"items": items, "busy": bool(_worker and _worker.is_alive()), "has_token": bool(civitai.token()),
            "domain": civitai.base_url()}


def upsert(entry: dict[str, Any]) -> dict[str, Any]:
    """Create or edit a catalog entry. Keyed by version_id, else filename."""
    key = str(entry.get("version_id") or entry.get("key") or entry.get("filename") or "")
    with _lock:
        items = catalog()
        match = next((e for e in items if _key(e) == key), None)
        if match is None:
            match = {"enabled": True, "recommended_strength": 1.0, "nickname": "", "tags": [],
                     "trigger_words": [], "notes": []}
            if entry.get("version_id"):
                match["version_id"] = int(entry["version_id"])
            items.insert(0, match)
        for k in ("enabled", "recommended_strength", "nickname", "tags", "trigger_words", "notes",
                  "filename", "model_id", "base_model", "version_name", "image", "files"):
            if k in entry and entry[k] is not None:
                match[k] = entry[k]
        files = match.get("files")
        if files:
            main = next((f for f in files if f.get("role") == "main" and f.get("install", True)), files[0])
            match["filename"] = main["name"]
        _save(items)
        return match


def remove(key: str, delete_files: bool = False) -> None:
    with _lock:
        items = catalog()
        gone = [e for e in items if _key(e) == key]
        _save([e for e in items if _key(e) != key])
    if delete_files:
        names = [f["name"] for e in gone for f in _entry_files(e)] or [key]
        for n in names:
            p = (LORA_DIR / n).resolve()
            if LORA_DIR.resolve() in p.parents:
                p.unlink(missing_ok=True)


def add_version(version_id: int, file_ids: list[int] | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add a CivitAI LoRA version (all its model files, or just `file_ids`) and download them."""
    v = civitai.version(version_id)
    if not v["files"]:
        raise civitai.CivitaiError("that version has no .safetensors model file")
    wanted = set(file_ids or [f["id"] for f in v["files"] if f.get("suggested")])
    files = [{"id": f["id"], "name": f["name"], "family": f["family"], "role": f["role"],
              "primary": f.get("primary", False), "install": f["id"] in wanted} for f in v["files"]]
    existing = next((e for e in catalog() if str(e.get("version_id")) == str(version_id)), {})
    entry = upsert({
        "version_id": int(version_id),
        "nickname": existing.get("nickname") or v.get("model_name") or v.get("name"),
        "version_name": v.get("name"), "model_id": v.get("model_id"), "base_model": v.get("base_model"),
        "image": v.get("image"),
        "trigger_words": existing.get("trigger_words") or v.get("trained_words") or [],
        "files": _merge_files(existing.get("files"), files),
        **(overrides or {}),
    })
    enqueue_entry(entry)
    return entry


def _merge_files(old: list[dict[str, Any]] | None, new: list[dict[str, Any]],
                 keep_install: bool = False) -> list[dict[str, Any]]:
    """Keep the user's family/role/scale edits for files they already had.

    keep_install=True (background refresh) also keeps their install choices;
    an explicit add_version() choice wins otherwise."""
    prev = {f.get("name"): f for f in old or []}
    keys = ("family", "role", "scale") + (("install",) if keep_install else ())
    out = []
    for f in new:
        p = prev.get(f["name"])
        out.append({**f, **{k: p[k] for k in keys if k in p}} if p else f)
    return out


# --------------------------------------------------------------------------- checkpoints

def checkpoints() -> list[dict[str, Any]]:
    """Base checkpoints: image manifest entries + ones installed from CivitAI."""
    m = util.image_yaml("models.yaml")
    models = m.get("models") or {}
    out = []
    for cid, ck in (m.get("checkpoints") or {}).items():
        files = {fam: models[mid]["file"] for fam, mid in (ck.get("files") or {}).items() if mid in models}
        out.append({"id": cid, "label": ck.get("label", cid), "files": files, "builtin": True})
    for ck in (util.read_json(CHECKPOINTS, {}) or {}).get("items") or []:
        out.append({**ck, "builtin": False})
    for ck in out:
        ck["available"] = {fam: (paths.MODELS / rel).is_file() for fam, rel in ck["files"].items()}
    return out


def checkpoint_file(ck_id: str, family: str) -> str | None:
    """models-relative path of a checkpoint's diffusion model for a family."""
    ck = next((c for c in checkpoints() if c["id"] == ck_id), None)
    return (ck or {}).get("files", {}).get(family)


def add_checkpoint(version_id: int, file_id: int | None = None, label: str | None = None) -> dict[str, Any]:
    v = civitai.version(version_id)
    files = [f for f in v["files"] if not file_id or f["id"] == file_id]
    if not files:
        raise civitai.CivitaiError("no matching checkpoint file in that version")
    mapping: dict[str, str] = {}
    for f in files:
        rel = f"diffusion_models/{f['name']}"
        if f["family"] in ("fl2v", "any"):
            mapping.setdefault("fl2v", rel)
        if f["family"] in ("ref2v", "any"):
            mapping.setdefault("ref2v", rel)
    ck = {"id": f"civ{int(version_id)}", "label": label or f"{v.get('model_name')} · {v.get('name')}",
          "version_id": int(version_id), "files": mapping, "image": v.get("image")}
    with _lock:
        data = util.read_json(CHECKPOINTS, {}) or {}
        items = [c for c in data.get("items") or [] if c.get("id") != ck["id"]]
        items.insert(0, ck)
        util.write_json(CHECKPOINTS, {"items": items})
    for f in files:
        _enqueue(("checkpoint", {"version_id": int(version_id), "file": f, "dest": UNET_DIR / f["name"]}))
    return ck


def remove_checkpoint(ck_id: str, delete_files: bool = False) -> None:
    with _lock:
        data = util.read_json(CHECKPOINTS, {}) or {}
        items = data.get("items") or []
        gone = [c for c in items if c.get("id") == ck_id]
        util.write_json(CHECKPOINTS, {"items": [c for c in items if c.get("id") != ck_id]})
    if delete_files:
        for c in gone:
            for rel in set(c.get("files", {}).values()):
                p = (paths.MODELS / rel).resolve()
                if paths.MODELS.resolve() in p.parents:
                    p.unlink(missing_ok=True)


# --------------------------------------------------------------------------- downloads

def _set_status(key: str, **values: Any) -> None:
    with _lock:
        data = util.read_json(STATUS, {}) or {}
        data.setdefault("items", {}).setdefault(key, {}).update(values)
        data["updated_at"] = time.time()
        util.write_json(STATUS, data)


def _fetch(version_id: int, file: dict[str, Any], dest: Path, status_key: str) -> None:
    if dest.exists() and dest.stat().st_size > 1024 * 1024:
        _set_status(status_key, state="ready", error=None)
        return
    total = int(float(file.get("size_kb") or 0) * 1024) or None
    _set_status(status_key, state="downloading", done=0, total=total, error=None)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        done, last = 0, 0.0
        with requests.get(civitai.download_url(version_id, file.get("id")), stream=True,
                          headers={"User-Agent": "MMH3-Studio"}, timeout=(30, 600)) as r:
            if r.status_code in (401, 403):
                raise RuntimeError("CivitAI refused the download. Set CIVITAI_TOKEN on the pod.")
            r.raise_for_status()
            with tmp.open("wb") as out:
                for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                    out.write(chunk)
                    done += len(chunk)
                    if time.time() - last > 1.5:
                        _set_status(status_key, done=done)
                        last = time.time()
        os.replace(tmp, dest)
        _set_status(status_key, state="ready", done=done, error=None)
    except requests.RequestException as exc:
        tmp.unlink(missing_ok=True)
        _set_status(status_key, state="failed", error=f"download failed ({type(exc).__name__})")
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        _set_status(status_key, state="failed", error=str(exc)[:200])


def _enqueue(item: tuple[str, dict[str, Any]]) -> None:
    global _worker
    with _lock:
        _queue.append(item)
        if not (_worker and _worker.is_alive()):
            _worker = threading.Thread(target=_work, daemon=True, name="civitai-downloads")
            _worker.start()


def _work() -> None:
    while True:
        with _lock:
            if not _queue:
                return
            kind, job = _queue.pop(0)
        try:
            if kind == "lora":
                _fetch(job["version_id"], job["file"], LORA_DIR / job["file"]["name"],
                       f"{job['version_id']}:{job['file'].get('id')}")
            elif kind == "checkpoint":
                _fetch(job["version_id"], job["file"], job["dest"], f"ck{job['version_id']}:{job['file'].get('id')}")
            elif kind == "refresh":
                _refresh_entry(job)
        except Exception as exc:  # never let one bad entry stop the queue
            util.setup_logging("loras").warning("download job failed: %s", exc)


def enqueue_entry(entry: dict[str, Any]) -> None:
    disk = _disk(LORA_DIR)
    for f in _entry_files(entry):
        if f.get("install", True) and f["name"] not in disk and entry.get("version_id"):
            _enqueue(("lora", {"version_id": int(entry["version_id"]), "file": f}))


def _refresh_entry(entry: dict[str, Any]) -> None:
    """Old single-file entries: look up the version to learn file ids and extra files."""
    try:
        v = civitai.version(int(entry["version_id"]))
    except Exception as exc:
        _set_status(str(entry["version_id"]), state="failed", error=str(exc)[:300])
        return
    files = [{"id": f["id"], "name": f["name"], "family": f["family"], "role": f["role"],
              "primary": f.get("primary", False),
              "install": bool(f.get("suggested")) or f["name"] == entry.get("filename")}
             for f in v["files"]]
    if entry.get("filename") and not any(f["name"] == entry["filename"] for f in files):
        # keep a user-renamed file as the main one
        files.insert(0, {"id": None, "name": entry["filename"], "family": "any", "role": "main", "install": True})
    updated = upsert({"version_id": entry["version_id"], "files": _merge_files(entry.get("files"), files, keep_install=True),
                      "base_model": v.get("base_model"), "version_name": v.get("name"),
                      "model_id": v.get("model_id"), "image": entry.get("image") or v.get("image"),
                      "trigger_words": entry.get("trigger_words") or v.get("trained_words") or []})
    enqueue_entry(updated)


def sync() -> bool:
    """Queue downloads for every enabled entry; refresh old entries that lack file ids."""
    import re

    have = {str(e.get("version_id")) for e in catalog()}
    extra = [x for x in re.split(r"[,;\s]+", os.environ.get("CIVITAI_LORAS", "") + " "
                                  + os.environ.get("MMH3_LORA_VERSION_IDS", "")) if x.isdigit()]
    for vid in extra:
        if vid not in have:
            upsert({"version_id": int(vid)})
    for e in catalog():
        if not e.get("enabled", True) or not e.get("version_id"):
            continue
        if not e.get("files") or any(f.get("id") is None for f in e.get("files") or []):
            _enqueue(("refresh", e))
        else:
            enqueue_entry(e)
    return True
