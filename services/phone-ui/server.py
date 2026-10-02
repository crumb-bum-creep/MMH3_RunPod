from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
import random
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
import yaml

from mmh3 import comfy
from mmh3.common import (
    COMFY_PERSIST,
    CONFIG_ROOT,
    DATA_ROOT,
    IMAGE_ROOT,
    STATE_ROOT,
    dump_json,
    load_yaml,
)
from mmh3.hardware import detect
from mmh3.loras import sync_loras

APP_VERSION = "0.6.0-mmH3-library-ux"
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
WORKFLOW_DIR = Path(os.environ.get("MMH3_WORKFLOW_DIR", IMAGE_ROOT / "workflows" / "api"))
INPUT_DIR = COMFY_PERSIST / "input"
OUTPUT_DIR = COMFY_PERSIST / "output"
TEMPLATE_FILE = DATA_ROOT / "templates.json"
RECORD_FILE = DATA_ROOT / "generation_records.json"
OUTPUT_META_FILE = DATA_ROOT / "output_meta.json"
ASSET_META_FILE = DATA_ROOT / "assets.json"
UI_STATE_FILE = DATA_ROOT / "ui_state.json"
THUMB_DIR = DATA_ROOT / "input_thumbs"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
MEDIA_EXT = IMAGE_EXT | VIDEO_EXT | AUDIO_EXT

WORKFLOWS = {
    ("t2v", "auto"): "t2v_auto.json",
    ("t2v", "custom"): "t2v_custom.json",
    ("i2v", "auto"): "i2v_auto.json",
    ("i2v", "custom"): "i2v_custom.json",
    ("r2v", "auto"): "r2v_auto.json",
    ("r2v", "custom"): "r2v_custom.json",
}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, value: Any) -> None:
    dump_json(path, value)


def iter_nodes(graph: dict[str, Any]):
    for nid, node in graph.items():
        if isinstance(node, dict) and node.get("class_type"):
            yield str(nid), node


def find_nodes(graph: dict[str, Any], class_type: str):
    return [(nid, n) for nid, n in iter_nodes(graph) if n.get("class_type") == class_type]


def find_title(graph: dict[str, Any], title: str):
    for nid, node in iter_nodes(graph):
        if str((node.get("_meta") or {}).get("title", "")).strip() == title:
            return nid, node
    return None, None


def random_video_seed() -> int:
    return random.randint(0, 1125899906842624)


def random_openrouter_seed() -> int:
    return random.randint(0, 2147483647)


def _system_prompts() -> dict[str, str]:
    cfg = load_yaml(CONFIG_ROOT / "system_prompts.yaml", {}) or {}
    return (cfg.get("prompts") or {})


def _openrouter_key(payload: dict[str, Any]) -> str:
    return str(payload.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY") or "").strip()


def _safe_name(raw: str) -> str:
    name = Path(raw or "upload.bin").name
    name = re.sub(r"[^A-Za-z0-9._() +\-]+", "_", name).strip(" .")
    return name[:180] or "upload.bin"


def _safe_rel(raw: str) -> str:
    p = Path(raw)
    if p.is_absolute() or ".." in p.parts:
        raise web.HTTPBadRequest(text="invalid path")
    return p.as_posix()


def _inside(root: Path, rel: str) -> Path:
    rel = _safe_rel(rel)
    root = root.resolve()
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise web.HTTPBadRequest(text="invalid path")
    return p


def _media_kind(path: Path | str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXT:
        return "image"
    if suffix in VIDEO_EXT:
        return "video"
    if suffix in AUDIO_EXT:
        return "audio"
    return "other"


def _asset_meta() -> dict[str, Any]:
    value = _load_json(ASSET_META_FILE, {})
    return value if isinstance(value, dict) else {}


def _asset_item(path: Path, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    rel = path.relative_to(INPUT_DIR).as_posix()
    st = path.stat()
    entry = ((meta or {}).get(rel) or {}) if isinstance(meta, dict) else {}
    nickname = str(entry.get("nickname") or "").strip()
    kind = _media_kind(path)
    return {
        "file": rel,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "kind": kind,
        "nickname": nickname,
        "display_name": nickname or path.name,
        "has_nickname": bool(nickname),
        "thumb": f"/media/input-thumb/{rel}" if kind == "image" else None,
    }


def _set_power_loras(graph: dict[str, Any], loras: list[dict[str, Any]]) -> None:
    for _, node in find_nodes(graph, "Power Lora Loader (rgthree)"):
        inputs = node.setdefault("inputs", {})
        for key in list(inputs):
            if re.fullmatch(r"lora_\d+", key):
                inputs.pop(key, None)
        for i, item in enumerate(loras or [], start=1):
            filename = str(item.get("filename") or item.get("lora") or "").strip()
            if not filename:
                continue
            try:
                strength = float(item.get("strength", item.get("recommended_strength", 1.0)))
            except (TypeError, ValueError):
                strength = 1.0
            inputs[f"lora_{i}"] = {"on": True, "lora": filename, "strength": strength}


def _reference_json(refs: list[dict[str, Any]]) -> str:
    out = []
    counts = {"image": 0, "video": 0, "audio": 0}
    caps = {"image": 9, "video": 3, "audio": 3}
    for ref in refs or []:
        kind = str(ref.get("kind") or "").lower()
        filename = str(ref.get("file") or "").strip()
        if kind not in caps or not filename:
            continue
        if counts[kind] >= caps[kind]:
            raise web.HTTPBadRequest(text=f"too many {kind} references")
        counts[kind] += 1
        item = {"kind": kind, "file": filename}
        if kind == "video":
            item["use_soundtrack"] = bool(ref.get("use_soundtrack", True))
        if ref.get("crop") is not None:
            item["crop"] = ref["crop"]
        if ref.get("trim") is not None:
            item["trim"] = ref["trim"]
        out.append(item)
    return json.dumps({"references": out}, separators=(",", ":"))


def patch_workflow(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    mode = str(payload.get("mode") or "t2v").lower()
    prompt_mode = str(payload.get("prompt_mode") or "custom").lower()
    key = (mode, prompt_mode)
    if key not in WORKFLOWS:
        raise web.HTTPBadRequest(text="unknown workflow mode")
    path = WORKFLOW_DIR / WORKFLOWS[key]
    if not path.exists():
        raise web.HTTPServiceUnavailable(text=f"workflow missing: {path.name}")
    graph = json.loads(path.read_text(encoding="utf-8"))

    aspect = str(payload.get("aspect_ratio") or "9:16 (Portrait Widescreen)")
    try:
        mp = float(payload.get("megapixels", 0.7))
        duration = float(payload.get("duration", 5))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="invalid duration or megapixels")
    mp = max(0.2, min(1.5, mp))
    duration = max(1.0, min(30.0, duration))

    seed = random_video_seed() if payload.get("randomize_seed", True) else int(payload.get("seed", 1))
    for _, node in find_nodes(graph, "ResolutionSelector"):
        node["inputs"]["aspect_ratio"] = aspect
        node["inputs"]["megapixels"] = mp
    for _, node in find_nodes(graph, "PrimitiveFloat"):
        if "duration" in str((node.get("_meta") or {}).get("title", "")).lower():
            node["inputs"]["value"] = duration
    for _, node in find_nodes(graph, "RandomNoise"):
        node["inputs"]["noise_seed"] = seed
    for _, node in find_nodes(graph, "VHS_VideoCombine"):
        node["inputs"]["filename_prefix"] = f"MMH3/{mode.upper()}"

    _set_power_loras(graph, payload.get("loras") or [])

    prompts = _system_prompts()
    user_text = str(payload.get("prompt_idea") if prompt_mode == "auto" else payload.get("prompt") or "").strip()
    if not user_text:
        user_text = str(payload.get("prompt") or payload.get("prompt_idea") or "").strip()

    if mode in ("t2v", "i2v"):
        title = "Your Prompt Idea Here" if prompt_mode == "auto" else "Your Prompt Here"
        _, prompt_node = find_title(graph, title)
        if prompt_node is not None:
            prompt_node["inputs"]["value"] = user_text

        if mode == "i2v":
            image = str(payload.get("starting_image") or "").strip()
            if not image:
                raise web.HTTPBadRequest(text="I2V requires a starting image")
            nodes = find_nodes(graph, "LoadImage")
            if not nodes:
                raise web.HTTPServiceUnavailable(text="I2V workflow has no LoadImage node")
            nodes[0][1]["inputs"]["image"] = image

        if prompt_mode == "auto":
            api_key = _openrouter_key(payload)
            if not api_key:
                raise web.HTTPBadRequest(text="OPENROUTER_API_KEY is not configured")
            _, key_node = find_title(graph, "OpenRouter API Key")
            if key_node is not None:
                inp = key_node.setdefault("inputs", {})
                if "string" in inp:
                    inp["string"] = api_key
                else:
                    inp["value"] = api_key
            for _, node in find_nodes(graph, "OpenRouterNode"):
                inp = node.setdefault("inputs", {})
                inp["system_prompt"] = prompts.get(f"{mode}_auto", "")
                inp["aspect_ratio"] = "auto"
                inp["image_resolution"] = "1K"
                inp["seed"] = random_openrouter_seed()

    elif mode == "r2v":
        refs = payload.get("refs") or []
        ref_json = _reference_json(refs)
        nodes = find_nodes(graph, "MiniMaxH3ReferencePack")
        if not nodes:
            raise web.HTTPServiceUnavailable(text="R2V workflow has no ReferencePack node")
        _, pack = nodes[0]
        inp = pack.setdefault("inputs", {})
        inp["direction"] = user_text
        inp["references_json"] = ref_json
        if prompt_mode == "auto":
            api_key = _openrouter_key(payload)
            if not api_key:
                raise web.HTTPBadRequest(text="OPENROUTER_API_KEY is not configured")
            inp["prompt_provider"] = "openrouter"
            inp["job_type"] = "auto"
            inp["system_prompt"] = prompts.get("r2v_auto", "")
            # MiniMaxH3ReferencePack's IS_CHANGED cache key includes
            # local_model_slug even when OpenRouter is selected, while its OpenRouter
            # execution path ignores that field. Use it as a per-job cache nonce so
            # identical R2V ideas/references still make a fresh prompt request.
            inp["local_model_slug"] = f"mmh3-cache-{random_openrouter_seed()}"
            _, key_node = find_title(graph, "OpenRouter API Key")
            if key_node is not None:
                key_node.setdefault("inputs", {})["value"] = api_key
        else:
            inp["prompt_provider"] = "none"
            inp["job_type"] = "standard"
            inp["system_prompt"] = ""
            inp["openrouter_api_key"] = ""

    record = {
        "mode": mode,
        "model_family": "ref2v" if mode == "r2v" else "fl2v",
        "prompt_mode": prompt_mode,
        "prompt": str(payload.get("prompt") or ""),
        "prompt_idea": str(payload.get("prompt_idea") or ""),
        "aspect_ratio": aspect,
        "megapixels": mp,
        "duration": duration,
        "seed": seed,
        "randomize_seed": bool(payload.get("randomize_seed", True)),
        "starting_image": payload.get("starting_image"),
        "refs": payload.get("refs") or [],
        "loras": payload.get("loras") or [],
        "queued_at": time.time(),
    }
    return graph, record


def _node_weight(node: dict[str, Any]) -> float:
    ct = str(node.get("class_type") or "")
    return {
        "SamplerCustomAdvanced": 60.0,
        "MiniMaxH3ImageToVideo": 7.0,
        "MiniMaxH3ReferenceToVideo": 7.0,
        "MiniMaxH3ReferencePack": 8.0,
        "OpenRouterNode": 7.0,
        "VAEDecode": 5.0,
        "VAEDecodeAudio": 5.0,
        "VHS_VideoCombine": 5.0,
        "UNETLoader": 2.0,
        "CLIPLoader": 2.0,
        "VAELoader": 2.0,
        "Power Lora Loader (rgthree)": 2.0,
        "LoraLoaderModelOnly": 2.0,
    }.get(ct, 0.6)


def _friendly(node: dict[str, Any]) -> str:
    ct = str(node.get("class_type") or "")
    title = str((node.get("_meta") or {}).get("title") or ct or "Processing")
    names = {
        "OpenRouterNode": "Writing auto prompt",
        "MiniMaxH3ReferencePack": "Preparing references / prompt",
        "UNETLoader": "Loading diffusion model",
        "CLIPLoader": "Loading text encoder",
        "Power Lora Loader (rgthree)": "Applying selected LoRAs",
        "LoraLoaderModelOnly": "Applying Turbo LoRA",
        "LoadImage": "Loading image input",
        "MiniMaxH3ImageToVideo": "Encoding prompt and inputs",
        "MiniMaxH3ReferenceToVideo": "Encoding references",
        "SamplerCustomAdvanced": "Generating video",
        "VAEDecode": "Decoding video",
        "VAEDecodeAudio": "Decoding audio",
        "VHS_VideoCombine": "Muxing video + audio",
    }
    if ct == "VAELoader":
        return "Loading VAE"
    return names.get(ct, title)


def _make_plan(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = {}
    total = 0.0
    for nid, node in iter_nodes(graph):
        weight = _node_weight(node)
        nodes[nid] = {
            "weight": weight,
            "name": _friendly(node),
            "title": str((node.get("_meta") or {}).get("title") or node.get("class_type") or nid),
            "state": "pending",
            "value": 0.0,
            "max": 1.0,
        }
        total += weight
    return {"nodes": nodes, "total_weight": max(total, 1.0), "current_node": None}


def _snapshot(app: web.Application, prompt_id: str) -> dict[str, Any]:
    plan = app["plans"].get(prompt_id) or {"nodes": {}, "total_weight": 1.0, "current_node": None}
    meta = app["progress"].get(prompt_id) or {"status": "queued"}
    done = 0.0
    for n in plan["nodes"].values():
        frac = 0.0
        if n["state"] in ("finished", "cached"):
            frac = 1.0
        elif n["state"] == "running":
            mx = float(n.get("max") or 1.0)
            frac = max(0.0, min(1.0, float(n.get("value") or 0.0) / mx))
        done += float(n["weight"]) * frac
    total = max(0.0, min(100.0, 100 * done / float(plan["total_weight"])))
    current = plan["nodes"].get(str(plan.get("current_node"))) if plan.get("current_node") is not None else None
    proc = 0.0
    if current and current["state"] == "running":
        proc = 100 * float(current.get("value") or 0.0) / float(current.get("max") or 1.0)
    status = meta.get("status", "queued")
    if status == "completed":
        total = proc = 100.0
    name = meta.get("process_name") if status in ("completed", "failed", "cancelled") else None
    return {
        "prompt_id": prompt_id,
        "status": status,
        "process_name": name or (current or {}).get("name") or ("Waiting in queue" if status == "queued" else "Starting"),
        "node_title": (current or {}).get("title", ""),
        "process_percent": round(max(0.0, min(100.0, proc)), 1),
        "total_percent": round(total, 1),
        "updated_at": meta.get("updated_at", time.time()),
        "ws_connected": bool(app.get("ws_connected")),
    }


def _touch(app: web.Application, pid: str, **patch: Any) -> None:
    item = app["progress"].setdefault(pid, {"status": "queued"})
    item.update(patch)
    item["updated_at"] = time.time()


def _finish_previous(app: web.Application, pid: str, new_node: Any) -> None:
    plan = app["plans"].get(pid)
    if not plan:
        return
    old = plan.get("current_node")
    if old is not None and str(old) != str(new_node):
        n = plan["nodes"].get(str(old))
        if n and n["state"] == "running":
            n["state"] = "finished"
            n["value"] = n.get("max") or 1.0


def _mark(app: web.Application, pid: str, nid: Any, state: str | None = None, value: Any = None, maximum: Any = None) -> None:
    plan = app["plans"].get(pid)
    if not plan or nid is None:
        return
    n = plan["nodes"].get(str(nid))
    if not n:
        return
    if state is not None:
        n["state"] = state
    if value is not None:
        n["value"] = float(value)
    if maximum is not None:
        n["max"] = float(maximum) or 1.0
    if state == "running":
        plan["current_node"] = str(nid)


def _extract_generated_prompt(outputs: Any) -> str:
    candidates: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, str):
            text = x.strip()
            if text:
                candidates.append(text)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(outputs)

    markers = (
        "integrated_multimodal_description:",
        "subject_definitions:",
        "detailed_description:",
        "overall_soundscape:",
    )
    scored: list[tuple[int, int, str]] = []
    for text in candidates:
        score = sum(1 for m in markers if m in text)
        if score:
            scored.append((score, len(text), text))

    if not scored:
        return ""
    scored.sort(reverse=True)
    return scored[0][2]


async def _finalize(app: web.Application, pid: str) -> None:
    await asyncio.sleep(1)
    try:
        async with app["session"].get(f"{app['comfy']}/history/{pid}") as r:
            if not r.ok:
                return
            hist = await r.json()
    except Exception:
        return
    item = hist.get(pid, hist) if isinstance(hist, dict) else {}
    files: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            fn = x.get("filename")
            if isinstance(fn, str) and fn.lower().endswith(".mp4"):
                sub = str(x.get("subfolder") or "").strip("/")
                files.append(f"{sub}/{fn}".strip("/"))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(item.get("outputs") if isinstance(item, dict) else item)
    output_meta = _load_json(OUTPUT_META_FILE, {})
    record = dict(app["records"].get(pid, {}))
    if record.get("prompt_mode") == "auto":
        actual = _extract_generated_prompt(item.get("outputs") if isinstance(item, dict) else item)
        if actual:
            record["actual_prompt"] = actual
    elif record.get("prompt"):
        record["actual_prompt"] = record.get("prompt")

    for rel in dict.fromkeys(files):
        output_meta[rel] = {**record, "prompt_id": pid, "completed_at": time.time()}
        p = _inside(OUTPUT_DIR, rel)
        if p.exists():
            try:
                p.with_suffix(p.suffix + ".h3.json").write_text(
                    json.dumps(output_meta[rel], indent=2), encoding="utf-8"
                )
            except OSError:
                pass
    _save_json(OUTPUT_META_FILE, output_meta)


async def _handle_event(app: web.Application, message: dict[str, Any]) -> None:
    typ = message.get("type")
    data = message.get("data") or {}
    pid = str(data.get("prompt_id") or "")
    if not pid:
        return
    if typ == "execution_start":
        _touch(app, pid, status="running", process_name="Starting workflow")
    elif typ == "execution_cached":
        for nid in data.get("nodes") or []:
            _mark(app, pid, nid, "cached", 1, 1)
        _touch(app, pid, status="running")
    elif typ == "executing":
        nid = data.get("node")
        if nid is None:
            _finish_previous(app, pid, None)
            _touch(app, pid, status="completed", process_name="Complete")
            asyncio.create_task(_finalize(app, pid))
        else:
            _finish_previous(app, pid, nid)
            _mark(app, pid, nid, "running", 0, 1)
            _touch(app, pid, status="running")
    elif typ == "progress":
        nid = data.get("node")
        _finish_previous(app, pid, nid)
        _mark(app, pid, nid, "running", data.get("value", 0), data.get("max", 1))
        _touch(app, pid, status="running")
    elif typ == "progress_state":
        plan = app["plans"].get(pid)
        if plan:
            for nid, state in (data.get("nodes") or {}).items():
                _mark(app, pid, nid, str(state.get("state") or "pending"), state.get("value", 0), state.get("max", 1))
        _touch(app, pid, status="running")
    elif typ == "executed":
        _mark(app, pid, data.get("node"), "finished", 1, 1)
    elif typ == "execution_success":
        _touch(app, pid, status="completed", process_name="Complete")
        asyncio.create_task(_finalize(app, pid))
    elif typ == "execution_error":
        _touch(app, pid, status="failed", process_name="Generation failed")
    elif typ == "execution_interrupted":
        _touch(app, pid, status="cancelled", process_name="Cancelled")


async def _ws_loop(app: web.Application) -> None:
    base = app["comfy"]
    ws_base = "wss://" + base[8:] if base.startswith("https://") else "ws://" + base[7:]
    url = f"{ws_base}/ws?clientId={app['client_id']}"
    while True:
        try:
            async with app["ws_session"].ws_connect(url, heartbeat=20) as ws:
                app["ws_connected"] = True
                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        try:
                            await _handle_event(app, json.loads(msg.data))
                        except Exception:
                            pass
                    elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSED, WSMsgType.CLOSING):
                        break
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        app["ws_connected"] = False
        await asyncio.sleep(2)


async def api_info(request: web.Request) -> web.Response:
    h = detect()
    mem = _load_json(STATE_ROOT / "memory.json", {})
    provisioning = _load_json(STATE_ROOT / "provisioning.json", {
        "status": "unknown", "stage": "unknown", "core_ready": False
    })
    return web.json_response({
        "version": APP_VERSION,
        "hardware": h.to_dict(),
        "memory": mem,
        "provisioning": provisioning,
        "openrouter_configured": bool(os.environ.get("OPENROUTER_API_KEY")),
        "hf_configured": bool(os.environ.get("HF_TOKEN")),
        "civitai_configured": bool(os.environ.get("CIVITAI_TOKEN")),
    })


async def api_generate(request: web.Request) -> web.Response:
    app = request.app
    payload = await request.json()

    requested_mode = str(payload.get("mode") or "t2v").lower()
    requested_family = "ref2v" if requested_mode == "r2v" else "fl2v"

    # The telemetry showed the dangerous RAM jump when Ref2V and FL2V families
    # accumulate in the same warm Comfy process. Same-family queueing stays fully
    # supported, but do not stack the other family behind it with no cleanup window.
    try:
        async with app["session"].get(f"{app['comfy']}/queue") as r:
            active_queue = await r.json()
    except Exception:
        active_queue = {"queue_running": [], "queue_pending": []}

    active_families = set()
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

    provisioning = _load_json(STATE_ROOT / "provisioning.json", {})
    if provisioning and not bool(provisioning.get("core_ready", False)):
        status = str(provisioning.get("status") or "pending")
        stage = str(provisioning.get("stage") or "models")
        message = str(provisioning.get("message") or "Core MiniMax H3 models are still provisioning")
        raise web.HTTPServiceUnavailable(
            text=f"{message} (status={status}, stage={stage}). "
                 "You can keep using the UI; generation will unlock automatically when core models are ready."
        )

    memory = _load_json(STATE_ROOT / "memory.json", {})
    try:
        fraction = float(memory.get("fraction") or 0.0)
        cleanup_fraction = float(memory.get("cleanup_fraction") or 0.84)
        resume_fraction = float(memory.get("resume_fraction") or 0.76)
        free_bytes = float(memory.get("free_bytes") or 0.0)
        min_free = float(memory.get("min_free_headroom_bytes") or 0.0)
        resume_free = float(memory.get("resume_free_headroom_bytes") or min_free)
    except (TypeError, ValueError):
        fraction, cleanup_fraction, resume_fraction = 0.0, 0.84, 0.76
        free_bytes = min_free = resume_free = 0.0

    pressure = fraction >= cleanup_fraction or (min_free > 0 and free_bytes < min_free)
    if pressure:
        if comfy.queue_idle():
            comfy.free_memory(True, True)
            deadline = time.time() + 45
            while time.time() < deadline:
                await asyncio.sleep(1)
                memory = _load_json(STATE_ROOT / "memory.json", {})
                try:
                    fraction = float(memory.get("fraction") or 0.0)
                    free_bytes = float(memory.get("free_bytes") or 0.0)
                except (TypeError, ValueError):
                    fraction, free_bytes = 0.0, 0.0
                if fraction <= resume_fraction and (resume_free <= 0 or free_bytes >= resume_free):
                    break

        pressure = fraction >= cleanup_fraction or (min_free > 0 and free_bytes < min_free)
        if pressure:
            free_gib = free_bytes / (1024 ** 3)
            raise web.HTTPServiceUnavailable(
                text=f"MMH3 is protecting pod memory ({fraction:.0%} used, {free_gib:.1f} GiB free). "
                     "Wait for model/cache cleanup to finish, then queue again."
            )

    graph, record = patch_workflow(payload)
    plan = _make_plan(graph)
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
    _touch(app, pid, status="queued", process_name="Waiting in queue")
    _save_json(RECORD_FILE, app["records"])
    return web.json_response({"prompt_id": pid, "seed": record["seed"]})


async def api_queue(request: web.Request) -> web.Response:
    app = request.app
    try:
        async with app["session"].get(f"{app['comfy']}/queue") as r:
            q = await r.json()
    except Exception:
        q = {"queue_running": [], "queue_pending": []}
    items = []
    running_rows = q.get("queue_running") or []
    for running_index, row in enumerate(running_rows, start=1):
        pid = str(row[1]) if isinstance(row, list) and len(row) > 1 else ""
        items.append({
            "prompt_id": pid,
            "status": "running",
            "position": 0,
            "label": "RUNNING" if len(running_rows) == 1 else f"RUNNING {running_index}",
            "record": app["records"].get(pid, {}),
        })
    for position, row in enumerate(q.get("queue_pending") or [], start=1):
        pid = str(row[1]) if isinstance(row, list) and len(row) > 1 else ""
        items.append({
            "prompt_id": pid,
            "status": "queued",
            "position": position,
            "label": f"NEXT #{position}",
            "record": app["records"].get(pid, {}),
        })
    return web.json_response({"items": items})


async def api_progress(request: web.Request) -> web.Response:
    app = request.app
    pid = str(request.query.get("prompt_id") or "").strip()
    if pid:
        return web.json_response(_snapshot(app, pid))
    candidates = []
    for p, m in app["progress"].items():
        rank = 0 if m.get("status") == "running" else 1 if m.get("status") == "queued" else 2
        candidates.append((rank, -float(m.get("updated_at") or 0), p))
    if not candidates:
        return web.json_response({
            "status": "idle",
            "process_name": "Ready",
            "process_percent": 0,
            "total_percent": 0,
            "ws_connected": app.get("ws_connected", False),
        })
    candidates.sort()
    return web.json_response(_snapshot(app, candidates[0][2]))


async def api_cancel(request: web.Request) -> web.Response:
    app = request.app
    pid = str(request.match_info["pid"])
    running = False
    try:
        async with app["session"].get(f"{app['comfy']}/queue") as r:
            q = await r.json()
        running = any(isinstance(row, list) and len(row) > 1 and str(row[1]) == pid for row in (q.get("queue_running") or []))
        async with app["session"].post(f"{app['comfy']}/queue", json={"delete": [pid]}) as r:
            await r.read()
        if running:
            async with app["session"].post(f"{app['comfy']}/interrupt") as r:
                await r.read()
    except Exception:
        pass
    _touch(app, pid, status="cancelled", process_name="Cancelled")
    return web.json_response({"ok": True, "interrupted_running_job": running})


async def api_upload(request: web.Request) -> web.Response:
    reader = await request.multipart()
    part = await reader.next()
    if part is None or not part.filename:
        raise web.HTTPBadRequest(text="missing file")
    original = _safe_name(part.filename)
    stem = Path(original).stem
    suffix = Path(original).suffix
    name = f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"
    dest = INPUT_DIR / name
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        while True:
            chunk = await part.read_chunk(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return web.json_response(_asset_item(dest, _asset_meta()))


async def api_inputs(request: web.Request) -> web.Response:
    kind = str(request.query.get("kind") or "all").lower()
    exts = IMAGE_EXT if kind == "image" else VIDEO_EXT if kind == "video" else AUDIO_EXT if kind == "audio" else MEDIA_EXT
    meta = _asset_meta()
    items = []
    if INPUT_DIR.exists():
        for path in INPUT_DIR.rglob("*"):
            if path.is_file() and path.suffix.lower() in exts:
                items.append(_asset_item(path, meta))
    sort = str(request.query.get("sort") or "recent").lower()
    if sort == "alpha":
        items.sort(key=lambda item: (str(item.get("display_name") or "").casefold(), str(item["file"]).casefold()))
    else:
        items.sort(key=lambda item: item["mtime"], reverse=True)
    return web.json_response({"items": items[:500]})


async def api_asset_meta_put(request: web.Request) -> web.Response:
    body = await request.json()
    rel = str(body.get("file") or "").strip()
    if not rel:
        raise web.HTTPBadRequest(text="asset file required")
    path = _inside(INPUT_DIR, rel)
    if not path.is_file() or path.suffix.lower() not in MEDIA_EXT:
        raise web.HTTPNotFound(text="asset not found")
    nickname = str(body.get("nickname") or "").strip()[:120]
    meta = _asset_meta()
    safe_rel = _safe_rel(rel)
    entry = dict(meta.get(safe_rel) or {})
    if nickname:
        entry["nickname"] = nickname
        meta[safe_rel] = entry
    else:
        entry.pop("nickname", None)
        if entry:
            meta[safe_rel] = entry
        else:
            meta.pop(safe_rel, None)
    _save_json(ASSET_META_FILE, meta)
    return web.json_response({"ok": True, "asset": _asset_item(path, meta)})


async def serve_input(request: web.Request) -> web.StreamResponse:
    path = _inside(INPUT_DIR, request.match_info["path"])
    if not path.is_file() or path.suffix.lower() not in MEDIA_EXT:
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def serve_input_thumb(request: web.Request) -> web.StreamResponse:
    path = _inside(INPUT_DIR, request.match_info["path"])
    if not path.is_file() or path.suffix.lower() not in IMAGE_EXT:
        raise web.HTTPNotFound()
    st = path.stat()
    rel = path.relative_to(INPUT_DIR).as_posix()
    key = hashlib.sha1(f"{rel}:{st.st_mtime_ns}:{st.st_size}".encode("utf-8")).hexdigest()
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    thumb = THUMB_DIR / f"{key}.jpg"
    if not thumb.exists():
        try:
            from PIL import Image, ImageOps
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((180, 180), Image.Resampling.LANCZOS)
                if image.mode != "RGB":
                    if "A" in image.getbands():
                        bg = Image.new("RGB", image.size, (14, 18, 27))
                        bg.paste(image, mask=image.getchannel("A"))
                        image = bg
                    else:
                        image = image.convert("RGB")
                image.save(thumb, "JPEG", quality=82, optimize=True)
        except Exception as exc:
            raise web.HTTPUnsupportedMediaType(text=f"thumbnail failed: {exc}")
    response = web.FileResponse(thumb)
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


async def api_ui_state_get(request: web.Request) -> web.Response:
    value = _load_json(UI_STATE_FILE, {"active": {"mode": "t2v", "prompt_mode": "auto"}, "profiles": {}, "updated_at": 0})
    if not isinstance(value, dict):
        value = {"active": {"mode": "t2v", "prompt_mode": "auto"}, "profiles": {}, "updated_at": 0}
    return web.json_response(value)


async def api_ui_state_put(request: web.Request) -> web.Response:
    body = await request.json()
    profiles = body.get("profiles")
    active = body.get("active")
    if not isinstance(profiles, dict) or not isinstance(active, dict):
        raise web.HTTPBadRequest(text="ui state requires active + profiles objects")
    allowed_modes = {"t2v", "i2v", "r2v"}
    allowed_prompt_modes = {"auto", "custom"}
    mode = str(active.get("mode") or "t2v").lower()
    prompt_mode = str(active.get("prompt_mode") or "auto").lower()
    if mode not in allowed_modes or prompt_mode not in allowed_prompt_modes:
        raise web.HTTPBadRequest(text="invalid active UI profile")
    clean_profiles = {}
    for key, value in profiles.items():
        if key not in {f"{m}:{p}" for m in allowed_modes for p in allowed_prompt_modes}:
            continue
        if isinstance(value, dict):
            clean_profiles[key] = value
    value = {"active": {"mode": mode, "prompt_mode": prompt_mode}, "profiles": clean_profiles, "updated_at": time.time()}
    _save_json(UI_STATE_FILE, value)
    return web.json_response({"ok": True, **value})



def _has_audio(path: Path) -> bool:
    # H3/VHS output filenames conventionally include "audio" once video+audio
    # have been muxed. Use that as the zero-cost fast path; only probe ambiguous
    # legacy/foreign MP4s.
    if "audio" in path.name.lower():
        return True
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return bool(p.stdout.strip())
    except Exception:
        return False


async def api_outputs(request: web.Request) -> web.Response:
    meta = _load_json(OUTPUT_META_FILE, {})
    items = []
    if OUTPUT_DIR.exists():
        for p in OUTPUT_DIR.rglob("*.mp4"):
            rel = p.relative_to(OUTPUT_DIR).as_posix()
            if "MMH3Director" in p.parts:
                continue
            if not _has_audio(p):
                continue
            st = p.stat()
            items.append({
                "file": rel,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "metadata": meta.get(rel) or _load_json(p.with_suffix(p.suffix + ".h3.json"), {}),
            })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return web.json_response({"items": items[:250]})


async def api_delete_output(request: web.Request) -> web.Response:
    rel = request.match_info["path"]
    p = _inside(OUTPUT_DIR, rel)
    if p.exists() and p.is_file():
        p.unlink()
    side = p.with_suffix(p.suffix + ".h3.json")
    if side.exists():
        side.unlink()
    meta = _load_json(OUTPUT_META_FILE, {})
    meta.pop(_safe_rel(rel), None)
    _save_json(OUTPUT_META_FILE, meta)
    return web.json_response({"ok": True})


async def serve_output(request: web.Request) -> web.StreamResponse:
    p = _inside(OUTPUT_DIR, request.match_info["path"])
    if not p.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(p)


async def api_output_to_input(request: web.Request) -> web.Response:
    body = await request.json()
    rel = str(body.get("file") or "").strip()
    if not rel:
        raise web.HTTPBadRequest(text="output file required")
    src = _inside(OUTPUT_DIR, rel)
    if not src.is_file():
        raise web.HTTPNotFound(text="output not found")
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    name = _safe_name(src.name)
    dest = INPUT_DIR / name
    if dest.exists():
        dest = INPUT_DIR / f"{dest.stem}_{uuid.uuid4().hex[:8]}{dest.suffix}"
    shutil.copy2(src, dest)
    return web.json_response({"ok": True, "file": dest.name})


def _write_lora_config(cfg: dict[str, Any]) -> None:
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    (CONFIG_ROOT / "loras.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _parse_civitai_version_id(value: Any) -> int:
    raw = str(value or "").strip()
    if raw.isdigit():
        return int(raw)
    # Accept common CivitAI URLs when the selected model version is present.
    match = re.search(r"(?:modelVersionId=|/model-versions/|/api/download/models/)(\d+)", raw, re.I)
    if match:
        return int(match.group(1))
    raise web.HTTPBadRequest(
        text="Enter a CivitAI model VERSION ID, or paste a CivitAI URL containing modelVersionId."
    )


async def api_lora_config_get(request: web.Request) -> web.Response:
    cfg = load_yaml(CONFIG_ROOT / "loras.yaml", {}) or {"loras": []}
    return web.json_response({"loras": cfg.get("loras") or []})


async def api_lora_config_upsert(request: web.Request) -> web.Response:
    body = await request.json()
    vid = _parse_civitai_version_id(body.get("version_id") or body.get("source"))
    cfg = load_yaml(CONFIG_ROOT / "loras.yaml", {}) or {}
    items = list(cfg.get("loras") or [])
    target = next((x for x in items if int((x or {}).get("version_id") or 0) == vid), None)
    if target is None:
        target = {"version_id": vid, "enabled": True}
        items.append(target)

    allowed = {
        "enabled", "nickname", "filename", "recommended_strength",
        "trigger_words", "notes", "tags", "auto_download",
    }
    for key in allowed:
        if key in body:
            value = body[key]
            if key in {"trigger_words", "notes", "tags"}:
                if isinstance(value, str):
                    # Trigger words/tags are comma-delimited; notes are line-delimited.
                    if key == "notes":
                        value = [x.strip() for x in value.splitlines() if x.strip()]
                    else:
                        value = [x.strip() for x in value.split(",") if x.strip()]
                elif not isinstance(value, list):
                    value = []
            if key == "recommended_strength" and value not in (None, ""):
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    raise web.HTTPBadRequest(text="recommended_strength must be numeric")
            target[key] = value

    target["version_id"] = vid
    cfg["loras"] = items
    _write_lora_config(cfg)
    return web.json_response({"ok": True, "lora": target})


async def api_lora_config_disable(request: web.Request) -> web.Response:
    vid = int(request.match_info["vid"])
    cfg = load_yaml(CONFIG_ROOT / "loras.yaml", {}) or {}
    items = list(cfg.get("loras") or [])
    found = False
    for item in items:
        if int((item or {}).get("version_id") or 0) == vid:
            item["enabled"] = False
            found = True
            break
    cfg["loras"] = items
    _write_lora_config(cfg)
    return web.json_response({"ok": True, "found": found})


async def api_loras(request: web.Request) -> web.Response:
    catalog = _load_json(DATA_ROOT / "lora_catalog.json", {"managed": [], "unmanaged": []})
    items = []
    for group in ("managed", "unmanaged"):
        for item in catalog.get(group) or []:
            if item.get("status") == "ready":
                items.append(item)
    return web.json_response({"items": items})


async def api_sync_loras(request: web.Request) -> web.Response:
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, sync_loras, CONFIG_ROOT / "loras.yaml")
    return web.json_response(result)


async def api_prompts_get(request: web.Request) -> web.Response:
    return web.json_response({"prompts": _system_prompts()})


async def api_prompts_put(request: web.Request) -> web.Response:
    body = await request.json()
    prompts = body.get("prompts")
    if not isinstance(prompts, dict):
        raise web.HTTPBadRequest(text="prompts must be an object")
    allowed = {"t2v_auto", "i2v_auto", "r2v_auto"}
    clean = {k: str(v) for k, v in prompts.items() if k in allowed}
    (CONFIG_ROOT / "system_prompts.yaml").write_text(
        yaml.safe_dump({"prompts": clean}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return web.json_response({"ok": True, "prompts": clean})


async def api_templates_get(request: web.Request) -> web.Response:
    return web.json_response({"templates": _load_json(TEMPLATE_FILE, {})})


async def api_templates_put(request: web.Request) -> web.Response:
    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name:
        raise web.HTTPBadRequest(text="template name required")
    data = _load_json(TEMPLATE_FILE, {})
    data[name] = body.get("value") or {}
    _save_json(TEMPLATE_FILE, data)
    return web.json_response({"ok": True})


async def api_templates_delete(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    data = _load_json(TEMPLATE_FILE, {})
    data.pop(name, None)
    _save_json(TEMPLATE_FILE, data)
    return web.json_response({"ok": True})


async def api_free(request: web.Request) -> web.Response:
    return web.json_response({"ok": comfy.free_memory(True, True)})


async def api_interrupt(request: web.Request) -> web.Response:
    return web.json_response({"ok": comfy.interrupt()})


async def index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC / "index.html")


async def on_startup(app: web.Application) -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    app["session"] = ClientSession(timeout=ClientTimeout(total=60))
    app["ws_session"] = ClientSession(timeout=ClientTimeout(total=None))
    app["ws_task"] = asyncio.create_task(_ws_loop(app))


async def on_cleanup(app: web.Application) -> None:
    task = app.get("ws_task")
    if task:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    await app["ws_session"].close()
    await app["session"].close()


def make_app(comfy_url: str) -> web.Application:
    app = web.Application(client_max_size=4 * 1024**3)
    app["comfy"] = comfy_url.rstrip("/")
    app["client_id"] = "mmh3-phone-" + uuid.uuid4().hex
    app["plans"] = {}
    app["progress"] = {}
    app["records"] = _load_json(RECORD_FILE, {})
    app["ws_connected"] = False
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.add_routes([
        web.get("/", index),
        web.static("/static", STATIC, show_index=False),
        web.get("/api/info", api_info),
        web.post("/api/generate", api_generate),
        web.get("/api/queue", api_queue),
        web.get("/api/progress", api_progress),
        web.post("/api/cancel/{pid}", api_cancel),
        web.post("/api/upload", api_upload),
        web.get("/api/inputs", api_inputs),
        web.put("/api/assets/meta", api_asset_meta_put),
        web.get("/media/input/{path:.*}", serve_input),
        web.get("/media/input-thumb/{path:.*}", serve_input_thumb),
        web.get("/api/ui-state", api_ui_state_get),
        web.put("/api/ui-state", api_ui_state_put),
        web.get("/api/outputs", api_outputs),
        web.delete("/api/outputs/{path:.*}", api_delete_output),
        web.get("/media/output/{path:.*}", serve_output),
        web.post("/api/output-to-input", api_output_to_input),
        web.get("/api/loras", api_loras),
        web.post("/api/loras/sync", api_sync_loras),
        web.get("/api/loras/config", api_lora_config_get),
        web.post("/api/loras/config", api_lora_config_upsert),
        web.delete("/api/loras/config/{vid}", api_lora_config_disable),
        web.get("/api/system-prompts", api_prompts_get),
        web.put("/api/system-prompts", api_prompts_put),
        web.get("/api/templates", api_templates_get),
        web.post("/api/templates", api_templates_put),
        web.delete("/api/templates/{name}", api_templates_delete),
        web.post("/api/system/free", api_free),
        web.post("/api/system/interrupt", api_interrupt),
    ])
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("MMH3_PHONE_UI_PORT", "7860")))
    parser.add_argument("--comfy", default=os.environ.get("MMH3_COMFY_URL", "http://127.0.0.1:8188"))
    args = parser.parse_args()
    web.run_app(make_app(args.comfy), host="0.0.0.0", port=args.port, access_log=None)


if __name__ == "__main__":
    main()
