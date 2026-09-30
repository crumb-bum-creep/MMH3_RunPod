"""CivitAI: search, bookmarks, your collections, version details.

Documented REST API (civitai.red/api/v1) for search, bookmarks (favorites=true)
and version details. Named collections are only exposed through CivitAI's site
API (tRPC), which accepts the same API key but is undocumented, so it is
best-effort: failures come back as a readable message instead of breaking the
browser.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

from . import util

H3_BASE_MODEL = "MiniMax H3"
TYPES = {"lora": "LORA", "checkpoint": "Checkpoint"}
ALL_BROWSING_LEVELS = 31
_REF = re.compile(r"(?<![a-z])(ref2va?|r2va?|refva|reference|ref)(?![a-z])")
_FL = re.compile(r"(?<![a-z])(fl2va?|t2va?|i2va?|flva)(?![a-z])")
_HELPER = re.compile(r"(?<![a-z])(motion|helper|assist|aux|addon|add-on)")


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

def classify_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Guess each file's family (fl2v / ref2v / any) and role (main / helper) from its name.

    A version that ships 'X.safetensors' and 'X-ref2va.safetensors' means the
    plain file is the T2V/I2V one, so an unhinted main file becomes fl2v when a
    sibling is explicitly R2V.
    """
    out = []
    for f in files:
        n = str(f.get("name") or "").lower()
        fam = "ref2v" if _REF.search(n) else "fl2v" if _FL.search(n) else "any"
        role = "helper" if _HELPER.search(n) else "main"
        out.append({**f, "family": fam, "role": role})
    if any(f["family"] == "ref2v" for f in out):
        for f in out:
            if f["family"] == "any" and f["role"] == "main":
                f["family"] = "fl2v"
    # Suggest one main file per mode family (the primary, else the first) plus helpers.
    # Further mains for the same family are usually alternate precisions of the same LoRA.
    for f in out:
        f["suggested"] = f["role"] == "helper"
    for fam in ("any", "fl2v", "ref2v"):
        mains = [f for f in out if f["family"] == fam and f["role"] == "main"]
        if mains:
            best = next((f for f in mains if f.get("primary")), mains[0])
            best["suggested"] = True
    return out


def _model_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = []
    for f in files or []:
        name = str(f.get("name") or "")
        if f.get("type") not in (None, "Model", "Pruned Model"):
            continue
        if not name.lower().endswith(".safetensors"):
            continue
        keep.append({"id": f.get("id"), "name": name, "size_kb": f.get("sizeKB"),
                     "primary": bool(f.get("primary")), "type": f.get("type")})
    keep.sort(key=lambda f: (not f["primary"], f["name"]))
    return classify_files(keep)


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
        "trained_words": v.get("trainedWords") or [], "files": _model_files(v.get("files") or []),
        "image": _image(v), "model_id": v.get("modelId") or model.get("id"),
        "model_name": model.get("name"), "type": model.get("type"),
    }


def normalize_model(m: dict[str, Any]) -> dict[str, Any]:
    versions = [normalize_version(v, m) for v in m.get("modelVersions") or []]
    return {
        "id": m.get("id"), "name": m.get("name"), "type": m.get("type"),
        "creator": (m.get("creator") or {}).get("username"), "nsfw": m.get("nsfw"),
        "image": next((v["image"] for v in versions if v.get("image")), None),
        "versions": versions,
    }


# --------------------------------------------------------------------------- queries

def _types(kind: str) -> list[str]:
    return [TYPES[kind]] if kind in TYPES else list(TYPES.values())


def search(query: str = "", kind: str = "lora", h3_only: bool = True, cursor: str | None = None,
           bookmarks: bool = False) -> dict[str, Any]:
    params: list[tuple[str, Any]] = [("limit", 20), ("nsfw", "true")]
    params += [("types", t) for t in _types(kind)]
    if h3_only:
        params.append(("baseModels", H3_BASE_MODEL))
    if query.strip():
        params.append(("query", query.strip()))
    else:
        params.append(("sort", "Newest"))
    if bookmarks:
        if not token():
            raise CivitaiError("Bookmarks need CIVITAI_TOKEN set on the pod.")
        params.append(("favorites", "true"))
    if cursor:
        params.append(("cursor", cursor))
    body = _get("/api/v1/models", params=params)
    items = [normalize_model(m) for m in body.get("items") or []]
    if h3_only:  # a model can mix base models across versions; keep the H3 ones
        for it in items:
            it["versions"] = [v for v in it["versions"] if v.get("base_model") == H3_BASE_MODEL] or it["versions"]
    return {"items": items, "next": (body.get("metadata") or {}).get("nextCursor")}


def version(version_id: int) -> dict[str, Any]:
    return normalize_version(_get(f"/api/v1/model-versions/{int(version_id)}"))


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
