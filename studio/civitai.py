"""CivitAI: search, bookmarks, your collections, version details.

Documented REST API (civitai.red/api/v1) for search, bookmarks (favorites=true)
and version details. The API's own text search only matches the start of a
model's name (searching "eros" finds nothing; "H3 Eros" finds anything named
"H3 ..."), so with "MiniMax H3 only" on, Studio downloads the whole H3 listing
(a few hundred models, a few seconds) and searches it locally instead. Named collections are only exposed through CivitAI's site
API (tRPC), which accepts the same API key but is undocumented, so it is
best-effort: failures come back as a readable message instead of breaking the
browser.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

import requests

from . import util

H3_BASE_MODEL = "MiniMax H3"
TYPES = {"lora": "LORA", "checkpoint": "Checkpoint"}
ALL_BROWSING_LEVELS = 31
_REF = re.compile(r"(?<![a-z])(ref2va?|r2va?|refva|reference|ref)(?![a-z])")
_FL = re.compile(r"(?<![a-z])(fl2va?|t2va?|i2va?|flva)(?![a-z])")
_HELPER = re.compile(r"(?<![a-z])(motion|helper|assist|aux|addon|add-on)")
# CivitAI renames uploads to camelCase ("comfyRef2vaPruned"); split words so the hints above match.
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# File types CivitAI uses for the weights themselves (it moved checkpoints to "Diffusion Model").
WEIGHT_TYPES = {None, "Model", "Pruned Model", "Diffusion Model", "UNet", "Enhancement LoRA", "Other"}
UNSUPPORTED_EXT = {".gguf": "GGUF needs the ComfyUI-GGUF loader, which this pod doesn't have. Pick a .safetensors version."}
INDEX_TTL = 15 * 60
PAGE = 20


class CivitaiError(RuntimeError):
    pass


def base_url() -> str:
    dom = str((util.settings().get("civitai") or {}).get("domain") or "civitai.red").strip().rstrip("/")
    return dom if dom.startswith("http") else f"https://{dom}"


def token() -> str | None:
    for name in ("CIVITAI_TOKEN", "civitai_token", "CIVITAI_API_KEY"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    return None


def _headers(auth: bool = True) -> dict[str, str]:
    h = {"User-Agent": "MMH3-Studio"}
    if auth and token():
        h["Authorization"] = f"Bearer {token()}"
    return h


def _get(path: str, params: Any = None, auth: bool = True, timeout: float = 30) -> Any:
    try:
        r = requests.get(base_url() + path, params=params, headers=_headers(auth), timeout=timeout)
    except requests.Timeout:
        raise CivitaiError(f"{base_url()} timed out. Try again in a moment.") from None
    except requests.RequestException as exc:
        raise CivitaiError(f"Can't reach {base_url()} from the pod ({type(exc).__name__}).") from None
    if r.status_code == 401:
        raise CivitaiError("CivitAI says unauthorized. Set CIVITAI_TOKEN (an API key from your CivitAI account settings).")
    if r.status_code == 429:
        raise CivitaiError("CivitAI rate limit hit. Try again in a minute.")
    try:
        body = r.json()
    except ValueError:
        raise CivitaiError(f"CivitAI returned HTTP {r.status_code}") from None
    if r.status_code >= 400:
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            err = (err.get("json") or err).get("message") or err
        raise CivitaiError(f"CivitAI {r.status_code}: {str(err or body)[:300]}")
    return body


def _trpc(path: str, payload: dict[str, Any]) -> Any:
    if not token():
        raise CivitaiError("Collections need CIVITAI_TOKEN set on the pod.")
    body = _get(f"/api/trpc/{path}", params={"input": json.dumps({"json": payload})})
    try:
        return body["result"]["data"]["json"]
    except (KeyError, TypeError):
        raise CivitaiError("CivitAI's collection API answered in an unexpected shape.") from None


# --------------------------------------------------------------------------- classification

def _words(text: str) -> str:
    return _CAMEL.sub("_", str(text or "")).lower()


def _family(text: str) -> str:
    n = _words(text)
    ref, fl = bool(_REF.search(n)), bool(_FL.search(n))
    if ref and fl:  # "[T2V/Ref2V]", "flREF2VA": made for both
        return "any"
    return "ref2v" if ref else "fl2v" if fl else "any"


def classify_files(files: list[dict[str, Any]], version_name: str = "") -> list[dict[str, Any]]:
    """Guess each file's family (fl2v / ref2v / any) and role (main / helper) from its name.

    A version that ships 'X.safetensors' and 'X-ref2va.safetensors' means the
    plain file is the T2V/I2V one, so an unhinted main file becomes fl2v when a
    sibling is explicitly R2V. When no file name has a hint, the version name
    decides ("Comfy Ref2va Pruned").
    """
    out = []
    for f in files:
        role = "helper" if _HELPER.search(_words(f.get("name"))) else "main"
        out.append({**f, "family": _family(f.get("name")), "role": role})
    if all(f["family"] == "any" for f in out):  # no file says, so the version name decides
        for f in out:
            f["family"] = _family(version_name)
    if any(f["family"] == "ref2v" for f in out):
        for f in out:
            if f["family"] == "any" and f["role"] == "main":
                f["family"] = "fl2v"
    # Suggest one main file per mode family (the primary, else the first) plus helpers.
    # Further mains for the same family are usually alternate precisions of the same LoRA.
    for f in out:
        f["suggested"] = f["role"] == "helper"
    for fam in ("any", "fl2v", "ref2v"):
        mains = [f for f in out if f["family"] == fam and f["role"] == "main" and not f.get("unsupported")]
        if mains:
            best = next((f for f in mains if f.get("primary")), mains[0])
            best["suggested"] = True
    return out


def _model_files(files: list[dict[str, Any]], version_name: str = "") -> list[dict[str, Any]]:
    """The weight files of a version (not its VAE, text encoder, workflow, training data...).
    Formats this pod can't load stay in the list, marked unsupported, so a version
    doesn't look empty."""
    keep = []
    for f in files or []:
        name = str(f.get("name") or "")
        ext = os.path.splitext(name.lower())[1]
        if f.get("type") not in WEIGHT_TYPES or ext not in (".safetensors", *UNSUPPORTED_EXT):
            continue
        item = {"id": f.get("id"), "name": name, "size_kb": f.get("sizeKB"),
                "primary": bool(f.get("primary")), "type": f.get("type")}
        if ext in UNSUPPORTED_EXT:
            item["unsupported"] = UNSUPPORTED_EXT[ext]
        keep.append(item)
    # Loadable first, then the primary file, then the files labelled as the model itself.
    keep.sort(key=lambda f: (bool(f.get("unsupported")), not f["primary"], f["type"] == "Other", f["name"]))
    return classify_files(keep, version_name)


def _image(version: dict[str, Any]) -> str | None:
    for img in version.get("images") or []:
        if img.get("url") and (img.get("type") in (None, "image")):
            url = img["url"]
            # CivitAI's image CDN accepts a width transform in the path.
            return re.sub(r"/(width=\d+|original=true)/", "/width=320/", url) if "/width=" in url or "/original=" in url else url
    return None


def normalize_version(v: dict[str, Any], model: dict[str, Any] | None = None) -> dict[str, Any]:
    model = model or v.get("model") or {}
    return {
        "id": v.get("id"), "name": v.get("name"), "base_model": v.get("baseModel"),
        "trained_words": v.get("trainedWords") or [], "files": _model_files(v.get("files") or [], v.get("name") or ""),
        "image": _image(v), "model_id": v.get("modelId") or model.get("id"),
        "model_name": model.get("name"), "type": model.get("type"),
    }


def normalize_model(m: dict[str, Any]) -> dict[str, Any]:
    versions = [normalize_version(v, m) for v in m.get("modelVersions") or []]
    return {
        "id": m.get("id"), "name": m.get("name"), "type": m.get("type"),
        "creator": (m.get("creator") or {}).get("username"), "nsfw": m.get("nsfw"),
        "tags": [t if isinstance(t, str) else (t or {}).get("name") for t in m.get("tags") or []],
        "image": next((v["image"] for v in versions if v.get("image")), None),
        "versions": versions,
    }


# --------------------------------------------------------------------------- queries

def _types(kind: str) -> list[str]:
    return [TYPES[kind]] if kind in TYPES else list(TYPES.values())


_index: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_index_lock = threading.Lock()


def _fetch_all(params: list[tuple[str, Any]], max_pages: int = 40) -> list[dict[str, Any]]:
    items, cursor = [], None
    for _ in range(max_pages):
        body = _get("/api/v1/models", params=params + ([("cursor", cursor)] if cursor else []), timeout=60)
        items += [normalize_model(m) for m in body.get("items") or []]
        cursor = (body.get("metadata") or {}).get("nextCursor")
        if not cursor:
            break
    return items


def _listing(kind: str, bookmarks: bool) -> list[dict[str, Any]]:
    """Every MiniMax H3 model of this kind (or every bookmark), newest first, cached a while."""
    key = (kind, "bookmarks" if bookmarks else "h3")
    with _index_lock:
        hit = _index.get(key)
        if hit and time.time() - hit[0] < INDEX_TTL:
            return hit[1]
        params: list[tuple[str, Any]] = [("limit", 100), ("nsfw", "true"), ("sort", "Newest")]
        params += [("types", t) for t in _types(kind)]
        if bookmarks:
            params.append(("favorites", "true"))
        else:
            params.append(("baseModels", H3_BASE_MODEL))
        items = _fetch_all(params)
        _index[key] = (time.time(), items)
        return items


def _haystack(m: dict[str, Any]) -> tuple[str, str]:
    """(name, everything else) as lowercase words, camelCase split, punctuation as spaces."""
    def norm(t: str) -> str:
        return " " + re.sub(r"[^a-z0-9]+", " ", _words(t)).strip() + " "
    rest = [m.get("creator") or "", *(m.get("tags") or [])]
    for v in m.get("versions") or []:
        rest += [v.get("name") or "", *(v.get("trained_words") or []), *(f.get("name") or "" for f in v.get("files") or [])]
    return norm(m.get("name") or ""), norm(" ".join(str(x) for x in rest if x))


def rank(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Models matching every word of the query (anywhere in a word, so "cherry" finds
    PinkCherry), names before creators / tags / versions / files, then newest first."""
    words = re.sub(r"[^a-z0-9]+", " ", _words(query)).split()
    if not words:
        return list(items)
    whole = " " + " ".join(words) + " "
    scored = []
    for n, m in enumerate(items):
        name, rest = _haystack(m)
        squashed = name.replace(" ", "")
        score = 0
        for w in words:
            if f" {w} " in name:
                score += 6
            elif f" {w}" in name:
                score += 4
            elif w in name or w in squashed:
                score += 3
            elif f" {w}" in rest:
                score += 2
            elif w in rest:
                score += 1
            else:
                break
        else:
            if whole.strip() == name.strip():
                score += 20
            elif whole in name:
                score += 8
            scored.append((-score, n, m))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [m for _, _, m in scored]


def search(query: str = "", kind: str = "lora", h3_only: bool = True, cursor: str | None = None,
           bookmarks: bool = False) -> dict[str, Any]:
    if bookmarks and not token():
        raise CivitaiError("Bookmarks need CIVITAI_TOKEN set on the pod.")
    if h3_only or bookmarks:
        items = rank(_listing(kind, bookmarks), query)
        if h3_only:  # a model can mix base models across versions; keep the H3 ones
            items = [{**it, "versions": [v for v in it["versions"] if v.get("base_model") == H3_BASE_MODEL]}
                     for it in items]
            items = [it for it in items if it["versions"]]
        start = int(cursor) if cursor and str(cursor).isdigit() else 0
        page = items[start:start + PAGE]
        return {"items": page, "next": str(start + PAGE) if start + PAGE < len(items) else None,
                "total": len(items)}
    # Every base model: too many to list, so this one is CivitAI's own (name-prefix) search.
    params: list[tuple[str, Any]] = [("limit", 20), ("nsfw", "true")]
    params += [("types", t) for t in _types(kind)]
    if query.strip():
        params.append(("query", query.strip()))
    else:
        params.append(("sort", "Newest"))
    if cursor:
        params.append(("cursor", cursor))
    body = _get("/api/v1/models", params=params)
    items = [normalize_model(m) for m in body.get("items") or []]
    return {"items": items, "next": (body.get("metadata") or {}).get("nextCursor")}


def version(version_id: int) -> dict[str, Any]:
    """A version as installs see it: only files this pod can load."""
    v = normalize_version(_get(f"/api/v1/model-versions/{int(version_id)}"))
    v["files"] = [f for f in v["files"] if not f.get("unsupported")]
    return v


def my_collections() -> list[dict[str, Any]]:
    data = _trpc("collection.getAllUser", {"permissions": ["VIEW"], "type": "Model", "contributingOnly": False})
    items = data if isinstance(data, list) else (data or {}).get("items") or []
    out = []
    for c in items:
        out.append({"id": c.get("id"), "name": c.get("name"), "type": c.get("type"),
                    "count": (c.get("_count") or {}).get("items") or c.get("itemCount")})
    return out


def collection_models(collection_id: int, cursor: Any = None, kind: str = "all", h3_only: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {"collectionId": int(collection_id), "limit": 40,
                               "browsingLevel": ALL_BROWSING_LEVELS, "sort": "Newest", "period": "AllTime"}
    if cursor:
        payload["cursor"] = cursor
    data = _trpc("model.getAll", payload)
    ids = [m.get("id") for m in (data or {}).get("items") or [] if m.get("id")]
    if not ids:
        return {"items": [], "next": None}
    params: list[tuple[str, Any]] = [("ids", ",".join(str(i) for i in ids)), ("nsfw", "true"), ("limit", 100)]
    body = _get("/api/v1/models", params=params)
    items = [normalize_model(m) for m in body.get("items") or []]
    wanted = set(_types(kind))
    items = [i for i in items if i.get("type") in wanted]
    if h3_only:
        for it in items:
            it["versions"] = [v for v in it["versions"] if v.get("base_model") == H3_BASE_MODEL]
        items = [i for i in items if i["versions"]]
    order = {mid: n for n, mid in enumerate(ids)}
    items.sort(key=lambda i: order.get(i["id"], 1e9))
    return {"items": items, "next": (data or {}).get("nextCursor")}


def download_url(version_id: int, file_id: int | None) -> str:
    url = f"{base_url()}/api/download/models/{int(version_id)}"
    params = {}
    if file_id:
        params["fileId"] = str(file_id)
    if token():
        params["token"] = token()
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return url
