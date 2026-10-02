from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable
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


def _env_version_ids() -> list[int]:
    ids: list[int] = []
    for env_name in ("MMH3_LORA_VERSION_IDS", "LORAS_IDS_TO_DOWNLOAD", "CIVITAI_LORAS"):
        raw = os.environ.get(env_name, "")
        for part in re.split(r"[,;\s]+", raw.strip()):
            if part.isdigit():
                ids.append(int(part))
    return ids


def _version_ids(cfg: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for item in cfg.get("loras", []) or []:
        if item and item.get("enabled", True) and item.get("version_id"):
            ids.append(int(item["version_id"]))
    ids.extend(_env_version_ids())
    return list(dict.fromkeys(ids))


def _auto_download_ids(cfg: dict[str, Any]) -> set[int]:
    """Version IDs that may download without an explicit install request.

    Catalog entries are install-on-demand by default: startup only indexes
    them, and the phone UI offers missing ones under Quick install. Entries
    marked ``auto_download: true`` and IDs passed through the legacy env vars
    keep the old download-at-startup behaviour.
    """
    ids = set(_env_version_ids())
    for item in cfg.get("loras", []) or []:
        if item and item.get("version_id") and item.get("auto_download") is True:
            ids.add(int(item["version_id"]))
    return ids


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


def sync_loras(config_path: Path, install_ids: Iterable[int] | None = None) -> dict[str, Any]:
    """Index managed LoRAs and download the ones that are allowed to download.

    ``install_ids`` are explicit install requests from the UI. Anything else
    that is missing on disk is reported with ``status: "available"`` instead of
    being fetched from CivitAI.
    """
    cfg = load_yaml(config_path, {}) or {}
    version_ids = _version_ids(cfg)
    overrides = _overrides(cfg)
    downloadable = _auto_download_ids(cfg) | {int(x) for x in (install_ids or [])}
    token = os.environ.get("CIVITAI_TOKEN") or os.environ.get("civitai_token") or None
    root = COMFY_PERSIST / "models" / "loras"
    root.mkdir(parents=True, exist_ok=True)

    existing_by_id: dict[int, dict[str, Any]] = {}
    try:
        existing = json.loads((DATA_ROOT / "lora_catalog.json").read_text(encoding="utf-8"))
        for item in existing.get("managed") or []:
            if (item or {}).get("version_id"):
                existing_by_id[int(item["version_id"])] = item
    except Exception:
        pass

    def sync_one(vid: int) -> dict[str, Any]:
        override = overrides.get(vid, {})

        # Warm-volume fast path: if the managed weight is already present, do
        # not make a CivitAI API request just to rediscover metadata we already
        # persisted. Merge the editable YAML fields over the prior catalog so
        # UI edits take effect immediately and startup remains network-free.
        prior_entry = existing_by_id.get(vid, {})
        configured_name = str(override.get("filename") or prior_entry.get("filename") or "").strip()
        if configured_name:
            filename = _safe_filename(configured_name)
            dest = root / filename
            if dest.is_file() and dest.stat().st_size >= 1024 * 1024:
                prior = existing_by_id.get(vid, {})
                def chosen(key: str, fallback: Any) -> Any:
                    return override[key] if key in override else prior.get(key, fallback)
                return {
                    "version_id": vid,
                    "model_id": prior.get("model_id"),
                    "model_name": prior.get("model_name"),
                    "version_name": prior.get("version_name"),
                    "nickname": chosen("nickname", prior.get("model_name") or prior.get("version_name") or filename),
                    "filename": filename,
                    "path": str(dest),
                    "trigger_words": chosen("trigger_words", []),
                    "recommended_strength": chosen("recommended_strength", 1.0),
                    "notes": chosen("notes", []),
                    "tags": chosen("tags", []),
                    "managed": True,
                    "status": "ready",
                    "metadata_source": "persistent",
                }

        def not_installed(status: str, error: str | None = None) -> dict[str, Any]:
            # Catalog-only view of an entry whose weights are not on disk.
            prior = existing_by_id.get(vid, {})
            def known(key: str, fallback: Any) -> Any:
                return override[key] if key in override else prior.get(key, fallback)
            name = configured_name and _safe_filename(configured_name)
            row = {
                "version_id": vid,
                "model_id": prior.get("model_id"),
                "model_name": prior.get("model_name"),
                "version_name": prior.get("version_name"),
                "nickname": known("nickname", prior.get("model_name") or name or f"CivitAI {vid}"),
                "filename": name or None,
                "trigger_words": known("trigger_words", []),
                "recommended_strength": known("recommended_strength", 1.0),
                "notes": known("notes", []),
                "tags": known("tags", []),
                "managed": True,
                "status": status,
            }
            if error:
                row["error"] = error
            return row

        if vid not in downloadable:
            return not_installed("available")

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
                "trigger_words": override["trigger_words"] if "trigger_words" in override else (meta.get("trainedWords") or []),
                "recommended_strength": override.get("recommended_strength", 1.0),
                "notes": override["notes"] if "notes" in override else [],
                "tags": override["tags"] if "tags" in override else [],
                "managed": True,
                "status": "ready",
            }
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            hint = " (check CIVITAI_TOKEN)" if code in (401, 403) else ""
            return not_installed("error", f"CivitAI returned HTTP {code}{hint}")
        except requests.RequestException as exc:
            return not_installed("error", f"Could not reach CivitAI: {type(exc).__name__}")
        except Exception as exc:
            return not_installed("error", f"{type(exc).__name__}: {exc}"[:300])
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
