"""Extra ComfyUI workflows (config/workflows.yaml), installed into ComfyUI's Workflows sidebar.

They are fetched on the pod from their source (CivitAI needs CIVITAI_TOKEN for
these), never bundled in the image, and never overwritten once present, so
edits made in ComfyUI survive restarts. `check()` compares a downloaded
workflow with what this ComfyUI has: node types it doesn't know and model
files that aren't on disk, so a missing piece shows up in System instead of
as red nodes.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from . import civitai, paths, util

STATUS = paths.STATE / "workflows.json"
MODEL_EXT = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin", ".sft")
# Frontend-only node types that never appear in /object_info.
UI_ONLY = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode", "GroupNode", "Comment"}
log = util.setup_logging("workflows")


def manifest() -> dict[str, Any]:
    return util.image_yaml("workflows.yaml")


def folder() -> Path:
    return paths.COMFY_USER / "default" / "workflows" / str(manifest().get("folder") or "Studio extras")


def _status() -> dict[str, Any]:
    return util.read_json(STATUS, {}) or {}


def _set(wid: str, **values: Any) -> None:
    data = _status()
    data.setdefault(wid, {}).update(values, updated=time.time())
    util.write_json(STATUS, data)


def fetch(force: bool = False, only: str | None = None) -> dict[str, Any]:
    """Download missing workflows (or all of them with force). Returns status by id."""
    target = folder()
    for wf in manifest().get("workflows") or []:
        if only and wf["id"] != only:
            continue
        dest = target / wf["filename"]
        if dest.exists() and not force:
            _set(wf["id"], state="ready", error=None)
            continue
        civ = wf.get("civitai") or {}
        if not civitai.token():
            _set(wf["id"], state="needs_token", error="Set CIVITAI_TOKEN on the pod: CivitAI only lets logged-in accounts download this workflow.")
            continue
        try:
            r = requests.get(civitai.download_url(int(civ["version_id"]), civ.get("file_id")),
                             headers={"User-Agent": "MMH3-Studio"}, timeout=(20, 120))
            if r.status_code in (401, 403):
                raise RuntimeError("CivitAI refused the download. Check CIVITAI_TOKEN.")
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict) or not ("nodes" in data or any(isinstance(v, dict) and "class_type" in v
                                                                          for v in data.values())):
                raise RuntimeError("the download isn't a ComfyUI workflow")
            target.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".part")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, dest)
            _set(wf["id"], state="ready", error=None)
            log.info("installed workflow %s -> %s", wf["id"], dest)
        except (requests.RequestException, ValueError, RuntimeError, KeyError) as exc:
            _set(wf["id"], state="failed", error=str(exc)[:300])
            log.warning("workflow %s failed: %s", wf["id"], exc)
    return _status()


def _nodes(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every node in a UI-format workflow, including those inside subgraphs."""
    nodes = list(data.get("nodes") or [])
    for sg in ((data.get("definitions") or {}).get("subgraphs") or []):
        nodes += list(sg.get("nodes") or [])
    return [n for n in nodes if isinstance(n, dict)]


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def check(data: dict[str, Any], node_types: set[str] | None) -> dict[str, list[str]]:
    """Node types this ComfyUI doesn't have, and model files the workflow names that aren't on disk."""
    subgraph_ids = {sg.get("id") for sg in ((data.get("definitions") or {}).get("subgraphs") or [])}
    if "nodes" in data:
        types = {n.get("type") for n in _nodes(data)}
        widgets = [n.get("widgets_values") for n in _nodes(data)]
    else:  # API format
        types = {v.get("class_type") for v in data.values() if isinstance(v, dict)}
        widgets = [v.get("inputs") for v in data.values() if isinstance(v, dict)]
    types = {t for t in types if t and t not in UI_ONLY and t not in subgraph_ids}
    missing_nodes = sorted(types - node_types) if node_types is not None else []
    on_disk = {p.name for p in paths.MODELS.rglob("*") if p.is_file()} if paths.MODELS.exists() else set()
    named = {Path(s.replace("\\", "/")).name for w in widgets for s in _strings(w) if s.lower().endswith(MODEL_EXT)}
    return {"missing_nodes": missing_nodes, "missing_models": sorted(n for n in named if n not in on_disk)}


def listing(node_types: set[str] | None = None) -> list[dict[str, Any]]:
    """Each workflow with its install state and, once downloaded, what it's missing."""
    status = _status()
    out = []
    for wf in manifest().get("workflows") or []:
        dest = folder() / wf["filename"]
        st = status.get(wf["id"]) or {}
        row = {"id": wf["id"], "title": wf["title"], "note": wf.get("note") or "", "source": wf.get("source"),
               "path": str(dest.relative_to(paths.COMFY_USER / "default" / "workflows")),
               "installed": dest.exists(), "state": "ready" if dest.exists() else st.get("state") or "missing",
               "error": None if dest.exists() else st.get("error"), "needs": wf.get("needs") or []}
        if dest.exists():
            try:
                row.update(check(json.loads(dest.read_text(encoding="utf-8")), node_types))
            except (OSError, ValueError) as exc:
                row["error"] = f"can't read it: {exc}"
        out.append(row)
    return out
