from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
from typing import Any

from aiohttp import web

try:
    import server_legacy as base
except ImportError:  # development/test checkout before runtime overlay
    import server as base

import output_indexer

APP_VERSION = "0.7.0-mmH3-library-index"
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
    return web.json_response({
        "items": items,
        "count": int(value.get("count") or len(items)),
        "index_updated_at": float(value.get("updated_at") or 0),
        "indexing": not INDEX_FILE.exists(),
        "index_error": value.get("error"),
    })


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


def _cache_first_free(unload_models: bool = True, free_memory_flag: bool = True) -> bool:
    # The generation admission path used to unload the active H3 model immediately
    # whenever host-memory pressure was detected. Preserve the hot model and ask
    # Comfy to clear reclaimable memory first; memory_guard.py performs automatic
    # escalation to a full unload only if pressure persists.
    return _real_free_memory(False, free_memory_flag)


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
    base.api_outputs = api_outputs
    base.api_delete_output = api_delete_output
    base.api_free = api_free
    base.index = index
    # Only the phone-server generation admission cleanup is softened here. The
    # supervisor memory guard runs in a separate process and uses its own staged
    # cache-first policy.
    base.comfy.free_memory = _cache_first_free

    app = base.make_app(comfy_url)
    app.router.add_get("/api/output-library", api_output_library_get)
    app.router.add_put("/api/output-library", api_output_library_put)
    app.on_startup.append(_start_indexer)
    app.on_cleanup.append(_stop_indexer)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(base.os.environ.get("MMH3_PHONE_UI_PORT", "7860")))
    parser.add_argument("--comfy", default=base.os.environ.get("MMH3_COMFY_URL", "http://127.0.0.1:8188"))
    args = parser.parse_args()
    web.run_app(make_app(args.comfy), host="0.0.0.0", port=args.port, access_log=None)


if __name__ == "__main__":
    main()
