from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientTimeout, web

try:
    import server_legacy as base
except ImportError:  # development/test checkout before runtime overlay
    import server as base

import output_indexer

APP_VERSION = "0.10.0-mmH3-gallery-nav-prompt-edit"
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

PROFILE_BALANCED = "balanced"
PROFILE_FAST = "fast"
FL2V_FAST = "minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors"
FL2V_BALANCED = "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"
REF2V_FAST = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
REF2V_BALANCED = "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"

PROMPT_EDIT_MODEL = os.environ.get("MMH3_PROMPT_EDIT_MODEL", "google/gemini-3-flash-preview").strip()
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
SEEN_LIMIT = 5000


def _load_library() -> dict[str, Any]:
    value = base._load_json(LIBRARY_FILE, {})
    if not isinstance(value, dict):
        value = {}
    groups = value.get("groups") if isinstance(value.get("groups"), list) else []
    videos = value.get("videos") if isinstance(value.get("videos"), dict) else {}
    seen = value.get("seen") if isinstance(value.get("seen"), dict) else {}
    try:
        baseline = float(value.get("seen_baseline") or 0)
    except (TypeError, ValueError):
        baseline = 0.0
    return {
        "version": 1,
        "groups": groups,
        "videos": videos,
        "seen": seen,
        "seen_baseline": baseline,
        "updated_at": float(value.get("updated_at") or 0),
    }


def _library_with_baseline() -> dict[str, Any]:
    """Load the library, establishing the "new video" baseline on first use.

    Videos that already existed when unwatched tracking was introduced count
    as seen, so the gallery does not light up every old output as NEW.
    """
    library = _load_library()
    if not library["seen_baseline"]:
        library["seen_baseline"] = time.time()
        library["updated_at"] = time.time()
        base._save_json(LIBRARY_FILE, library)
    return library


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


def _generation_profile(payload: dict[str, Any]) -> str:
    raw = str(payload.get("generation_profile") or PROFILE_BALANCED).strip().lower()
    aliases = {
        "balanced": PROFILE_BALANCED,
        "quality": PROFILE_BALANCED,
        "8step": PROFILE_BALANCED,
        "fast": PROFILE_FAST,
        "legacy": PROFILE_FAST,
        "4step": PROFILE_FAST,
    }
    choice = aliases.get(raw)
    if not choice:
        raise web.HTTPBadRequest(text=f"unknown generation profile: {raw}")
    return choice


def _profile_spec(mode: str, profile: str) -> dict[str, Any]:
    if mode == "r2v":
        if profile == PROFILE_FAST:
            return {
                "lora": REF2V_FAST,
                "strength": 0.85,
                "steps": 4,
                "sampler": "seeds_2",
                "legacy": True,
                "shift_video": 12.0,
                "shift_audio": 3.0,
                "scheduler": "legacy_beta",
            }
        return {
            "lora": REF2V_BALANCED,
            "strength": 1.0,
            "steps": 8,
            "sampler": "euler",
            "legacy": False,
            "shift_video": 12.0,
            "shift_audio": 3.0,
            "scheduler": "simple",
        }

    if profile == PROFILE_FAST:
        return {
            "lora": FL2V_FAST,
            "strength": 1.0,
            "steps": 4,
            "sampler": "euler",
            "legacy": False,
            "shift_video": 6.0,
            "shift_audio": 3.0,
            "scheduler": "simple",
        }
    return {
        "lora": FL2V_BALANCED,
        "strength": 1.0,
        "steps": 8,
        "sampler": "euler",
        "legacy": False,
        "shift_video": 6.0,
        "shift_audio": 3.0,
        "scheduler": "simple",
    }


def _profile_path(filename: str) -> Path:
    return base.COMFY_PERSIST / "models" / "loras" / filename


def _profile_turbo_node(graph: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    nodes = base.find_nodes(graph, "LoraLoaderModelOnly")
    if not nodes:
        raise web.HTTPServiceUnavailable(text="workflow has no generation-profile Turbo LoRA node")
    for nid, node in nodes:
        title = str((node.get("_meta") or {}).get("title") or "").lower()
        if "turbo" in title or "generation profile" in title:
            return nid, node
    return nodes[0]


def _ensure_profile_node(
    graph: dict[str, Any],
    class_type: str,
    prefix: str,
    title: str,
) -> tuple[str, dict[str, Any]]:
    nodes = base.find_nodes(graph, class_type)
    if nodes:
        return nodes[0]
    nid = _next_node_id(graph, prefix)
    graph[nid] = {"inputs": {}, "class_type": class_type, "_meta": {"title": title}}
    return nid, graph[nid]


def _patch_generation_profile(
    graph: dict[str, Any],
    mode: str,
    profile: str,
) -> dict[str, Any]:
    spec = _profile_spec(mode, profile)
    turbo_id, turbo = _profile_turbo_node(graph)
    inp = turbo.setdefault("inputs", {})
    inp["lora_name"] = spec["lora"]
    inp["strength_model"] = spec["strength"]
    turbo.setdefault("_meta", {})["title"] = (
        "Generation Profile Turbo LoRA · Fast"
        if profile == PROFILE_FAST
        else "Generation Profile Turbo LoRA · Balanced"
    )

    for _, node in base.find_nodes(graph, "KSamplerSelect"):
        node.setdefault("inputs", {})["sampler_name"] = spec["sampler"]

    guiders = base.find_nodes(graph, "BasicGuider")
    samplers = base.find_nodes(graph, "SamplerCustomAdvanced")
    if not guiders or not samplers:
        raise web.HTTPServiceUnavailable(text="workflow is missing profile sampler/guider nodes")

    if spec["legacy"]:
        beta_id, beta = _ensure_profile_node(
            graph, "BetaSamplingScheduler", "mmh3_fast_beta", "Fast Legacy Beta Scheduler"
        )
        beta["inputs"] = {
            "steps": 4,
            "alpha": 0.6,
            "beta": 0.6,
            "model": [turbo_id, 0],
        }
        extend_id, extend = _ensure_profile_node(
            graph, "ExtendIntermediateSigmas", "mmh3_fast_sigmas", "Fast Legacy Sigma Extension"
        )
        extend["inputs"] = {
            "steps": 2,
            "start_at_sigma": 0.8,
            "end_at_sigma": 0,
            "spacing": "linear",
            "sigmas": [beta_id, 0],
        }
        for _, node in guiders:
            node.setdefault("inputs", {})["model"] = [turbo_id, 0]
        for _, node in samplers:
            node.setdefault("inputs", {})["sigmas"] = [extend_id, 0]
    else:
        shift_id, shift = _ensure_profile_node(
            graph, "MiniMaxH3SigmaShift", "mmh3_profile_shift", "Generation Profile Sigma Shift"
        )
        shift["inputs"] = {
            "model": [turbo_id, 0],
            "shift_video": spec["shift_video"],
            "shift_audio": spec["shift_audio"],
        }
        shift.setdefault("_meta", {})["title"] = (
            "Generation Profile Sigma Shift · Fast"
            if profile == PROFILE_FAST
            else "Generation Profile Sigma Shift · Balanced"
        )

        scheduler_id, scheduler = _ensure_profile_node(
            graph, "BasicScheduler", "mmh3_profile_scheduler", "Generation Profile Scheduler"
        )
        scheduler["inputs"] = {
            "model": [shift_id, 0],
            "scheduler": "simple",
            "steps": spec["steps"],
            "denoise": 1.0,
        }
        scheduler.setdefault("_meta", {})["title"] = (
            "Generation Profile Scheduler · Fast"
            if profile == PROFILE_FAST
            else "Generation Profile Scheduler · Balanced"
        )
        for _, node in guiders:
            node.setdefault("inputs", {})["model"] = [shift_id, 0]
        for _, node in samplers:
            node.setdefault("inputs", {})["sigmas"] = [scheduler_id, 0]

    return spec


def _next_node_id(graph: dict[str, Any], prefix: str) -> str:
    candidate = prefix
    suffix = 1
    while candidate in graph:
        suffix += 1
        candidate = f"{prefix}_{suffix}"
    return candidate


def _effective_seconds(duration: Any) -> float:
    """Clip length after the workflows' frame rounding (24 fps, 17n+5 frames)."""
    try:
        seconds = float(duration or 5)
    except (TypeError, ValueError):
        seconds = 5.0
    frames = max(5, round(seconds * 24))
    frames += (5 - frames % 17) % 17
    return frames / 24


def patch_workflow_v3(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply MMH3 v3 queue-time features on top of the d2103 workflow patcher."""
    graph, record = _base_patch_workflow(payload)
    mode = str(payload.get("mode") or "t2v").lower()
    prompt_mode = str(payload.get("prompt_mode") or "custom").lower()
    choice = _checkpoint_choice(payload)
    checkpoint = _checkpoint_filename(mode, choice)
    generation_profile = _generation_profile(payload)

    unets = base.find_nodes(graph, "UNETLoader")
    if not unets:
        raise web.HTTPServiceUnavailable(text="workflow has no UNETLoader node")
    for _, node in unets:
        node.setdefault("inputs", {})["unet_name"] = checkpoint

    profile_spec = _patch_generation_profile(graph, mode, generation_profile)

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
        # The note switches the writer to MiniMax's FL2VA format, whose
        # alignment line needs the effective (frame-rounded) clip length.
        if prompt_mode == "auto":
            note = (
                "\n\n## This job\nTwo images are attached: <Picture 1> is the first frame and Picture 2 is the "
                f"last frame. Use the FL2VA format. Effective video duration: {_effective_seconds(record.get('duration')):.2f} seconds."
            )
            for _, node in base.find_nodes(graph, "OpenRouterNode"):
                inp = node.setdefault("inputs", {})
                inp["image_2"] = [end_id, 0]
                inp["system_prompt"] = str(inp.get("system_prompt") or "") + note

    record["base_checkpoint"] = choice
    record["base_checkpoint_file"] = checkpoint
    record["generation_profile"] = generation_profile
    record["turbo_lora"] = profile_spec["lora"]
    record["turbo_steps"] = profile_spec["steps"]
    record["turbo_sampler"] = profile_spec["sampler"]
    record["turbo_scheduler"] = profile_spec["scheduler"]
    record["turbo_shift_video"] = profile_spec["shift_video"]
    record["turbo_shift_audio"] = profile_spec["shift_audio"]
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
    return web.json_response(_library_with_baseline())


async def api_output_library_put(request: web.Request) -> web.Response:
    clean = _clean_library(await request.json())
    # Watched state is server-owned (see /api/output-library/seen); a stale
    # client PUT must not roll it back.
    current = _library_with_baseline()
    clean["seen"] = current["seen"]
    clean["seen_baseline"] = current["seen_baseline"]
    base._save_json(LIBRARY_FILE, clean)
    return web.json_response({"ok": True, **clean})


async def api_output_library_seen(request: web.Request) -> web.Response:
    """Mark generated videos as watched, or everything with {"all": true}."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    library = _library_with_baseline()
    now = time.time()
    if body.get("all"):
        # Moving the baseline marks everything older as seen and lets the
        # per-file map be dropped entirely.
        library["seen_baseline"] = now
        library["seen"] = {}
    else:
        raw_files = body.get("files") if isinstance(body.get("files"), list) else [body.get("file")]
        seen = dict(library["seen"])
        for raw in raw_files[:500]:
            rel = str(raw or "").strip().replace("\\", "/")[:500]
            if not rel or rel.startswith("/") or ".." in Path(rel).parts:
                continue
            seen[rel] = now
        if len(seen) > SEEN_LIMIT:
            seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-SEEN_LIMIT:])
        library["seen"] = seen
    library["updated_at"] = now
    base._save_json(LIBRARY_FILE, library)
    return web.json_response({"ok": True, "seen": library["seen"], "seen_baseline": library["seen_baseline"], "updated_at": now})


async def api_delete_output(request: web.Request) -> web.Response:
    rel = str(request.match_info.get("path") or "")
    response = await _base_delete_output(request)
    library = _load_library()
    videos = dict(library.get("videos") or {})
    seen = dict(library.get("seen") or {})
    if rel in videos or rel in seen:
        videos.pop(rel, None)
        seen.pop(rel, None)
        library["videos"] = videos
        library["seen"] = seen
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
    generation_profile = _generation_profile(payload)

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

    profile_spec = _profile_spec(requested_mode, generation_profile)
    progress_rows = provisioning.get("model_progress") if isinstance(provisioning, dict) else None
    profile_row_id = (
        "ref2v_turbo_4step" if requested_mode == "r2v" and generation_profile == PROFILE_FAST
        else "ref2v_turbo_8step" if requested_mode == "r2v"
        else "fl2v_turbo_4step" if generation_profile == PROFILE_FAST
        else "fl2v_turbo_8step"
    )
    # New images publish accelerator rows during bootstrap. Older persisted
    # provisioning state did not know about generation profiles, so don't turn
    # that legacy state into a false 503 before bootstrap refreshes it.
    profile_managed = isinstance(progress_rows, dict) and profile_row_id in progress_rows
    if profile_managed:
        turbo_path = _profile_path(profile_spec["lora"])
        try:
            turbo_ready = turbo_path.is_file() and turbo_path.stat().st_size >= 500 * 1024**2
        except OSError:
            turbo_ready = False
        if not turbo_ready:
            raise web.HTTPServiceUnavailable(
                text=f"{generation_profile.title()} generation profile is still provisioning "
                     f"({profile_spec['lora']}). Choose another ready profile or wait for this Turbo file to finish."
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


def _lora_catalog() -> dict[str, Any]:
    catalog = base._load_json(base.DATA_ROOT / "lora_catalog.json", {"managed": [], "unmanaged": []})
    return catalog if isinstance(catalog, dict) else {"managed": [], "unmanaged": []}


async def api_loras(request: web.Request) -> web.Response:
    """Ready LoRAs for generation plus catalog entries that are not installed yet."""
    catalog = _lora_catalog()
    ready: list[dict[str, Any]] = []
    for group in ("managed", "unmanaged"):
        for item in catalog.get(group) or []:
            if isinstance(item, dict) and item.get("status") == "ready":
                ready.append(item)
    jobs: dict[int, dict[str, Any]] = request.app["lora_installs"]
    available: list[dict[str, Any]] = []
    for item in catalog.get("managed") or []:
        if not isinstance(item, dict) or item.get("status") not in ("available", "error"):
            continue
        try:
            vid = int(item.get("version_id") or 0)
        except (TypeError, ValueError):
            continue
        job = jobs.get(vid) or {}
        available.append({
            **item,
            "install_status": job.get("status") or ("error" if item.get("status") == "error" else "available"),
            "install_error": job.get("error") or item.get("error"),
        })
    return web.json_response({
        "items": ready,
        "available": available,
        "installing": [vid for vid, job in jobs.items() if job.get("status") == "installing"],
    })


async def _run_lora_sync(app: web.Application, install_ids: list[int] | None = None) -> dict[str, Any]:
    # Serialise catalog rebuilds so a slow install cannot be overwritten by a
    # concurrent sync that saw its file as still missing.
    async with app["lora_lock"]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: base.sync_loras(base.CONFIG_ROOT / "loras.yaml", install_ids=install_ids)
        )


async def api_sync_loras(request: web.Request) -> web.Response:
    """Re-index the catalog. Downloads only happen for explicit install IDs."""
    install_ids: list[int] = []
    if request.can_read_body:
        try:
            body = await request.json()
            install_ids = [int(x) for x in (body or {}).get("install") or []]
        except Exception:
            install_ids = []
    return web.json_response(await _run_lora_sync(request.app, install_ids))


async def _install_lora(app: web.Application, vid: int) -> None:
    jobs = app["lora_installs"]
    try:
        catalog = await _run_lora_sync(app, [vid])
        row = next((x for x in catalog.get("managed") or [] if int(x.get("version_id") or 0) == vid), {})
        if row.get("status") == "ready":
            jobs.pop(vid, None)
        else:
            jobs[vid] = {"status": "error", "error": str(row.get("error") or "download did not complete")}
    except Exception as exc:
        jobs[vid] = {"status": "error", "error": repr(exc)}


async def api_install_lora(request: web.Request) -> web.Response:
    """Start a background CivitAI download for one catalog entry.

    Runs detached because LoRA downloads routinely outlive the RunPod proxy's
    request timeout; the UI polls /api/loras for completion.
    """
    try:
        vid = int(request.match_info["vid"])
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="invalid version id")
    jobs = request.app["lora_installs"]
    if (jobs.get(vid) or {}).get("status") != "installing":
        jobs[vid] = {"status": "installing", "started_at": time.time()}
        task = asyncio.create_task(_install_lora(request.app, vid))
        request.app["lora_install_tasks"].add(task)
        task.add_done_callback(request.app["lora_install_tasks"].discard)
    return web.json_response({"ok": True, "version_id": vid, "status": "installing"})


def _default_prompts() -> dict[str, str]:
    cfg = base.load_yaml(base.IMAGE_ROOT / "config" / "system_prompts.yaml", {}) or {}
    prompts = cfg.get("prompts") if isinstance(cfg, dict) else None
    return prompts if isinstance(prompts, dict) else {}


async def api_prompts_get(request: web.Request) -> web.Response:
    return web.json_response({"prompts": base._system_prompts(), "defaults": _default_prompts()})


PROMPT_EDIT_SYSTEM = """You are a precise editor for MiniMax H3 video-generation prompts.

You receive the CURRENT TEXT and a CHANGE REQUEST. Return the full revised text with the change applied, and nothing else: no preamble, no explanation, no Markdown fences, no quotation marks around the result.

Rules:
- Apply exactly what the change request asks for. Leave everything it does not touch as written; do not polish, shorten or restyle untouched passages.
- Propagate consequences. If a change affects other parts (wardrobe, action, camera, shot timing, dialogue, soundscape, music, subject definitions, retention lines), update those parts so the whole text stays consistent.
- Preserve the existing structure and syntax exactly: field names and their order (for example integrated_multimodal_description / overall_soundscape / non_diegetic_music, or subject_definitions / summary / retention_analysis / detailed_description / overall_soundscape / non_diegetic_music), [Shot N] markers, MM:SS.mmm timestamps, <Subject N> / <Picture N> / <Video N> / <Audio N> tags, (S1) speaker labels and <d>[Language] ...</d> dialogue markup.
- Keep every timestamp inside the clip duration and strictly increasing; only Shot 1 has no timestamp.
- Never renumber or invent reference tags. Never sanitise the user's wording, slang or explicit language.
- If the current text is a short idea rather than a structured prompt, return a revised idea of similar length and style; do not expand it into a full structured prompt.
- If the current text is empty, write the text the change request describes, in the format the guide below implies for this mode."""


def _openrouter_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") if isinstance(data, dict) else None
    message = (choices or [{}])[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def api_prompt_edit(request: web.Request) -> web.Response:
    """Revise the current prompt or idea with Gemini Flash through OpenRouter."""
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    text = str(body.get("text") or "")[:40000]
    instruction = str(body.get("instruction") or "").strip()[:4000]
    if not instruction:
        raise web.HTTPBadRequest(text="Describe the change you want.")
    api_key = base._openrouter_key({})
    if not api_key:
        raise web.HTTPBadRequest(text="OPENROUTER_API_KEY is not configured")

    mode = str(body.get("mode") or "t2v").lower()
    if mode not in ("t2v", "i2v", "r2v"):
        mode = "t2v"
    prompt_mode = "auto" if str(body.get("prompt_mode") or "").lower() == "auto" else "custom"
    guide = str(base._system_prompts().get(f"{mode}_auto") or "").strip()

    context = [
        f"MODE: {mode.upper()} · {'prompt idea (Auto Prompt expands it later)' if prompt_mode == 'auto' else 'final prompt sent to MiniMax H3'}",
    ]
    if body.get("duration"):
        context.append(f"CLIP DURATION: {body.get('duration')} seconds")
    if body.get("aspect_ratio"):
        context.append(f"ASPECT RATIO: {body.get('aspect_ratio')}")
    refs = body.get("refs") if isinstance(body.get("refs"), list) else []
    if refs:
        context.append("REFERENCE TAGS IN USE: " + ", ".join(str(x) for x in refs[:20]))
    user_message = "\n".join(context) + f"\n\nCHANGE REQUEST:\n{instruction}\n\nCURRENT TEXT:\n{text}"

    system = PROMPT_EDIT_SYSTEM
    if guide:
        system += "\n\n=== PROMPT-WRITING GUIDE FOR THIS MODE (format reference only; do not echo it) ===\n" + guide

    started = time.time()
    try:
        async with request.app["session"].post(
            OPENROUTER_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Title": "MMH3 Phone UI",
            },
            json={
                "model": PROMPT_EDIT_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.4,
            },
            timeout=ClientTimeout(total=90),
        ) as r:
            raw = await r.text()
            try:
                data = json.loads(raw)
            except ValueError:
                data = {}
            if not r.ok:
                detail = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else None
                raise web.HTTPBadGateway(text=f"OpenRouter error {r.status}: {detail or raw[:300]}")
    except asyncio.TimeoutError:
        raise web.HTTPGatewayTimeout(text="Gemini did not answer in time; try again.")
    except ClientError as exc:
        raise web.HTTPBadGateway(text=f"Could not reach OpenRouter: {exc}")
    revised = _openrouter_content(data)
    if not revised:
        raise web.HTTPBadGateway(text="Gemini returned an empty revision.")
    return web.json_response({
        "text": revised,
        "model": data.get("model") or PROMPT_EDIT_MODEL,
        "seconds": round(time.time() - started, 1),
    })


async def index(request: web.Request) -> web.Response:
    html = (base.STATIC / "index.html").read_text(encoding="utf-8")
    styles = [("library-v2.css", 2), ("features-v3.css", 1), ("features-v4.css", 1)]
    scripts = [("library-v2.js", 2), ("features-v3.js", 1), ("features-v4.js", 1)]
    links = "".join(
        f'  <link rel="stylesheet" href="/static/{name}?v={ver}">\n' for name, ver in styles if name not in html
    )
    html = html.replace("</head>", links + "</head>", 1)
    legacy = '<script src="/static/app.js?v=2"></script>'
    tags = "".join(f'\n<script src="/static/{name}?v={ver}"></script>' for name, ver in scripts if name not in html)
    html = html.replace(legacy, legacy + tags, 1)
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
    base.api_loras = api_loras
    base.api_sync_loras = api_sync_loras
    base.api_prompts_get = api_prompts_get

    app = base.make_app(comfy_url)
    app["lora_installs"] = {}
    app["lora_install_tasks"] = set()
    app["lora_lock"] = asyncio.Lock()
    app.router.add_get("/api/output-library", api_output_library_get)
    app.router.add_put("/api/output-library", api_output_library_put)
    app.router.add_post("/api/output-library/seen", api_output_library_seen)
    app.router.add_post("/api/loras/install/{vid}", api_install_lora)
    app.router.add_post("/api/prompt/edit", api_prompt_edit)
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
