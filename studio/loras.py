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

Entry-level `auto_install` (default true): false keeps a LoRA out of the boot
sync. It is listed under Quick install in the LoRA screen and downloads with
one tap (`install`). `uninstall` deletes its files but keeps the entry there.

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


# The "recommended" LoRAs earlier images seeded under Quick install. No longer
# offered: install what you want from the CivitAI browser (or by link/id).
RETIRED_SEED = {3205014, 3206518, 3215304, 3224980, 3226989, 3228089, 3228867, 3236596, 3242519,
                3243753, 3250559, 3252213, 3258236, 3260276, 3264127, 3264805, 3266628, 3268303}
SEED_PRUNED = paths.DATA / "seed_loras_retired.flag"


def ensure_seeded() -> None:
    """A new volume starts with the image's seed catalog (empty now). Once per
    volume, the retired recommended LoRAs are dropped from an existing catalog,
    unless you installed one or set it to download on startup."""
    seed = paths.IMAGE_CONFIG / "loras.seed.yaml"
    if not CATALOG.exists():
        CATALOG.parent.mkdir(parents=True, exist_ok=True)
        if seed.exists():
            shutil.copy2(seed, CATALOG)
        else:
            _save([])
        SEED_PRUNED.parent.mkdir(parents=True, exist_ok=True)
        SEED_PRUNED.touch()
        return
    if SEED_PRUNED.exists():
        return
    disk = _disk(LORA_DIR)
    with _lock:
        items = catalog()
        keep = [e for e in items
                if not (_vid(e) in RETIRED_SEED and e.get("auto_install") is not True
                        and not any(f.get("name") in disk for f in _entry_files(e)))]
        if len(keep) != len(items):
            _save(keep)
    SEED_PRUNED.parent.mkdir(parents=True, exist_ok=True)
    SEED_PRUNED.touch()


def _vid(e: dict[str, Any]) -> int | None:
    try:
        return int(e.get("version_id"))
    except (TypeError, ValueError):
        return None


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


def auto_installs(e: dict[str, Any]) -> bool:
    return e.get("auto_install", True) is not False


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
            skey = f"url:{f['name']}" if f.get("url") else f"{e.get('version_id')}:{f.get('id')}"
            st = _live(skey, status.get(skey) or status.get(str(e.get("version_id"))) or {})
            installed = f["name"] in disk
            idle = ("queued" if auto_installs(e) else "available") if f.get("install", True) else "skipped"
            files.append({**f, "installed": installed,
                          "state": "ready" if installed else st.get("state") or idle,
                          "error": None if installed else st.get("error"),
                          "done": st.get("done"), "total": st.get("total")})
        families = sorted({f.get("family", "any") for f in files if f["installed"]})
        active = {fam: [{"name": f["name"], "role": f.get("role", "main"), "scale": float(f.get("scale", 1.0))}
                        for f in files_for(e, fam)] for fam in ("fl2v", "ref2v")}
        items.append({**{k: v for k, v in e.items() if k != "files"}, "key": _key(e), "files": files, "active": active,
                      "auto_install": auto_installs(e),
                      "installed": any(f["installed"] for f in files),
                      "families": ["fl2v", "ref2v"] if "any" in families else families})
    for fn in sorted(disk - known):
        if Path(fn).name.startswith(TURBO_PREFIXES):
            continue
        items.append({"key": fn, "filename": fn, "nickname": Path(fn).stem, "enabled": True, "installed": True,
                      "untracked": True, "recommended_strength": 1.0, "trigger_words": [], "tags": [],
                      "families": ["fl2v", "ref2v"],
                      "files": [{"name": fn, "family": "any", "role": "main", "installed": True, "state": "ready"}]})
    return {"items": items, "busy": busy(), "has_token": bool(civitai.token()),
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
                  "filename", "model_id", "base_model", "version_name", "image", "files", "auto_install",
                  "source_url"):
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


def install(key: str) -> dict[str, Any]:
    """Download a catalog entry now, whatever its auto_install says (Quick install)."""
    entry = next((e for e in catalog() if _key(e) == key), None)
    if entry is None:
        raise civitai.CivitaiError("that LoRA isn't in the catalog")
    if not entry.get("version_id"):
        if any(f.get("url") for f in entry.get("files") or []):
            enqueue_entry(entry, force=True)
            return entry
        raise civitai.CivitaiError("a local file has nothing to download")
    if not entry.get("files") or any(f.get("id") is None for f in entry.get("files") or []):
        _enqueue(("refresh", {**entry, "_download": True}))
    else:
        enqueue_entry(entry, force=True)
    return entry


def uninstall(key: str) -> dict[str, Any]:
    """Delete an entry's files but keep it in the catalog, under Quick install."""
    entry = next((e for e in catalog() if _key(e) == key), None)
    if entry is None:
        raise civitai.CivitaiError("that LoRA isn't in the catalog")
    for f in _entry_files(entry):
        p = (LORA_DIR / f["name"]).resolve()
        if LORA_DIR.resolve() in p.parents:
            p.unlink(missing_ok=True)
        _set_status(f"{entry.get('version_id')}:{f.get('id')}", state=None, error=None, done=None)
    return upsert({"key": key, "version_id": entry.get("version_id"), "auto_install": False})


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
    enqueue_entry(entry, force=True)  # an explicit install from CivitAI always downloads
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
    """Base checkpoints: image manifest entries + ones installed from CivitAI.

    A CivitAI checkpoint that isn't on disk yet carries `download`: its state
    (queued / downloading / failed / interrupted), bytes done and total, error."""
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
        if not ck["builtin"] and not all(ck["available"].values()):
            ck["download"] = _checkpoint_progress(ck)
    return out


def _checkpoint_progress(ck: dict[str, Any]) -> dict[str, Any]:
    rows = []
    prefix = f"ck{ck.get('version_id')}:"
    saved = (util.read_json(STATUS, {}) or {}).get("items") or {}
    names = {rel.split("/")[-1] for rel in (ck.get("files") or {}).values()}
    for f in ck.get("downloads") or [{"id": None, "name": n} for n in sorted(names)]:
        if (UNET_DIR / f["name"]).is_file():
            continue
        key = f"url:{f['name']}" if f.get("url") else f"{prefix}{f.get('id')}"
        if f.get("id") is None and not f.get("url"):  # a record from before `downloads`: file id unknown
            key = next((k for k in saved if k.startswith(prefix)), key)
        st = _live(key, saved.get(key) or {})
        part = UNET_DIR / (f["name"] + ".part")
        if not st.get("done") and part.exists():
            st["done"] = part.stat().st_size
        st.setdefault("total", int(float(f.get("size_kb") or 0) * 1024) or None)
        rows.append(st)
    if not rows:
        return {"state": "missing"}
    order = ("failed", "downloading", "queued", "interrupted", "missing")
    state = min((r.get("state") or "missing" for r in rows), key=lambda x: order.index(x) if x in order else 9)
    return {"state": state, "done": sum(r.get("done") or 0 for r in rows),
            "total": sum(r.get("total") or 0 for r in rows) or None,
            "error": next((r.get("error") for r in rows if r.get("error")), None),
            "note": next((r.get("note") for r in rows if r.get("note") and r.get("state") == "downloading"), None)}


def _live(key: str, st: dict[str, Any]) -> dict[str, Any]:
    """A saved status, corrected for a Studio restart: 'downloading' with no worker on it was interrupted."""
    st = dict(st)
    if st.get("state") in ("queued", "downloading") and key not in _pending:
        st["state"] = "interrupted"
    return st


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
          "version_id": int(version_id), "files": mapping, "image": v.get("image"),
          "downloads": [{"id": f["id"], "name": f["name"], "size_kb": f.get("size_kb")} for f in files]}
    with _lock:
        data = util.read_json(CHECKPOINTS, {}) or {}
        items = [c for c in data.get("items") or [] if c.get("id") != ck["id"]]
        items.insert(0, ck)
        util.write_json(CHECKPOINTS, {"items": items})
    enqueue_checkpoint(ck)
    return ck


def _checkpoint_downloads(ck: dict[str, Any]) -> list[dict[str, Any]]:
    """The files a CivitAI checkpoint downloads. Records saved before `downloads` existed
    are matched to the version's files by name (one CivitAI lookup)."""
    if ck.get("downloads"):
        return ck["downloads"]
    names = {rel.split("/")[-1] for rel in (ck.get("files") or {}).values()}
    v = civitai.version(int(ck["version_id"]))
    found = [{"id": f["id"], "name": f["name"], "size_kb": f.get("size_kb")} for f in v["files"] if f["name"] in names]
    if found:
        with _lock:
            data = util.read_json(CHECKPOINTS, {}) or {}
            for c in data.get("items") or []:
                if c.get("id") == ck.get("id"):
                    c["downloads"] = found
            util.write_json(CHECKPOINTS, data)
    return found


def enqueue_checkpoint(ck: dict[str, Any]) -> int:
    """Queue a checkpoint's missing files (resuming any .part). Returns how many were queued."""
    downloads = ck.get("downloads") or []
    if not ck.get("version_id") and not any(f.get("url") for f in downloads):
        return 0
    n = 0
    for f in downloads if not ck.get("version_id") else _checkpoint_downloads(ck):
        if not (UNET_DIR / f["name"]).is_file():
            vid = int(ck["version_id"]) if ck.get("version_id") else None
            n += _enqueue(("checkpoint", {"version_id": vid, "file": f, "dest": UNET_DIR / f["name"]}))
    return n


def safe_filename(name: str) -> str:
    """A bare .safetensors file name, safe to put in a models folder."""
    import re

    base = re.sub(r"[^A-Za-z0-9._()+-]+", "_", str(name or "").replace("\\", "/").split("/")[-1]).strip("._")
    if not base.lower().endswith(".safetensors"):
        raise civitai.CivitaiError(f"Studio can only load .safetensors files ({name or 'no file name'})")
    return base


def add_url_lora(files: list[dict[str, Any]], nickname: str | None = None, trigger_words: list[str] | None = None,
                 source: str | None = None) -> dict[str, Any]:
    """A LoRA from a Hugging Face or direct link: catalog entry keyed by its main file name."""
    clean = []
    for f in files:
        name = safe_filename(f.get("name"))
        guess = civitai.classify_files([{"name": name}])[0]
        clean.append({"id": None, "name": name, "url": f["url"], "size_kb": f.get("size_kb"),
                      "family": f.get("family") or guess["family"], "role": f.get("role") or guess["role"],
                      "install": True})
    if not clean:
        raise civitai.CivitaiError("nothing to install")
    entry = upsert({"key": clean[0]["name"], "filename": clean[0]["name"], "files": clean,
                    "nickname": nickname or Path(clean[0]["name"]).stem, "trigger_words": trigger_words or [],
                    "source_url": source or clean[0]["url"]})
    enqueue_entry(entry, force=True)
    return entry


def add_url_checkpoint(files: list[dict[str, Any]], label: str | None = None) -> dict[str, Any]:
    """A base checkpoint from a Hugging Face or direct link."""
    import hashlib

    downloads, mapping = [], {}
    for f in files[:1]:  # one diffusion model per checkpoint entry, like the CivitAI browser
        name = safe_filename(f.get("name"))
        fam = f.get("family") or civitai.classify_files([{"name": name}])[0]["family"]
        downloads.append({"id": None, "name": name, "url": f["url"], "size_kb": f.get("size_kb")})
        if fam in ("fl2v", "any"):
            mapping["fl2v"] = f"diffusion_models/{name}"
        if fam in ("ref2v", "any"):
            mapping["ref2v"] = f"diffusion_models/{name}"
    if not downloads:
        raise civitai.CivitaiError("nothing to install")
    ck = {"id": "url" + hashlib.sha1(downloads[0]["url"].encode()).hexdigest()[:8],
          "label": label or Path(downloads[0]["name"]).stem, "version_id": None, "files": mapping,
          "downloads": downloads, "source_url": downloads[0]["url"]}
    with _lock:
        data = util.read_json(CHECKPOINTS, {}) or {}
        items = [c for c in data.get("items") or [] if c.get("id") != ck["id"]]
        items.insert(0, ck)
        util.write_json(CHECKPOINTS, {"items": items})
    enqueue_checkpoint(ck)
    return ck


def install_checkpoint(ck_id: str) -> dict[str, Any]:
    """Download (or resume, or retry) a CivitAI checkpoint now."""
    ck = next((c for c in checkpoints() if c["id"] == ck_id), None)
    if ck is None:
        raise civitai.CivitaiError("that base model isn't in your list")
    if ck["builtin"]:
        raise civitai.CivitaiError("built-in base models download from System")
    enqueue_checkpoint(ck)
    return ck


def resume_checkpoints() -> None:
    """Boot: pick up every CivitAI checkpoint that isn't fully on disk yet."""
    for ck in (util.read_json(CHECKPOINTS, {}) or {}).get("items") or []:
        try:
            enqueue_checkpoint(ck)
        except Exception as exc:
            log.warning("can't resume checkpoint %s: %s", ck.get("id"), exc)


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
                    p.with_suffix(p.suffix + ".part").unlink(missing_ok=True)


# --------------------------------------------------------------------------- downloads
#
# Two lanes, each with its own worker thread: "small" for LoRAs and catalog
# refreshes, "big" for base checkpoints (20-60 GB), so a checkpoint never holds
# up a LoRA and vice versa. Every download resumes from its .part file and
# retries with a fresh CivitAI link: one dropped connection in a 20 GB transfer
# used to throw the whole file away with nothing on screen saying so.

BIG_FILE_KB = 4 * 1024 * 1024
RETRIES = 8
_queues: dict[str, list[tuple[str, dict[str, Any]]]] = {"small": [], "big": []}
_workers: dict[str, threading.Thread] = {}
_pending: set[str] = set()   # status keys queued or downloading, so a second tap doesn't double up
log = util.setup_logging("loras")


class _Retry(Exception):
    """A failure worth another attempt (dropped connection, short read)."""


class _Fatal(Exception):
    """A failure no retry will fix (refused, not a model file, disk full)."""


def _set_status(key: str, **values: Any) -> None:
    with _lock:
        data = util.read_json(STATUS, {}) or {}
        data.setdefault("items", {}).setdefault(key, {}).update(values)
        data["updated_at"] = time.time()
        util.write_json(STATUS, data)


def _status(key: str) -> dict[str, Any]:
    return ((util.read_json(STATUS, {}) or {}).get("items") or {}).get(key) or {}


REFUSED = {
    "CivitAI": "CivitAI refused the download. Set CIVITAI_TOKEN on the pod (some models also need you to accept "
               "their terms on the site once).",
    "Hugging Face": "Hugging Face refused the download: the repo is private or gated. Set HF_TOKEN on the pod "
                    "(and accept the model's terms on its page).",
}


def _source_of(url: str) -> str:
    host = url.split("://", 1)[-1].split("/", 1)[0].lower()
    if "civitai" in host:
        return "CivitAI"
    if host.endswith(("huggingface.co", "hf.co")):
        return "Hugging Face"
    return host or "the server"


def _auth_for(url: str) -> dict[str, str]:
    """Hugging Face takes HF_TOKEN as a header (dropped on its CDN redirect, which is signed)."""
    if _source_of(url) == "Hugging Face" and os.environ.get("HF_TOKEN", "").strip():
        return {"Authorization": f"Bearer {os.environ['HF_TOKEN'].strip()}"}
    return {}


def _attempt(url: str, tmp: Path, status_key: str) -> tuple[int, int | None]:
    """One pass at the download, continuing tmp if it exists. Returns (bytes on disk, total)."""
    src = _source_of(url)
    have = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": "MMH3-Studio", **_auth_for(url)}
    if have:
        headers["Range"] = f"bytes={have}-"
    try:
        r = requests.get(url, stream=True, headers=headers, timeout=(30, 300), allow_redirects=True)
    except requests.RequestException as exc:
        raise _Retry(f"couldn't reach {src} ({type(exc).__name__})") from None
    with r:
        if r.status_code in (401, 403):
            raise _Fatal(REFUSED.get(src, f"{src} refused the download (HTTP {r.status_code})."))
        if r.status_code == 404:
            raise _Fatal(f"{src} says this file doesn't exist (any more)")
        if r.status_code == 416 and have:  # asked past the end: the .part is already whole (or bigger)
            return have, have
        if r.status_code == 429 or r.status_code >= 500:
            raise _Retry(f"{src} answered HTTP {r.status_code}")
        if r.status_code not in (200, 206):
            raise _Fatal(f"{src} answered HTTP {r.status_code}")
        ctype = r.headers.get("Content-Type", "")
        if ctype.startswith(("text/html", "application/json")):
            raise _Fatal(f"{src} sent a web page instead of the file. Check the link (and the token, if it "
                         "needs one).")
        length = int(r.headers.get("Content-Length") or 0) or None
        if r.status_code == 206:
            rng = r.headers.get("Content-Range", "")  # bytes 100-999/1000
            total = int(rng.rsplit("/", 1)[1]) if "/" in rng and rng.rsplit("/", 1)[1].isdigit() else (
                have + length if length else None)
            mode = "ab"
        else:  # the server ignored the range: start over
            have, total, mode = 0, length, "wb"
        _set_status(status_key, state="downloading", done=have, total=total, error=None)
        done, last = have, 0.0
        try:
            with tmp.open(mode) as out:
                for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                    out.write(chunk)
                    done += len(chunk)
                    if time.time() - last > 1.5:
                        _set_status(status_key, done=done, total=total)
                        last = time.time()
        except requests.RequestException as exc:  # (an OSError subclass, so first)
            _set_status(status_key, done=done)
            raise _Retry(f"the connection dropped at {done // (1024 * 1024)} MB ({type(exc).__name__})") from None
        except OSError as exc:
            if getattr(exc, "errno", None) == 28:
                raise _Fatal("the volume is full. Free some space (or remove a model) and tap Retry.") from None
            raise _Fatal(f"couldn't write the file: {exc}") from None
        return done, total


def _fetch(version_id: int, file: dict[str, Any], dest: Path, status_key: str, retries: int = RETRIES) -> bool:
    """Download one CivitAI file to dest, resuming and retrying. True when it's on disk."""
    return _download(lambda: civitai.download_url(version_id, file.get("id")), file, dest, status_key, retries)


def _download(url_fn, file: dict[str, Any], dest: Path, status_key: str, retries: int = RETRIES) -> bool:
    """Download to dest, resuming from dest.part and asking url_fn() for a fresh link each attempt."""
    if dest.exists() and dest.stat().st_size > 1024 * 1024:
        _set_status(status_key, state="ready", error=None)
        return True
    expected = int(float(file.get("size_kb") or 0) * 1024) or None
    tmp = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _set_status(status_key, state="downloading", done=tmp.stat().st_size if tmp.exists() else 0,
                total=expected, error=None, attempt=1)
    last_error = "download failed"
    for attempt in range(1, retries + 1):
        try:
            done, total = _attempt(url_fn(), tmp, status_key)
            if total and done < total:
                raise _Retry(f"the connection ended early ({done // (1024 * 1024)} of {total // (1024 * 1024)} MB)")
            if not total and expected and done < expected * 0.98:
                raise _Retry("the file came back shorter than CivitAI lists it")
            os.replace(tmp, dest)
            _set_status(status_key, state="ready", done=done, total=total or done, error=None)
            log.info("downloaded %s (%d MB)", dest.name, done // (1024 * 1024))
            return True
        except _Fatal as exc:
            last_error = str(exc)
            break
        except _Retry as exc:
            last_error = str(exc)
            log.warning("%s: attempt %d/%d: %s", dest.name, attempt, retries, exc)
            if attempt < retries:
                _set_status(status_key, state="downloading", error=None, attempt=attempt + 1,
                            note=f"retrying: {last_error}")
                time.sleep(min(60, 5 * attempt))
        except Exception as exc:  # anything else: report it, keep the .part for a Retry
            last_error = f"{type(exc).__name__}: {exc}"[:200]
            break
    log.warning("download of %s failed: %s", dest.name, last_error)
    _set_status(status_key, state="failed", error=last_error[:300])
    return False


def _job_key(kind: str, job: dict[str, Any]) -> str:
    if job.get("file", {}).get("url"):
        return f"url:{job['file']['name']}"
    if kind == "lora":
        return f"{job['version_id']}:{job['file'].get('id')}"
    if kind == "checkpoint":
        return f"ck{job['version_id']}:{job['file'].get('id')}"
    return f"refresh:{job.get('version_id')}"


def _enqueue(item: tuple[str, dict[str, Any]]) -> bool:
    """Queue a job on its lane. False when the same file is already queued or downloading."""
    kind, job = item
    key = _job_key(kind, job)
    big = kind == "checkpoint" or float((job.get("file") or {}).get("size_kb") or 0) > BIG_FILE_KB
    lane = "big" if big else "small"
    with _lock:
        if key in _pending:
            return False
        _pending.add(key)
        _queues[lane].append(item)
        if kind in ("lora", "checkpoint"):
            _set_status(key, state="queued", error=None)
        w = _workers.get(lane)
        if not (w and w.is_alive()):
            w = threading.Thread(target=_work, args=(lane,), daemon=True, name=f"civitai-{lane}")
            _workers[lane] = w
            w.start()
    return True


def busy() -> bool:
    return any(w.is_alive() for w in _workers.values())


def _work(lane: str) -> None:
    from . import bootgate

    bootgate.wait_open()  # never download while the volume is still being copied in
    while True:
        with _lock:
            if not _queues[lane]:
                _workers.pop(lane, None)
                return
            kind, job = _queues[lane].pop(0)
        key = _job_key(kind, job)
        try:
            if job.get("file", {}).get("url"):
                url = job["file"]["url"]
                _download(lambda: url, job["file"], job.get("dest") or LORA_DIR / job["file"]["name"], key)
            elif kind == "lora":
                _fetch(job["version_id"], job["file"], LORA_DIR / job["file"]["name"], key)
            elif kind == "checkpoint":
                _fetch(job["version_id"], job["file"], job["dest"], key)
            elif kind == "refresh":
                _refresh_entry(job)
        except Exception as exc:  # never let one bad entry stop the queue
            log.warning("download job failed: %s", exc)
            if kind in ("lora", "checkpoint"):
                _set_status(key, state="failed", error=str(exc)[:200])
        finally:
            with _lock:
                _pending.discard(key)


def enqueue_entry(entry: dict[str, Any], force: bool = False) -> None:
    """Queue an entry's missing files. A quick-install entry (auto_install false) is
    only fetched when forced, or when some of its files are already on disk (you
    ticked another file of a LoRA you installed)."""
    disk = _disk(LORA_DIR)
    files = _entry_files(entry)
    if not (force or auto_installs(entry) or any(f["name"] in disk for f in files)):
        return
    for f in files:
        if not f.get("install", True) or f["name"] in disk:
            continue
        if f.get("url"):
            _enqueue(("lora", {"version_id": None, "file": f, "dest": LORA_DIR / f["name"]}))
        elif entry.get("version_id"):
            _enqueue(("lora", {"version_id": int(entry["version_id"]), "file": f}))


def _refresh_entry(entry: dict[str, Any]) -> None:
    """Old single-file entries: look up the version to learn file ids and extra files.

    Quick-install entries get their details (files, preview image) without a download,
    unless the refresh was queued by install()."""
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
    enqueue_entry(updated, force=bool(entry.get("_download")))


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
        if e.get("enabled", True) and not e.get("version_id") and any(f.get("url") for f in e.get("files") or []):
            enqueue_entry(e)  # added by link (Hugging Face / direct URL)
            continue
        if not e.get("enabled", True) or not e.get("version_id"):
            continue
        if not e.get("files") or any(f.get("id") is None for f in e.get("files") or []):
            _enqueue(("refresh", e))
        else:
            enqueue_entry(e)
    resume_checkpoints()
    return True
