from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from aiohttp import web

try:
    import server_legacy as base
except ImportError:  # development/test checkout before runtime overlay
    import server as base

import output_indexer

APP_VERSION = "0.8.0-mmH3-checkpoints-provisioning"
LIBRARY_FILE = base.DATA_ROOT / "output_library.json"
INDEX_FILE = output_indexer.INDEX_FILE
_real_free_memory = base.comfy.free_memory
_base_delete_output = base.api_delete_output
_base_patch_workflow = base.patch_workflow

STOCK_FL2VA = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
STOCK_REF2VA = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
EROS_BETA5_INT8 = "10Eros_Max_h3_hybrid_beta5_int8.safetensors"
CHECKPOINT_STOCK = "stock_convrot_int8"
CHECKPOINT_EROS = "eros_beta5_int8"


def _load_library() -> dict[str, Any]:
    value = base._load_json(LIBRARY_FILE, {})
    if not isinstance(value, dict):
        value = {}
    groups = value.get("groups") if isinstance(value.get("groups"), list) else []
    videos = value.get("videos") if isinstance(value.get("videos"), dict) else {}
    return {
        "version": 1,
        "groups": groups,
        "videos": videos,
        "updated_at": float(value.get("updated_at") or 0),
    }


def _clean_library(body: Any) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    raw_groups = body.get("groups") if isinstance(body.get("groups"), list) else []
    groups: list[str] = []
    seen: set[str] = set()
    for raw in raw_groups[:200]:
        name = str(raw or "").strip()[:80]
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            groups.append(name)

    raw_videos = body.get("videos") if isinstance(body.get("videos"), dict) else {}
    videos: dict[str, dict[str, Any]] = {}
    for raw_file, raw_meta in list(raw_videos.items())[:5000]:
        rel = str(raw_file or "").strip().replace("\\", "/")[:500]
        if not rel or rel.startswith("/") or ".." in Path(rel).parts or not isinstance(raw_meta, dict):
            continue
        group = str(raw_meta.get("group") or "").strip()[:80]
        tags: list[str] = []
        tag_seen: set[str] = set()
        for raw_tag in (raw_meta.get("tags") if isinstance(raw_meta.get("tags"), list) else [])[:30]:
            tag = str(raw_tag or "").strip()[:50]
            key = tag.casefold()
            if tag and key not in tag_seen:
                tag_seen.add(key)
                tags.append(tag)
        favorite = bool(raw_meta.get("favorite", False))
        if group or tags or favorite:
            videos[rel] = {"group": group, "tags": tags, "favorite": favorite}
        if group and group.casefold() not in seen:
            seen.add(group.casefold())
            groups.append(group)

    return {
        "version": 1,
        "groups": groups,
        "videos": videos,
        "updated_at": time.time(),
    }


def _checkpoint_choice(payload: dict[str, Any]) -> str:
    raw = str(payload.get("base_checkpoint") or CHECKPOINT_STOCK).strip().lower()
    aliases = {
        "stock": CHECKPOINT_STOCK,
        "stock_int8": CHECKPOINT_STOCK,
        CHECKPOINT_STOCK: CHECKPOINT_STOCK,
        "eros": CHECKPOINT_EROS,
        "eros_int8": CHECKPOINT_EROS,
        CHECKPOINT_EROS: CHECKPOINT_EROS,
    }
    choice = aliases.get(raw)
    if not choice:
        raise web.HTTPBadRequest(text=f"unknown base checkpoint: {raw}")
    return choice


def _checkpoint_filename(mode: str, choice: str) -> str:
    if choice == CHECKPOINT_EROS:
        return EROS_BETA5_INT8
    return STOCK_REF2VA if mode == "r2v" else STOCK_FL2VA


def _checkpoint_path(filename: str) -> Path:
    return base.COMFY_PERSIST / "models" / "diffusion_models" / filename


def _next_node_id(graph: dict[str, Any], prefix: str) -> str:
    candidate = prefix
    suffix = 1
    while candidate in graph:
        suffix += 1
        candidate = f"{prefix}_{suffix}"
    return candidate


def patch_workflow_v3(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply MMH3 v3 queue-time features on top of the d2103 workflow patcher."""
    graph, record = _base_patch_workflow(payload)
    mode = str(payload.get("mode") or "t2v").lower()
    prompt_mode = str(payload.get("prompt_mode") or "custom").lower()
    choice = _checkpoint_choice(payload)
    checkpoint = _checkpoint_filename(mode, choice)

    unets = base.find_nodes(graph, "UNETLoader")
    if not unets:
        raise web.HTTPServiceUnavailable(text="workflow has no UNETLoader node")
    for _, node in unets:
        node.setdefault("inputs", {})["unet_name"] = checkpoint

    ending_image = str(payload.get("ending_image") or "").strip()
    if ending_image and mode != "i2v":
        raise web.HTTPBadRequest(text="ending_image is only supported for I2V")

    if mode == "i2v" and ending_image:
        i2v_nodes = base.find_nodes(graph, "MiniMaxH3ImageToVideo")
        if not i2v_nodes:
            raise web.HTTPServiceUnavailable(text="I2V workflow has no MiniMaxH3ImageToVideo node")
        end_id = _next_node_id(graph, "mmh3_last_frame")
        graph[end_id] = {
            "inputs": {"image": ending_image},
            "class_type": "LoadImage",
            "_meta": {"title": "Load Last Frame"},
        }
        i2v_nodes[0][1].setdefault("inputs", {})["last_frame"] = [end_id, 0]

        # Auto I2V should let the prompt writer see both endpoints, not only the
        # starting frame. OpenRouterNode accepts multiple optional image inputs.
        if prompt_mode == "auto":
            for _, node in base.find_nodes(graph, "OpenRouterNode"):
                node.setdefault("inputs", {})["image_2"] = [end_id, 0]

    record["base_checkpoint"] = choice
    record["base_checkpoint_file"] = checkpoint
    record["ending_image"] = ending_image or None
    return graph, record


async def api_outputs(request: web.Request) -> web.Response:
    value = base._load_json(INDEX_FILE, {})
    if not isinstance(value, dict):
        value = {}
    items = value.get("items") if isinstance(value.get("items"), list) else []
    updated_at = float(value.get("updated_at") or 0)
    try:
        since = float(request.query.get("since") or 0)
    except (TypeError, ValueError):
        since = 0.0
    common = {
        "count": int(value.get("count") or len(items)),
        "index_updated_at": updated_at,
        "indexing": not INDEX_FILE.exists(),
        "index_error": value.get("error"),
    }
    if since and updated_at and updated_at <= since:
        return web.json_response({**common, "unchanged": True})
    return web.json_response({**common, "unchanged": False, "items": items})


async def api_output_library_get(request: web.Request) -> web.Response:
    return web.json_response(_load_library())


async def api_output_library_put(request: web.Request) -> web.Response:
    clean = _clean_library(await request.json())
    base._save_json(LIBRARY_FILE, clean)
    return web.json_response({"ok": True, **clean})


async def api_delete_output(request: web.Request) -> web.Response:
    rel = str(request.match_info.get("path") or "")
    response = await _base_delete_output(request)
    library = _load_library()
    videos = dict(library.get("videos") or {})
    if rel in videos:
        videos.pop(rel, None)
        library["videos"] = videos
        library["updated_at"] = time.time()
        base._save_json(LIBRARY_FILE, library)
    return response


async def api_free(request: web.Request) -> web.Response:
    mode = "full"
    try:
        body = await request.json()
        mode = str((body or {}).get("mode") or "full").lower()
    except Exception:
        pass
    cache_only = mode in {"cache", "cache-only", "cache_only"}
    ok = _real_free_memory(not cache_only, True)
    return web.json_response({"ok": ok, "mode": "cache" if cache_only else "full"})


async def api_generate(request: web.Request) -> web.Response:
    """Queue a generation without performing memory cleanup inside the request."""
    app = request.app
    payload = await request.json()

    requested_mode = str(payload.get("mode") or "t2v").lower()
    requested_family = "ref2v" if requested_mode == "r2v" else "fl2v"
    checkpoint_choice = _checkpoint_choice(payload)

    try:
        async with app["session"].get(f"{app['comfy']}/queue") as r:
            active_queue = await r.json()
    except Exception:
        active_queue = {"queue_running": [], "queue_pending": []}

    active_families: set[str] = set()
    for key in ("queue_running", "queue_pending"):
        for row in active_queue.get(key) or []:
            pid = str(row[1]) if isinstance(row, list) and len(row) > 1 else ""
            rec = app["records"].get(pid) or {}
            family = rec.get("model_family")
            if not family and rec.get("mode"):
                family = "ref2v" if rec.get("mode") == "r2v" else "fl2v"
            if family:
                active_families.add(str(family))

    if active_families and requested_family not in active_families:
        names = ", ".join(sorted(active_families))
        raise web.HTTPConflict(
            text=f"A {names} generation family is already running/queued. "
                 f"Finish that family first so MMH3 gets a memory-cleanup window before switching to {requested_family}."
        )

    provisioning = base._load_json(base.STATE_ROOT / "provisioning.json", {})
    if provisioning and not bool(provisioning.get("core_ready", False)):
        status = str(provisioning.get("status") or "pending")
        stage = str(provisioning.get("stage") or "models")
        message = str(provisioning.get("message") or "Core MiniMax H3 models are still provisioning")
        raise web.HTTPServiceUnavailable(
            text=f"{message} (status={status}, stage={stage}). "
                 "You can keep using the UI; generation will unlock automatically when stock core models are ready."
        )

    if checkpoint_choice == CHECKPOINT_EROS:
        eros_path = _checkpoint_path(EROS_BETA5_INT8)
        try:
            eros_ready = eros_path.is_file() and eros_path.stat().st_size >= 1024**3
        except OSError:
            eros_ready = False
        if not eros_ready:
            raise web.HTTPServiceUnavailable(
                text="Eros Max Beta5 INT8 is still provisioning. Stock H3 ConvRot is ready to use now; "
                     "choose Stock H3 ConvRot INT8 or wait for the Eros addon download to finish."
            )

    memory = base._load_json(base.STATE_ROOT / "memory.json", {})
    try:
        fraction = float(memory.get("fraction") or 0.0)
        critical_fraction = float(memory.get("critical_fraction") or 0.92)
        free_bytes = float(memory.get("free_bytes") or 0.0)
        updated_at = float(memory.get("updated_at") or 0.0)
    except (TypeError, ValueError):
        fraction, critical_fraction, free_bytes, updated_at = 0.0, 0.92, 0.0, 0.0

    memory_is_fresh = updated_at > 0 and (time.time() - updated_at) <= 15.0
    if memory_is_fresh and fraction >= critical_fraction:
        free_gib = free_bytes / (1024 ** 3)
        raise web.HTTPServiceUnavailable(
            text=f"MMH3 host memory is critically high ({fraction:.0%} used, {free_gib:.1f} GiB free). "
                 "The prompt was not queued and no memory cleanup was triggered by this submission."
        )

    graph, record = base.patch_workflow(payload)
    plan = base._make_plan(graph)
    async with app["session"].post(
        f"{app['comfy']}/prompt",
        json={"client_id": app["client_id"], "prompt": graph},
    ) as r:
        body = await r.text()
        if not r.ok:
            raise web.HTTPBadGateway(text=body)
        reply = json.loads(body)

    pid = str(reply["prompt_id"])
    app["plans"][pid] = plan
    app["records"][pid] = record
    base._touch(app, pid, status="queued", process_name="Waiting in queue")
    base._save_json(base.RECORD_FILE, app["records"])
    return web.json_response({"prompt_id": pid, "seed": record["seed"]})


async def index(request: web.Request) -> web.Response:
    html = (base.STATIC / "index.html").read_text(encoding="utf-8")
    if "library-v2.css" not in html:
        html = html.replace("</head>", '  <link rel="stylesheet" href="/static/library-v2.css?v=1">\n</head>')
    if "features-v3.css" not in html:
        html = html.replace("</head>", '  <link rel="stylesheet" href="/static/features-v3.css?v=1">\n</head>')
    legacy = '<script src="/static/app.js?v=2"></script>'
    upgraded = legacy + '\n<script src="/static/library-v2.js?v=1"></script>\n<script src="/static/features-v3.js?v=1"></script>'
    if "features-v3.js" not in html:
        if "library-v2.js" in html:
            html = html.replace('<script src="/static/library-v2.js?v=1"></script>', '<script src="/static/library-v2.js?v=1"></script>\n<script src="/static/features-v3.js?v=1"></script>')
        else:
            html = html.replace(legacy, upgraded)
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


async def _start_indexer(app: web.Application) -> None:
    app["output_indexer_task"] = asyncio.create_task(output_indexer.run_forever())


async def _stop_indexer(app: web.Application) -> None:
    task = app.get("output_indexer_task")
    if not task:
        return
    task.cancel()
    try:
        await task
    except BaseException:
        pass


def make_app(comfy_url: str) -> web.Application:
    base.APP_VERSION = APP_VERSION
    base.patch_workflow = patch_workflow_v3
    base.api_generate = api_generate
    base.api_outputs = api_outputs
    base.api_delete_output = api_delete_output
    base.api_free = api_free
    base.index = index

    app = base.make_app(comfy_url)
    app.router.add_get("/api/output-library", api_output_library_get)
    app.router.add_put("/api/output-library", api_output_library_put)
    app.on_startup.append(_start_indexer)
    app.on_cleanup.append(_stop_indexer)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("MMH3_PHONE_UI_PORT", "7860")))
    parser.add_argument("--comfy", default=os.environ.get("MMH3_COMFY_URL", "http://127.0.0.1:8188"))
    args = parser.parse_args()
    web.run_app(make_app(args.comfy), host="0.0.0.0", port=args.port, access_log=None)


if __name__ == "__main__":
    main()
