"""Outputs library, input assets, and reference kits.

On-disk formats are the ones the previous MMH3 build used, so an existing volume
carries over untouched:
  data/output_library.json   {"groups": [...], "videos": {rel: {favorite, group, tags}}}
  data/output_meta.json      legacy {rel: generation record} (read-only here)
  <video>.h3.json            per-output generation record (sidecar)
  data/assets.json           {rel: {"nickname": ...}} for inputs
New:
  data/kits.json             saved reference sets
  data/library_index.json    cached scan of the output folder
  data/seen.json             which outputs you have opened ("new" badges)
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import media, paths, util

LIBRARY_FILE = paths.DATA / "output_library.json"
LEGACY_META = paths.DATA / "output_meta.json"
ASSETS_FILE = paths.DATA / "assets.json"
KITS_FILE = paths.DATA / "kits.json"
INDEX_FILE = paths.DATA / "library_index.json"
SEEN_FILE = paths.DATA / "seen.json"
_SAFE = re.compile(r"[^A-Za-z0-9._() +\-]+")
_lock = threading.RLock()


class LibraryError(ValueError):
    pass


def safe_rel(root: Path, rel: str) -> Path:
    """Resolve a user-supplied relative path, refusing anything outside root."""
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    p = (root / rel).resolve()
    root_r = root.resolve()
    if p != root_r and root_r not in p.parents:
        raise LibraryError("invalid path")
    return p


def safe_name(name: str) -> str:
    name = Path(name or "upload").name
    name = _SAFE.sub("_", name).strip(" .")
    return name[:150] or "upload"


# --------------------------------------------------------------------------- outputs

def _sidecar(video: Path) -> Path:
    return video.with_suffix(video.suffix + ".h3.json")


def _preview_png(video: Path) -> Path | None:
    """VHS writes <name>.png (first frame) next to <name>.mp4 / <name>-audio.mp4."""
    stem = re.sub(r"(?i)-audio$", "", video.stem)
    for cand in (video.with_name(stem + ".png"), video.with_name(video.stem + ".png")):
        if cand.exists():
            return cand
    return None


def _mode_from_name(rel: str) -> str:
    m = re.search(r"(?i)\b(t2v|i2v|r2v)", rel)
    return m.group(1).lower() if m else ""


def _scan() -> list[dict[str, Any]]:
    items = []
    legacy = util.read_json(LEGACY_META, {}) or {}
    for root, dirs, files in os.walk(paths.OUTPUT):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "MMH3Director"]
        names = set(files)
        for name in files:
            if not name.lower().endswith(".mp4"):
                continue
            stem = name[:-4]
            # Skip VHS's silent twin when the -audio version exists.
            if not stem.lower().endswith("-audio") and f"{stem}-audio.mp4" in names:
                continue
            p = Path(root) / name
            try:
                st = p.stat()
            except OSError:
                continue
            rel = p.relative_to(paths.OUTPUT).as_posix()
            meta = util.read_json(_sidecar(p), None)
            if not isinstance(meta, dict):
                meta = legacy.get(rel) if isinstance(legacy.get(rel), dict) else {}
            png = _preview_png(p)
            items.append({
                "file": rel,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "mode": (meta.get("mode") or _mode_from_name(rel) or "").lower(),
                "preview": png.relative_to(paths.OUTPUT).as_posix() if png else None,
                "meta": meta,
            })
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return items


def refresh_index() -> dict[str, Any]:
    with _lock:
        idx = {"updated_at": time.time(), "items": _scan()}
        util.write_json(INDEX_FILE, idx)
        return idx


def index(max_age: float = 30.0) -> dict[str, Any]:
    idx = util.read_json(INDEX_FILE, None)
    if not isinstance(idx, dict) or time.time() - float(idx.get("updated_at") or 0) > max_age:
        idx = refresh_index()
    return idx


def library_meta() -> dict[str, Any]:
    data = util.read_json(LIBRARY_FILE, {}) or {}
    return {"groups": list(data.get("groups") or []), "videos": dict(data.get("videos") or {})}


def _seen() -> dict[str, Any]:
    """{"baseline": t, "files": {rel: t}}. Outputs older than the baseline count as seen,
    so the first run doesn't flag a whole existing library as new."""
    data = util.read_json(SEEN_FILE, None)
    if not isinstance(data, dict) or not data.get("baseline"):
        data = {"baseline": time.time(), "files": {}}
        util.write_json(SEEN_FILE, data)
    data.setdefault("files", {})
    return data


def mark_seen(files: list[str], everything: bool = False) -> None:
    with _lock:
        data = _seen()
        if everything:
            data = {"baseline": time.time(), "files": {}}
        else:
            for rel in files:
                safe_rel(paths.OUTPUT, rel)
                data["files"][rel] = time.time()
        util.write_json(SEEN_FILE, data)


def outputs() -> dict[str, Any]:
    lib = library_meta()
    seen = _seen()
    items = []
    for it in index()["items"]:
        v = lib["videos"].get(it["file"]) or {}
        items.append({**it, "favorite": bool(v.get("favorite")), "group": v.get("group") or "",
                      "tags": list(v.get("tags") or []),
                      "new": it["mtime"] > seen["baseline"] and it["file"] not in seen["files"]})
    return {"items": items, "groups": lib["groups"], "new": sum(1 for i in items if i["new"])}


def update_output(rel: str, **changes: Any) -> dict[str, Any]:
    safe_rel(paths.OUTPUT, rel)
    with _lock:
        lib = library_meta()
        v = dict(lib["videos"].get(rel) or {})
        for k in ("favorite", "group", "tags"):
            if k in changes and changes[k] is not None:
                v[k] = changes[k]
        if v.get("group") and v["group"] not in lib["groups"]:
            lib["groups"].append(v["group"])
        if v.get("favorite") or v.get("group") or v.get("tags"):
            lib["videos"][rel] = v
        else:
            lib["videos"].pop(rel, None)
        util.write_json(LIBRARY_FILE, lib)
        return v


def set_groups(groups: list[str]) -> list[str]:
    with _lock:
        lib = library_meta()
        clean = list(dict.fromkeys(str(g).strip()[:60] for g in groups if str(g).strip()))
        lib["groups"] = clean
        for rel, v in list(lib["videos"].items()):
            if v.get("group") and v["group"] not in clean:
                v["group"] = ""
        util.write_json(LIBRARY_FILE, lib)
        return clean


def delete_output(rel: str) -> None:
    p = safe_rel(paths.OUTPUT, rel)
    stem = re.sub(r"(?i)-audio$", "", p.stem)
    for cand in (p, _sidecar(p), p.with_name(stem + ".mp4"), p.with_name(stem + ".png"),
                 _sidecar(p.with_name(stem + ".mp4"))):
        try:
            cand.unlink()
        except OSError:
            pass
    with _lock:
        lib = library_meta()
        if lib["videos"].pop(rel, None) is not None:
            util.write_json(LIBRARY_FILE, lib)
        seen = _seen()
        if seen["files"].pop(rel, None) is not None:
            util.write_json(SEEN_FILE, seen)
    _reserve_number(p.parent, stem)
    refresh_index()


_NUMBERED = re.compile(r"(?P<prefix>.+)_(?P<n>\d+)")
RESERVED_SUFFIX = "-number-reserved.txt"


def _reserve_number(folder: Path, stem: str) -> None:
    """Keep a deleted clip's number from being handed out again.

    VideoHelperSuite names the next clip <prefix>_<highest number in the folder + 1>,
    counting any file named <prefix>_<number>... So deleting the newest clips frees
    their numbers and the next render reuses a deleted clip's exact name. One small
    marker per prefix, at the highest number ever used, keeps numbers going up."""
    m = _NUMBERED.fullmatch(stem)
    if not m:
        return
    prefix, n = m["prefix"], int(m["n"])
    counter = re.compile(rf"{re.escape(prefix)}_(\d+)\D*\..+", re.IGNORECASE)  # VHS's own matcher
    try:
        names = os.listdir(folder)
    except OSError:
        return
    markers = [x for x in names if x.endswith(RESERVED_SUFFIX) and counter.fullmatch(x)]
    highest = max((int(mm.group(1)) for x in names if not x.endswith(RESERVED_SUFFIX)
                   for mm in [counter.fullmatch(x)] if mm), default=0)
    keep = max([n] + [int(counter.fullmatch(x).group(1)) for x in markers])
    if keep <= highest:  # a remaining clip already holds the highest number
        for x in markers:
            (folder / x).unlink(missing_ok=True)
        return
    width = len(m["n"])
    marker = folder / f"{prefix}_{keep:0{width}d}{RESERVED_SUFFIX}"
    if not marker.exists():
        marker.write_text("MMH3 Studio: keeps this clip number from being reused after a delete. Safe to leave.\n")
    for x in markers:
        if folder / x != marker:
            (folder / x).unlink(missing_ok=True)


def write_sidecar(rel: str, record: dict[str, Any]) -> None:
    util.write_json(_sidecar(safe_rel(paths.OUTPUT, rel)), record, indent=2)


def thumb_for_output(rel: str) -> Path | None:
    src = safe_rel(paths.OUTPUT, rel)
    key = hashlib.sha1(f"out:{rel}:{src.stat().st_mtime if src.exists() else 0}".encode()).hexdigest()[:20]
    dst = paths.THUMBS / f"{key}.jpg"
    if dst.exists():
        return dst
    png = _preview_png(src)
    ok = media.image_thumb(png, dst) if png else media.video_thumb(src, dst)
    return dst if ok else None


def normalize_record(meta: dict[str, Any]) -> dict[str, Any]:
    """Map a generation record (new or legacy) onto Create's form fields for Reuse."""
    if not isinstance(meta, dict):
        return {}
    if meta.get("studio"):  # written by this build
        out = {k: meta.get(k) for k in ("mode", "prompt_mode", "idea", "prompt", "aspect", "megapixels",
                                          "duration", "seed", "start_image", "end_image", "refs",
                                          "checkpoint", "recipe_id", "overrides")}
        out["loras"] = meta.get("lora_picks") or [{"key": l.get("file"), "file": l.get("file"),
                                                  "strength": l.get("strength", 1.0)} for l in meta.get("loras") or []]
        return out
    aspect = str(meta.get("aspect_ratio") or "9:16").split(" ")[0]
    ck = str(meta.get("base_checkpoint") or "stock")
    profile = str(meta.get("generation_profile") or "balanced").lower()
    recipe_id = {"fast": "fast", "fast4": "fast", "community": "upstream"}.get(profile, "balanced")
    if meta.get("mode") == "r2v" and recipe_id == "fast":
        recipe_id = "balanced"  # the old 4-step R2V recipe is the one that drifted
    return {
        "mode": meta.get("mode") or "t2v",
        "prompt_mode": "auto" if meta.get("prompt_mode") == "auto" else "manual",
        "idea": meta.get("prompt_idea") or "",
        "prompt": meta.get("actual_prompt") or meta.get("prompt") or "",
        "aspect": aspect,
        "megapixels": meta.get("megapixels") or 0.7,
        "duration": meta.get("duration") or 5,
        "seed": meta.get("seed"),
        "start_image": meta.get("starting_image"),
        "end_image": meta.get("ending_image"),
        "refs": meta.get("refs") or [],
        "loras": [_legacy_lora(l) for l in (meta.get("loras") or []) if isinstance(l, dict)],
        "checkpoint": "eros" if "eros" in ck.lower() else "stock",
        "recipe_id": recipe_id,
    }


def _legacy_lora(l: dict[str, Any]) -> dict[str, Any]:
    fn = l.get("filename") or l.get("lora") or l.get("file")
    key = str(l.get("version_id") or fn or "")
    return {"key": key, "file": fn, "strength": l.get("strength", l.get("recommended_strength", 1.0))}


# --------------------------------------------------------------------------- inputs

def _asset_meta() -> dict[str, Any]:
    return util.read_json(ASSETS_FILE, {}) or {}


def assets() -> list[dict[str, Any]]:
    meta = _asset_meta()
    out = []
    for root, dirs, files in os.walk(paths.INPUT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith(".") or name.startswith("validate_"):
                continue
            p = Path(root) / name
            k = media.kind(p)
            if k == "other":
                continue
            rel = p.relative_to(paths.INPUT).as_posix()
            try:
                st = p.stat()
            except OSError:
                continue
            entry = meta.get(rel) or {}
            out.append({
                "file": rel, "kind": k, "size": st.st_size, "mtime": st.st_mtime,
                "nickname": entry.get("nickname") or "",
                "has_audio": entry.get("has_audio"),
                "duration": entry.get("duration"),
            })
    out.sort(key=lambda a: a["mtime"], reverse=True)
    return out


def save_upload(filename: str, data_iter_path: Path, subdir: str = paths.UPLOAD_SUBDIR) -> dict[str, Any]:
    """Move an already-streamed temp file into input/<subdir>/YYYY-MM/ with a unique name."""
    name = safe_name(filename)
    if media.kind(name) == "other":
        raise LibraryError("unsupported file type")
    folder = paths.INPUT / subdir / time.strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / name
    n = 1
    while dst.exists():
        dst = folder / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    os.replace(data_iter_path, dst)
    rel = dst.relative_to(paths.INPUT).as_posix()
    info = media.probe(dst) if media.kind(dst) in ("video", "audio") else {}
    if info:
        with _lock:
            meta = _asset_meta()
            meta.setdefault(rel, {}).update({k: info[k] for k in ("has_audio", "duration") if k in info})
            util.write_json(ASSETS_FILE, meta)
    return {"file": rel, "kind": media.kind(dst), "size": dst.stat().st_size, "mtime": time.time(),
            "nickname": "", **{k: info.get(k) for k in ("has_audio", "duration")}}


def set_nickname(rel: str, nickname: str) -> None:
    safe_rel(paths.INPUT, rel)
    with _lock:
        meta = _asset_meta()
        entry = meta.setdefault(rel, {})
        if nickname.strip():
            entry["nickname"] = nickname.strip()[:80]
        else:
            entry.pop("nickname", None)
        if not entry:
            meta.pop(rel, None)
        util.write_json(ASSETS_FILE, meta)


def delete_asset(rel: str) -> None:
    p = safe_rel(paths.INPUT, rel)
    used = [k["name"] for k in kits() if any(r.get("file") == rel for r in k.get("refs") or [])]
    if used:
        raise LibraryError(f"used by kit(s): {', '.join(used)}")
    p.unlink(missing_ok=True)
    with _lock:
        meta = _asset_meta()
        if meta.pop(rel, None) is not None:
            util.write_json(ASSETS_FILE, meta)


def thumb_for_asset(rel: str) -> Path | None:
    src = safe_rel(paths.INPUT, rel)
    if not src.exists():
        return None
    key = hashlib.sha1(f"in:{rel}:{src.stat().st_mtime}".encode()).hexdigest()[:20]
    dst = paths.THUMBS / f"{key}.jpg"
    if dst.exists():
        return dst
    k = media.kind(src)
    ok = media.image_thumb(src, dst) if k == "image" else media.video_thumb(src, dst) if k == "video" else False
    return dst if ok else None


def use_soundtrack_default(rel: str) -> bool:
    """False for videos known to have no audio stream (keeps <Audio N> tags honest)."""
    entry = _asset_meta().get(rel) or {}
    if entry.get("has_audio") is None:
        info = media.probe(paths.INPUT / rel)
        if info:
            with _lock:
                meta = _asset_meta()
                meta.setdefault(rel, {}).update({k: info[k] for k in ("has_audio", "duration") if k in info})
                util.write_json(ASSETS_FILE, meta)
            return bool(info.get("has_audio"))
        return True
    return bool(entry.get("has_audio"))


def continue_frame(output_rel: str) -> str:
    """Extract an output's last frame into input/ and return its input-relative path."""
    src = safe_rel(paths.OUTPUT, output_rel)
    dst = paths.INPUT / paths.UPLOAD_SUBDIR / "last_frames" / (Path(output_rel).stem + "_last.png")
    if not dst.exists() and not media.last_frame(src, dst):
        raise LibraryError("could not extract the last frame")
    return dst.relative_to(paths.INPUT).as_posix()


def output_as_input(output_rel: str) -> str:
    """Hard-link (or copy) an output into input/ so it can be a reference."""
    src = safe_rel(paths.OUTPUT, output_rel)
    dst = paths.INPUT / paths.UPLOAD_SUBDIR / "from_outputs" / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        try:
            os.link(src, dst)
        except OSError:
            import shutil

            shutil.copy2(src, dst)
    return dst.relative_to(paths.INPUT).as_posix()


# --------------------------------------------------------------------------- kits

def kits() -> list[dict[str, Any]]:
    return list((util.read_json(KITS_FILE, {}) or {}).get("kits") or [])


def save_kit(name: str, refs: list[dict[str, Any]], kit_id: str | None = None) -> dict[str, Any]:
    name = str(name or "").strip()[:60]
    if not name:
        raise LibraryError("give the kit a name")
    clean = []
    for r in refs or []:
        if r.get("kind") not in ("image", "video", "audio") or not r.get("file"):
            continue
        safe_rel(paths.INPUT, r["file"])
        item = {"kind": r["kind"], "file": r["file"]}
        for k in ("use_soundtrack", "crop", "trim", "label"):
            if r.get(k) is not None:
                item[k] = r[k]
        clean.append(item)
    if not clean:
        raise LibraryError("a kit needs at least one reference")
    with _lock:
        data = util.read_json(KITS_FILE, {}) or {}
        items = list(data.get("kits") or [])
        now = time.time()
        for k in items:
            if k["id"] == kit_id or (not kit_id and k["name"].lower() == name.lower()):
                k.update(name=name, refs=clean, updated=now)
                util.write_json(KITS_FILE, {"kits": items})
                return k
        kit = {"id": uuid.uuid4().hex[:10], "name": name, "refs": clean, "created": now, "updated": now}
        items.insert(0, kit)
        util.write_json(KITS_FILE, {"kits": items})
        return kit


def delete_kit(kit_id: str) -> None:
    with _lock:
        data = util.read_json(KITS_FILE, {}) or {}
        items = [k for k in data.get("kits") or [] if k.get("id") != kit_id]
        util.write_json(KITS_FILE, {"kits": items})
