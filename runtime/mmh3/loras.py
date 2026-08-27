from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests

from .common import COMFY_PERSIST, DATA_ROOT, dump_json, load_yaml

_SAFE = re.compile(r"[^A-Za-z0-9._ ()\-+]+")


def _safe_filename(name: str) -> str:
    name = Path(name).name.strip() or "lora.safetensors"
    return _SAFE.sub("_", name)[:240]


def _with_token(url: str, token: str | None) -> str:
    if not token:
        return url
    p = urlsplit(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q["token"] = token
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


def _version_ids(cfg: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for item in cfg.get("loras", []) or []:
        if item and item.get("enabled", True) and item.get("version_id"):
            ids.append(int(item["version_id"]))
    for env_name in ("MMH3_LORA_VERSION_IDS", "LORAS_IDS_TO_DOWNLOAD", "CIVITAI_LORAS"):
        raw = os.environ.get(env_name, "")
        for part in re.split(r"[,;\s]+", raw.strip()):
            if part.isdigit():
                ids.append(int(part))
    return list(dict.fromkeys(ids))


def _overrides(cfg: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out = {}
    for item in cfg.get("loras", []) or []:
        if item and item.get("version_id"):
            out[int(item["version_id"])] = item
    return out


def _workers() -> int:
    try:
        return max(1, min(8, int(os.environ.get("MMH3_LORA_DOWNLOAD_WORKERS", "3"))))
    except ValueError:
        return 3


def sync_loras(config_path: Path) -> dict[str, Any]:
    cfg = load_yaml(config_path, {}) or {}
    version_ids = _version_ids(cfg)
    overrides = _overrides(cfg)
    token = os.environ.get("CIVITAI_TOKEN") or os.environ.get("civitai_token") or None
    root = COMFY_PERSIST / "models" / "loras"
    root.mkdir(parents=True, exist_ok=True)

    def sync_one(vid: int) -> dict[str, Any]:
        override = overrides.get(vid, {})
        session = requests.Session()
        if token:
            session.headers["Authorization"] = f"Bearer {token}"
        try:
            meta_r = session.get(f"https://civitai.com/api/v1/model-versions/{vid}", timeout=30)
            meta_r.raise_for_status()
            meta = meta_r.json()
            files = meta.get("files") or []
            candidates = [f for f in files if str(f.get("name", "")).lower().endswith(".safetensors")]
            if not candidates:
                candidates = files
            if not candidates:
                raise RuntimeError("version metadata has no downloadable files")
            file_meta = next((f for f in candidates if f.get("primary")), candidates[0])
            filename = _safe_filename(override.get("filename") or file_meta.get("name") or f"civitai_{vid}.safetensors")
            dest = root / filename
            if not dest.is_file() or dest.stat().st_size < 1024 * 1024:
                download = file_meta.get("downloadUrl") or f"https://civitai.com/api/download/models/{vid}"
                download = _with_token(download, token)
                tmp = dest.with_suffix(dest.suffix + ".part")
                with session.get(download, stream=True, timeout=(30, 600)) as r:
                    r.raise_for_status()
                    with tmp.open("wb") as out:
                        for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                            if chunk:
                                out.write(chunk)
                os.replace(tmp, dest)
            model = meta.get("model") or {}
            return {
                "version_id": vid,
                "model_id": model.get("id"),
                "model_name": model.get("name"),
                "version_name": meta.get("name"),
                "nickname": override.get("nickname") or model.get("name") or meta.get("name") or filename,
                "filename": filename,
                "path": str(dest),
                "trigger_words": override.get("trigger_words") or meta.get("trainedWords") or [],
                "recommended_strength": override.get("recommended_strength", 1.0),
                "notes": override.get("notes") or [],
                "tags": override.get("tags") or [],
                "managed": True,
                "status": "ready",
            }
        except Exception as exc:
            return {"version_id": vid, "managed": True, "status": "error", "error": repr(exc)}
        finally:
            session.close()

    managed: list[dict[str, Any]]
    workers = _workers()
    if workers == 1 or len(version_ids) <= 1:
        managed = [sync_one(vid) for vid in version_ids]
    else:
        by_id: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(workers, len(version_ids)), thread_name_prefix="mmh3-lora") as pool:
            future_to_id = {pool.submit(sync_one, vid): vid for vid in version_ids}
            for future in as_completed(future_to_id):
                vid = future_to_id[future]
                by_id[vid] = future.result()
        managed = [by_id[vid] for vid in version_ids]

    known = {x.get("filename") for x in managed if x.get("filename")}
    unmanaged = []
    for p in sorted(root.rglob("*.safetensors")):
        rel = p.relative_to(root).as_posix()
        if p.name not in known and rel not in known:
            unmanaged.append({
                "nickname": p.stem,
                "filename": rel,
                "path": str(p),
                "trigger_words": [],
                "recommended_strength": 1.0,
                "notes": [],
                "tags": [],
                "managed": False,
                "status": "ready",
            })
    catalog = {"managed": managed, "unmanaged": unmanaged}
    dump_json(DATA_ROOT / "lora_catalog.json", catalog)
    return catalog
