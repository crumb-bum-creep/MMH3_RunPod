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

APP_VERSION = "0.7.2-mmH3-library-index"
LIBRARY_FILE = base.DATA_ROOT / "output_library.json"
INDEX_FILE = output_indexer.INDEX_FILE
_real_free_memory = base.comfy.free_memory
_base_delete_output = base.api_delete_output


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
    """Queue a generation without performing memory cleanup inside the request.

    The legacy d2103 endpoint coupled admission to the memory guard's ordinary
    pressure threshold. A submit could therefore unload models, wait up to 45s,
    and still reject before POSTing to Comfy. The supervisor already owns memory
    cleanup, so this route now has one job: validate/patch and enqueue.

    A *fresh* critical-memory reading may still reject the request as a final OOM
    safety rail, but rejection is side-effect free: this endpoint never calls
    /free and never waits for a cleanup cycle.
    """
    app = request.app
    payload = await request.json()

    requested_mode = str(payload.get("mode") or "t2v").lower()
    requested_family = "ref2v" if requested_mode == "r2v" else "fl2v"

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
                 "You can keep using the UI; generation will unlock automatically when core models are ready."
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
    legacy = '<script src="/static/app.js?v=2"></script>'
    upgraded = legacy + '\n<script src="/static/library-v2.js?v=1"></script>'
    if "library-v2.js" not in html:
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
