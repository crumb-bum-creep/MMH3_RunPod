from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from mmh3.controls import load_controls, save_controls
from mmh3.generation_profiles import canonical_profile_id, public_profiles, resolve_profile

import server_core as base

import output_indexer

APP_VERSION = "1.0.0-mmH3-vnext"
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


def _generation_profile(payload: dict[str, Any], mode: str) -> str:
    try:
        return canonical_profile_id(mode, str(payload.get("generation_profile") or "") or None)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))


def _profile_spec(
    mode: str,
    profile: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return resolve_profile(mode, profile, overrides)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))


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
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = _profile_spec(mode, profile, overrides)
    turbo_id, turbo = _profile_turbo_node(graph)
    inp = turbo.setdefault("inputs", {})
    inp["lora_name"] = spec["turbo_lora"]
    inp["strength_model"] = spec["strength"]
    turbo.setdefault("_meta", {})["title"] = f"Generation Profile Turbo LoRA · {spec['label']}"

    for _, node in base.find_nodes(graph, "KSamplerSelect"):
        node.setdefault("inputs", {})["sampler_name"] = spec["sampler"]

    if mode == "r2v":
        for _, node in base.find_nodes(graph, "MiniMaxH3ReferenceToVideo"):
            node.setdefault("inputs", {})["ref_image_size"] = spec["ref_image_size"]

    guiders = base.find_nodes(graph, "BasicGuider")
    samplers = base.find_nodes(graph, "SamplerCustomAdvanced")
    if not guiders or not samplers:
        raise web.HTTPServiceUnavailable(text="workflow is missing profile sampler/guider nodes")

    if spec["schedule_type"] == "beta":
        beta_id, beta = _ensure_profile_node(
            graph, "BetaSamplingScheduler", "mmh3_profile_beta", "Generation Profile Beta Scheduler"
        )
        beta["inputs"] = {
            "steps": spec["steps"],
            "alpha": spec["beta_alpha"],
            "beta": spec["beta_beta"],
            "model": [turbo_id, 0],
        }

        sigma_source = [beta_id, 0]
        if spec["extend_enabled"]:
            extend_id, extend = _ensure_profile_node(
                graph,
                "ExtendIntermediateSigmas",
                "mmh3_profile_sigmas",
                "Generation Profile Sigma Extension",
            )
            extend["inputs"] = {
                "steps": spec["extend_steps"],
                "start_at_sigma": spec["extend_start"],
                "end_at_sigma": spec["extend_end"],
                "spacing": spec["extend_spacing"],
                "sigmas": [beta_id, 0],
            }
            sigma_source = [extend_id, 0]

        for _, node in guiders:
            node.setdefault("inputs", {})["model"] = [turbo_id, 0]
        for _, node in samplers:
            node.setdefault("inputs", {})["sigmas"] = sigma_source
    else:
        shift_id, shift = _ensure_profile_node(
            graph, "MiniMaxH3SigmaShift", "mmh3_profile_shift", "Generation Profile Sigma Shift"
        )
        shift["inputs"] = {
            "model": [turbo_id, 0],
            "shift_video": spec["shift_video"],
            "shift_audio": spec["shift_audio"],
        }
        shift.setdefault("_meta", {})["title"] = f"Generation Profile Sigma Shift · {spec['label']}"

        scheduler_id, scheduler = _ensure_profile_node(
            graph, "BasicScheduler", "mmh3_profile_scheduler", "Generation Profile Scheduler"
        )
        scheduler["inputs"] = {
            "model": [shift_id, 0],
            "scheduler": spec["scheduler"],
            "steps": spec["steps"],
            "denoise": 1.0,
        }
        scheduler.setdefault("_meta", {})["title"] = f"Generation Profile Scheduler · {spec['label']}"
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


def _sanitize_prefix_segment(value: str) -> str:
    import re
    value = re.sub(r"[^A-Za-z0-9._() +\-]+", "_", str(value or "")).strip(" .")
    return value[:120] or "_"


def _output_prefix(payload: dict[str, Any], record: dict[str, Any], profile: str, checkpoint: str) -> tuple[str, str]:
    controls = load_controls()
    template = str(payload.get("output_naming_template") or controls.get("output_naming_template") or "MMH3/{mode}")
    now = time.localtime()
    values = {
        "mode": str(record.get("mode") or "VIDEO").upper(),
        "prompt_mode": str(record.get("prompt_mode") or "").lower(),
        "profile": str(profile or ""),
        "checkpoint": str(checkpoint or ""),
        "seed": str(record.get("seed") or ""),
        "date": time.strftime("%Y-%m-%d", now),
        "time": time.strftime("%H-%M-%S", now),
    }
    try:
        rendered = template.format_map(values)
    except (KeyError, ValueError):
        rendered = "MMH3/{mode}".format_map(values)
        template = "MMH3/{mode}"
    parts = [_sanitize_prefix_segment(part) for part in rendered.replace("\\", "/").split("/") if part not in {"", ".", ".."}]
    return "/".join(parts[:8]) or f"MMH3/{values['mode']}", template


def patch_workflow_v3(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply MMH3 v3 queue-time features on top of the core workflow patcher."""
    graph, record = _base_patch_workflow(payload)
    mode = str(payload.get("mode") or "t2v").lower()
    prompt_mode = str(payload.get("prompt_mode") or "custom").lower()
    choice = _checkpoint_choice(payload)
    checkpoint = _checkpoint_filename(mode, choice)
    generation_profile = _generation_profile(payload, mode)

    unets = base.find_nodes(graph, "UNETLoader")
    if not unets:
        raise web.HTTPServiceUnavailable(text="workflow has no UNETLoader node")
    for _, node in unets:
        node.setdefault("inputs", {})["unet_name"] = checkpoint

    profile_spec = _patch_generation_profile(
        graph,
        mode,
        generation_profile,
        payload.get("generation_settings"),
    )

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
    record["generation_profile"] = generation_profile
    record["turbo_lora"] = profile_spec["turbo_lora"]
    record["turbo_steps"] = profile_spec["steps"]
    record["turbo_sampler"] = profile_spec["sampler"]
    record["turbo_scheduler"] = (
        profile_spec["scheduler"] if profile_spec["schedule_type"] == "basic" else "legacy_beta"
    )
    record["turbo_shift_video"] = profile_spec["shift_video"]
    record["turbo_shift_audio"] = profile_spec["shift_audio"]
    record["generation_settings"] = profile_spec
    record["ending_image"] = ending_image or None

    output_prefix, naming_template = _output_prefix(payload, record, generation_profile, choice)
    for _, node in base.find_nodes(graph, "VHS_VideoCombine"):
        node.setdefault("inputs", {})["filename_prefix"] = output_prefix
    record["output_prefix"] = output_prefix
    record["output_naming_template"] = naming_template
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


def _graph_model_requirements(graph: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (Comfy class, input name, selected filename) loader requirements."""
    mapping = {
        "CLIPLoader": "clip_name",
        "UNETLoader": "unet_name",
        "VAELoader": "vae_name",
        "LoraLoaderModelOnly": "lora_name",
    }
    required: list[tuple[str, str, str]] = []
    for class_type, input_name in mapping.items():
        for _, node in base.find_nodes(graph, class_type):
            filename = str((node.get("inputs") or {}).get(input_name) or "").strip()
            if filename:
                required.append((class_type, input_name, filename))
    return required


def _object_info_choices(info: dict[str, Any], class_type: str, input_name: str) -> set[str]:
    try:
        raw = info[class_type]["input"]["required"][input_name][0]
    except (KeyError, IndexError, TypeError):
        return set()
    return {str(x) for x in raw} if isinstance(raw, list) else set()


async def _ensure_graph_models_visible(app: web.Application, graph: dict[str, Any]) -> None:
    """Fail safely and ask the supervisor to refresh Comfy if its cache is stale."""
    required = _graph_model_requirements(graph)
    if not required:
        return
    try:
        async with app["session"].get(f"{app['comfy']}/object_info") as r:
            if not r.ok:
                raise RuntimeError(f"object_info HTTP {r.status}")
            info = await r.json()
    except Exception as exc:
        raise web.HTTPServiceUnavailable(text=f"Could not verify Comfy model visibility: {exc}")

    missing = [
        filename
        for class_type, input_name, filename in required
        if filename not in _object_info_choices(info, class_type, input_name)
    ]
    if not missing:
        return

    request_file = base.STATE_ROOT / "comfy_model_rescan.request"
    try:
        request_file.write_text(
            json.dumps({"requested_at": time.time(), "missing": sorted(set(missing))}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    names = ", ".join(sorted(set(missing))[:4])
    raise web.HTTPServiceUnavailable(
        text=f"Comfy's model index is stale ({names}). Automatic Comfy refresh requested; retry in a few seconds."
    )


async def api_generation_profiles(request: web.Request) -> web.Response:
    mode = str(request.query.get("mode") or "t2v").lower()
    try:
        payload = public_profiles(mode)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))

    for spec in payload["profiles"].values():
        path = _profile_path(spec["turbo_lora"])
        try:
            spec["ready"] = path.is_file() and path.stat().st_size >= 500 * 1024**2
        except OSError:
            spec["ready"] = False

    samplers: set[str] = set()
    schedulers: set[str] = set()
    try:
        async with request.app["session"].get(f"{request.app['comfy']}/object_info") as r:
            if r.ok:
                info = await r.json()
                samplers = _object_info_choices(info, "KSamplerSelect", "sampler_name")
                schedulers = _object_info_choices(info, "BasicScheduler", "scheduler")
    except Exception:
        pass

    if not samplers:
        samplers = {str(x.get("sampler") or "") for x in payload["profiles"].values()}
    if not schedulers:
        schedulers = {str(x.get("scheduler") or "") for x in payload["profiles"].values() if x.get("scheduler")}

    payload["samplers"] = sorted(x for x in samplers if x)
    payload["schedulers"] = sorted(x for x in schedulers if x)
    payload["schedule_types"] = ["basic", "beta"]
    payload["ref_image_sizes"] = ["max", "match"]
    return web.json_response(payload)


async def api_runtime_controls_get(request: web.Request) -> web.Response:
    return web.json_response(load_controls())


async def api_runtime_controls_put(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="runtime controls must be an object")
    return web.json_response(save_controls(body))


async def api_generate(request: web.Request) -> web.Response:
    """Queue a generation without performing memory cleanup inside the request."""
    app = request.app
    payload = await request.json()
    controls = load_controls()
    memory_protection = bool(controls.get("memory_protection", True))

    requested_mode = str(payload.get("mode") or "t2v").lower()
    requested_family = "ref2v" if requested_mode == "r2v" else "fl2v"
    checkpoint_choice = _checkpoint_choice(payload)
    generation_profile = _generation_profile(payload, requested_mode)

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

    if memory_protection and active_families and requested_family not in active_families:
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

    profile_spec = _profile_spec(
        requested_mode,
        generation_profile,
        payload.get("generation_settings"),
    )
    turbo_path = _profile_path(profile_spec["turbo_lora"])
    try:
        turbo_ready = turbo_path.is_file() and turbo_path.stat().st_size >= 500 * 1024**2
    except OSError:
        turbo_ready = False
    if not turbo_ready:
        raise web.HTTPServiceUnavailable(
            text=f"{profile_spec['label']} is not ready ({profile_spec['turbo_lora']}). "
                 "Choose a ready profile or wait/install the required Turbo file."
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
    if memory_protection and memory_is_fresh and fraction >= critical_fraction:
        free_gib = free_bytes / (1024 ** 3)
        raise web.HTTPServiceUnavailable(
            text=f"MMH3 host memory is critically high ({fraction:.0%} used, {free_gib:.1f} GiB free). "
                 "The prompt was not queued and no memory cleanup was triggered by this submission."
        )

    graph, record = base.patch_workflow(payload)
    await _ensure_graph_models_visible(app, graph)

    # Force Comfy to validate the actual video output branch. Without explicit
    # targets a disconnected/invalid video branch can coexist with a valid
    # display node, producing an apparent instant "success" with no video.
    video_targets = [nid for nid, _ in base.find_nodes(graph, "VHS_VideoCombine")]
    if not video_targets:
        raise web.HTTPServiceUnavailable(text="workflow has no VHS_VideoCombine output target")

    plan = base._make_plan(graph)
    async with app["session"].post(
        f"{app['comfy']}/prompt",
        json={
            "client_id": app["client_id"],
            "prompt": graph,
            "partial_execution_targets": video_targets,
        },
    ) as r:
        body = await r.text()
        if not r.ok:
            raise web.HTTPBadGateway(text=body)
        reply = json.loads(body)

    node_errors = reply.get("node_errors") or {}
    if node_errors:
        raise web.HTTPBadGateway(
            text="Comfy rejected the video output branch: " + json.dumps(node_errors, separators=(",", ":"))
        )
    if not reply.get("prompt_id"):
        raise web.HTTPBadGateway(text="Comfy accepted the request without returning a prompt_id")

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
    app.router.add_get("/api/generation-profiles", api_generation_profiles)
    app.router.add_get("/api/runtime-controls", api_runtime_controls_get)
    app.router.add_put("/api/runtime-controls", api_runtime_controls_put)
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
