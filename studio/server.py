"""MMH3 Studio web server (port 7860): phone UI + JSON API."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from . import __version__, civitai, graph, library, loras, memory, paths, prompting, provision, recipes, util
from .comfy import Comfy, ComfyError
from .jobs import JobError, Runner

log = util.setup_logging("studio")
MAX_UPLOAD = 4 * 1024 ** 3
SETTABLE = {
    "memory.unload_on_family_switch": bool, "memory.trim_after_job_above": float,
    "memory.idle_unload_above": float, "output.crf": int, "output.delete_silent_twin": bool,
    "prompting.model": str, "prompting.reasoning_effort": str, "eros_enabled": bool,
    "civitai.domain": str,
}
LOG_FILES = {"comfyui": "comfyui.log", "studio": "studio.log", "provision": "provision.log",
             "supervisor": "supervisor.log", "jupyter": "jupyter.log"}


def ok(data: Any = None, status: int = 200) -> web.Response:
    return web.json_response({"ok": True} if data is None else data, status=status)


def bad(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


# ---------------------------------------------------------------------- auth

def _password() -> str:
    return os.environ.get("MMH3_PASSWORD", "").strip()


def _token(pw: str) -> str:
    return hmac.new(pw.encode(), b"mmh3-studio", hashlib.sha256).hexdigest()


@web.middleware
async def auth_mw(request: web.Request, handler):
    pw = _password()
    open_paths = ("/", "/api/login", "/manifest.webmanifest")
    if pw and not request.path.startswith("/static/") and request.path not in open_paths:
        if not hmac.compare_digest(request.cookies.get("mmh3", ""), _token(pw)):
            return bad("login required", 401)
    return await handler(request)


@web.middleware
async def errors_mw(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (JobError, library.LibraryError, recipes.RecipeError, graph.GraphError, prompting.PromptError) as exc:
        return bad(str(exc))
    except (ComfyError, civitai.CivitaiError) as exc:
        return bad(str(exc), 502)
    except Exception as exc:
        log.exception("unhandled error on %s", request.path)
        return bad(f"{type(exc).__name__}: {exc}", 500)


async def login(request: web.Request) -> web.Response:
    body = await request.json()
    pw = _password()
    if not pw or hmac.compare_digest(str(body.get("password", "")), pw):
        resp = ok()
        if pw:
            resp.set_cookie("mmh3", _token(pw), max_age=60 * 60 * 24 * 90, httponly=True, samesite="Lax")
        return resp
    await asyncio.sleep(1)
    return bad("wrong password", 401)


# ---------------------------------------------------------------------- pages

async def index(request: web.Request) -> web.StreamResponse:
    html = (paths.WEB_ROOT / "index.html").read_text(encoding="utf-8").replace("{{v}}", __version__)
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


def _checkpoints() -> list[dict[str, Any]]:
    return [{"id": c["id"], "label": c["label"], "available": c["available"], "builtin": c["builtin"],
             "image": c.get("image")} for c in loras.checkpoints()]


async def boot(request: web.Request) -> web.Response:
    s = util.settings()
    return ok({
        "version": __version__,
        "recipes": recipes.public_catalog(),
        "checkpoints": _checkpoints(),
        "aspects": list(graph.ASPECTS),
        "auth": bool(_password()),
        "prompting": {"model": (s.get("prompting") or {}).get("model"), "key": bool(prompting.api_key()),
                      "key_source": prompting.key_source()},
        "limits": {"duration": [1, 15], "megapixels": [0.25, 1.2], "refs": graph.MAX_REFS},
    })


async def state(request: web.Request) -> web.Response:
    runner: Runner = request.app["runner"]
    comfy: Comfy = request.app["comfy"]
    prov = await asyncio.to_thread(provision.status)
    fam = {}
    for f in ("fl2v", "ref2v"):
        ready, missing = provision.family_ready(f)
        fam[f] = {"ready": ready, "missing": missing}
    downloading = [m for m in prov["models"].values() if m.get("state") in ("downloading", "queued")]
    return ok({
        **runner.snapshot(),
        "comfy": comfy.connected,
        "memory": memory.read().as_dict(),
        "families": fam,
        "downloads": {"active": bool(downloading), "items": downloading},
        "time": time.time(),
    })


# ---------------------------------------------------------------------- generate / queue

async def generate(request: web.Request) -> web.Response:
    jobs = request.app["runner"].submit(await request.json())
    return ok({"jobs": [j["id"] for j in jobs], "seed": jobs[0]["seed"]})


def _prompt_job(form: dict[str, Any]) -> dict[str, Any]:
    """The parts of a Create form the prompt writer needs."""
    mode = str(form.get("mode") or "")
    recipes.family_for(mode)
    job = {k: form.get(k) for k in ("mode", "idea", "aspect", "megapixels", "duration", "start_image",
                                     "end_image", "refs")}
    job["aspect"] = job.get("aspect") or "9:16"
    job["megapixels"] = float(job.get("megapixels") or 0.7)
    job["duration"] = float(job.get("duration") or 5)
    for key in ("start_image", "end_image"):
        if job.get(key) and not library.safe_rel(paths.INPUT, job[key]).is_file():
            raise prompting.PromptError(f"input file is missing: {job[key]}")
    return job


async def draft(request: web.Request) -> web.Response:
    job = _prompt_job(await request.json())
    started = time.time()
    text = await prompting.write(job)
    return ok({"prompt": text, "seconds": round(time.time() - started, 1)})


async def refine(request: web.Request) -> web.Response:
    """Tweak: edit an existing prompt from a short change request."""
    form = await request.json()
    job = _prompt_job(form)
    started = time.time()
    text = await prompting.refine(job, str(form.get("prompt") or ""), str(form.get("request") or ""))
    return ok({"prompt": text, "seconds": round(time.time() - started, 1)})


async def choices(request: web.Request) -> web.Response:
    comfy: Comfy = request.app["comfy"]
    return ok({"samplers": await comfy.choices("KSamplerSelect", "sampler_name"),
               "schedulers": await comfy.choices("BasicScheduler", "scheduler")})


async def job_action(request: web.Request) -> web.Response:
    runner: Runner = request.app["runner"]
    jid, action = request.match_info["id"], request.match_info["action"]
    body = await request.json() if request.can_read_body else {}
    if action == "cancel":
        runner.cancel(jid)
    elif action == "retry":
        return ok({"jobs": [j["id"] for j in runner.retry(jid)]})
    elif action == "remove":
        runner.remove(jid)
    elif action == "move":
        runner.move(jid, int(body.get("direction", -1)))
    elif action == "prompt":
        runner.update_prompt(jid, str(body.get("prompt") or ""))
    else:
        return bad("unknown action", 404)
    return ok()


async def job_preview(request: web.Request) -> web.StreamResponse:
    seq, data, mime = request.app["runner"].previews.get(request.match_info["id"], (0, b"", ""))
    if not data:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type=mime.split(";")[0], headers={"Cache-Control": "no-store"})


async def queue_action(request: web.Request) -> web.Response:
    runner: Runner = request.app["runner"]
    action = request.match_info["action"]
    body = await request.json() if request.can_read_body else {}
    if action == "pause":
        runner.set_paused(bool(body.get("paused", True)))
    elif action == "clear":
        return ok({"removed": runner.clear_finished()})
    else:
        return bad("unknown action", 404)
    return ok()


# ---------------------------------------------------------------------- outputs

async def outputs(request: web.Request) -> web.Response:
    return ok(await asyncio.to_thread(library.outputs))


async def output_patch(request: web.Request) -> web.Response:
    body = await request.json()
    return ok(library.update_output(body["file"], favorite=body.get("favorite"), group=body.get("group"),
                                    tags=body.get("tags")))


async def output_delete(request: web.Request) -> web.Response:
    await asyncio.to_thread(library.delete_output, request.query["file"])
    return ok()


async def outputs_seen(request: web.Request) -> web.Response:
    body = await request.json()
    library.mark_seen(body.get("files") or [], everything=bool(body.get("all")))
    return ok()


async def groups_put(request: web.Request) -> web.Response:
    return ok({"groups": library.set_groups((await request.json()).get("groups") or [])})


async def output_reuse(request: web.Request) -> web.Response:
    rel = request.query["file"]
    item = next((i for i in library.index()["items"] if i["file"] == rel), None)
    if not item:
        return bad("no such output", 404)
    return ok(library.normalize_record(item.get("meta") or {}))


async def output_continue(request: web.Request) -> web.Response:
    rel = (await request.json())["file"]
    return ok({"start_image": await asyncio.to_thread(library.continue_frame, rel)})


async def output_to_input(request: web.Request) -> web.Response:
    rel = (await request.json())["file"]
    return ok({"file": await asyncio.to_thread(library.output_as_input, rel)})


async def serve_output(request: web.Request) -> web.StreamResponse:
    p = library.safe_rel(paths.OUTPUT, request.match_info["path"])
    if not p.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})


async def serve_output_thumb(request: web.Request) -> web.StreamResponse:
    p = await asyncio.to_thread(library.thumb_for_output, request.match_info["path"])
    if not p:
        raise web.HTTPNotFound()
    return web.FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------------- inputs / kits

async def assets(request: web.Request) -> web.Response:
    return ok({"items": await asyncio.to_thread(library.assets), "kits": library.kits()})


async def upload(request: web.Request) -> web.Response:
    reader = await request.multipart()
    saved = []
    tmpdir = paths.INPUT / ".uploading"
    tmpdir.mkdir(parents=True, exist_ok=True)
    while True:
        part = await reader.next()
        if part is None:
            break
        if not part.filename:
            continue
        fd, tmp = tempfile.mkstemp(dir=tmpdir)
        size = 0
        with os.fdopen(fd, "wb") as f:
            while chunk := await part.read_chunk(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    f.close()
                    os.unlink(tmp)
                    return bad("file too large", 413)
                f.write(chunk)
        try:
            saved.append(await asyncio.to_thread(library.save_upload, part.filename, Path(tmp)))
        except library.LibraryError:
            os.unlink(tmp)
            raise
    if not saved:
        return bad("no file received")
    return ok({"items": saved})


async def asset_patch(request: web.Request) -> web.Response:
    body = await request.json()
    library.set_nickname(body["file"], str(body.get("nickname") or ""))
    return ok()


async def asset_delete(request: web.Request) -> web.Response:
    library.delete_asset(request.query["file"])
    return ok()


async def asset_soundtrack(request: web.Request) -> web.Response:
    rel = request.query["file"]
    return ok({"has_audio": await asyncio.to_thread(library.use_soundtrack_default, rel)})


async def serve_input(request: web.Request) -> web.StreamResponse:
    p = library.safe_rel(paths.INPUT, request.match_info["path"])
    if not p.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})


async def serve_input_thumb(request: web.Request) -> web.StreamResponse:
    p = await asyncio.to_thread(library.thumb_for_asset, request.match_info["path"])
    if not p:
        raise web.HTTPNotFound()
    return web.FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})


async def kit_save(request: web.Request) -> web.Response:
    body = await request.json()
    return ok(library.save_kit(body.get("name", ""), body.get("refs") or [], body.get("id")))


async def kit_delete(request: web.Request) -> web.Response:
    library.delete_kit(request.match_info["id"])
    return ok()


async def ref_tags(request: web.Request) -> web.Response:
    return ok({"tags": graph.reference_tags((await request.json()).get("refs") or [])})


# ---------------------------------------------------------------------- loras

async def lora_list(request: web.Request) -> web.Response:
    data = await asyncio.to_thread(loras.listing)
    data["checkpoints"] = _checkpoints()
    return ok(data)


async def lora_upsert(request: web.Request) -> web.Response:
    """Add a CivitAI version ({version_id, file_ids?}) or edit an entry ({key, ...fields})."""
    body = await request.json()
    vid = str(body.get("version_id") or "").strip()
    if vid and not body.get("key"):
        if not vid.isdigit():
            return bad("CivitAI version id must be a number (the modelVersionId in the URL)")
        entry = await asyncio.to_thread(loras.add_version, int(vid), body.get("file_ids"))
        return ok(entry)
    if not body.get("key"):
        return bad("nothing to save")
    fields = {k: body[k] for k in ("enabled", "recommended_strength", "nickname", "tags", "trigger_words",
                                   "notes", "files", "auto_install") if k in body}
    entry = loras.upsert({"key": body["key"], "version_id": body.get("version_id"),
                          "filename": body.get("filename"), **fields})
    await asyncio.to_thread(loras.enqueue_entry, entry)
    return ok(entry)


async def lora_delete(request: web.Request) -> web.Response:
    loras.remove(request.match_info["key"], delete_files=request.query.get("file") == "1")
    request.app["comfy"].forget_object_info()
    return ok()


async def lora_install(request: web.Request) -> web.Response:
    return ok(await asyncio.to_thread(loras.install, request.match_info["key"]))


async def lora_uninstall(request: web.Request) -> web.Response:
    entry = await asyncio.to_thread(loras.uninstall, request.match_info["key"])
    request.app["comfy"].forget_object_info()
    return ok(entry)


async def lora_sync(request: web.Request) -> web.Response:
    return ok({"started": await asyncio.to_thread(loras.sync)})


async def checkpoint_add(request: web.Request) -> web.Response:
    body = await request.json()
    ck = await asyncio.to_thread(loras.add_checkpoint, int(body["version_id"]), body.get("file_id"), body.get("label"))
    return ok(ck)


async def checkpoint_delete(request: web.Request) -> web.Response:
    ck_id = request.match_info["id"]
    if any(c["id"] == ck_id and c["builtin"] for c in loras.checkpoints()):
        return bad("built-in checkpoints can't be removed")
    loras.remove_checkpoint(ck_id, delete_files=request.query.get("file") == "1")
    return ok()


def _mark_installed(result: dict[str, Any]) -> dict[str, Any]:
    have = {str(e.get("version_id")) for e in loras.catalog()}
    cks = {str(c.get("version_id")) for c in loras.checkpoints() if c.get("version_id")}
    for item in result.get("items") or []:
        for v in item.get("versions") or []:
            v["installed"] = str(v.get("id")) in (cks if item.get("type") == "Checkpoint" else have)
    return result


async def civitai_search(request: web.Request) -> web.Response:
    q = request.query
    result = await asyncio.to_thread(
        civitai.search, q.get("q", ""), q.get("kind", "lora"), q.get("h3", "1") != "0",
        q.get("cursor") or None, q.get("source") == "bookmarks")
    return ok(_mark_installed(result))


async def civitai_collections(request: web.Request) -> web.Response:
    return ok({"items": await asyncio.to_thread(civitai.my_collections)})


async def civitai_collection(request: web.Request) -> web.Response:
    q = request.query
    result = await asyncio.to_thread(civitai.collection_models, int(request.match_info["id"]),
                                     q.get("cursor") or None, q.get("kind", "all"), q.get("h3", "1") != "0")
    return ok(_mark_installed(result))


async def civitai_version(request: web.Request) -> web.Response:
    return ok(await asyncio.to_thread(civitai.version, int(request.match_info["id"])))


# ---------------------------------------------------------------------- system

async def system(request: web.Request) -> web.Response:
    comfy: Comfy = request.app["comfy"]
    s = util.settings()
    services = util.read_json(paths.STATE / "services.json", {}) or {}
    return ok({
        "version": __version__,
        "models": (await asyncio.to_thread(provision.status))["models"],
        "services": {k: {kk: v.get(kk) for kk in ("alive", "started", "restarts")} if isinstance(v, dict) else v
                     for k, v in services.items() if k != "updated_at"},
        "comfy_args": ((services.get("comfy") or {}).get("argv") or [])[2:],
        "memory": memory.read().as_dict(),
        "gpu": await asyncio.to_thread(memory.gpu),
        "comfy_connected": comfy.connected,
        "settings": {k: _get_dotted(s, k) for k in SETTABLE},
        "prompting": {"key": bool(prompting.api_key()), "key_source": prompting.key_source(),
                      "model": (s.get("prompting") or {}).get("model")},
        "civitai_token": bool(civitai.token()), "civitai_domain": civitai.base_url(),
        "password": bool(_password()),
    })


def _get_dotted(d: dict[str, Any], key: str) -> Any:
    for k in key.split("."):
        d = d.get(k) if isinstance(d, dict) else None
    return d


async def system_settings(request: web.Request) -> web.Response:
    body = await request.json()
    for key, value in body.items():
        if key not in SETTABLE:
            return bad(f"{key} can't be changed here")
        util.save_setting(key, SETTABLE[key](value))
    if body.get("eros_enabled"):
        _spawn_provision(["core", "eros"])
    return ok()


def _spawn_provision(groups: list[str]) -> None:
    args = [sys.executable, "-m", "studio.provision"]
    for g in groups:
        args += ["--group", g]
    env = dict(os.environ, PYTHONPATH=str(paths.IMAGE_ROOT))
    subprocess.Popen(args, cwd=paths.IMAGE_ROOT, env=env, start_new_session=True,
                     stdout=(paths.LOGS / "provision.log").open("ab"), stderr=subprocess.STDOUT)


async def system_action(request: web.Request) -> web.Response:
    comfy: Comfy = request.app["comfy"]
    runner: Runner = request.app["runner"]
    action = request.match_info["action"]
    body = await request.json() if request.can_read_body else {}
    if action == "free":
        busy = any(j["status"] == "running" for j in runner.jobs.values())
        if busy and body.get("unload", True):
            return bad("a video is rendering; unloading now would break it. Cancel it first.")
        if body.get("unload", True):
            await runner._unload_all()
        else:
            await comfy.free(unload_models=False, free_memory=True)
    elif action == "restart-comfy":
        (paths.STATE / "restart_comfy.request").touch()
        runner.resident = None
    elif action == "provision":
        _spawn_provision([g for g in (body.get("groups") or ["core"]) if g in ("core", "eros")])
    elif action == "key":
        prompting.save_key(str(body.get("key") or ""))
    else:
        return bad("unknown action", 404)
    return ok()


async def prompts_get(request: web.Request) -> web.Response:
    base = util.image_yaml("system_prompts.yaml").get("prompts") or {}
    return ok({"prompts": prompting.system_prompts(), "defaults": base, "stale": prompting.stale_prompts()})


async def prompts_put(request: web.Request) -> web.Response:
    body = await request.json()
    name = body.get("name")
    if name not in prompting.PROMPT_NAMES:
        return bad("unknown prompt")
    prompting.save_system_prompt(name, str(body.get("text") or ""))
    return ok()


async def logs(request: web.Request) -> web.Response:
    name = LOG_FILES.get(request.match_info["name"])
    if not name:
        return bad("unknown log", 404)
    path = paths.LOGS / name
    n = min(2000, int(request.query.get("lines", 300)))
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            lines = f.read().decode(errors="replace").splitlines()[-n:]
    except OSError:
        lines = []
    return web.Response(text="\n".join(lines), content_type="text/plain")


async def form_state(request: web.Request) -> web.Response:
    path = paths.DATA / "studio_form.json"
    if request.method == "PUT":
        util.write_json(path, await request.json())
        return ok()
    return ok(util.read_json(path, {}) or {})


# ---------------------------------------------------------------------- app

async def on_startup(app: web.Application) -> None:
    paths.ensure_dirs()
    loras.ensure_seeded()
    comfy = Comfy(f"http://127.0.0.1:{(util.settings().get('ports') or {}).get('comfy', 8188)}")
    await comfy.start()
    runner = Runner(comfy)
    await runner.start()
    app["comfy"], app["runner"] = comfy, runner
    await asyncio.to_thread(loras.sync)
    asyncio.create_task(asyncio.to_thread(library.refresh_index))


async def on_cleanup(app: web.Application) -> None:
    await app["runner"].stop()
    await app["comfy"].close()


def make_app() -> web.Application:
    app = web.Application(middlewares=[errors_mw, auth_mw], client_max_size=MAX_UPLOAD)
    r = app.router
    r.add_get("/", index)
    r.add_static("/static", paths.WEB_ROOT, append_version=False)
    r.add_post("/api/login", login)
    r.add_get("/api/boot", boot)
    r.add_get("/api/state", state)
    r.add_post("/api/generate", generate)
    r.add_post("/api/draft", draft)
    r.add_post("/api/refine", refine)
    r.add_get("/api/choices", choices)
    r.add_post("/api/jobs/{id}/{action}", job_action)
    r.add_get("/api/jobs/{id}/preview", job_preview)
    r.add_post("/api/queue/{action}", queue_action)
    r.add_get("/api/outputs", outputs)
    r.add_patch("/api/outputs", output_patch)
    r.add_delete("/api/outputs", output_delete)
    r.add_post("/api/outputs/seen", outputs_seen)
    r.add_put("/api/groups", groups_put)
    r.add_get("/api/outputs/reuse", output_reuse)
    r.add_post("/api/outputs/continue", output_continue)
    r.add_post("/api/outputs/to-input", output_to_input)
    r.add_get("/media/output/{path:.+}", serve_output)
    r.add_get("/media/output-thumb/{path:.+}", serve_output_thumb)
    r.add_get("/api/assets", assets)
    r.add_post("/api/upload", upload)
    r.add_patch("/api/assets", asset_patch)
    r.add_delete("/api/assets", asset_delete)
    r.add_get("/api/assets/soundtrack", asset_soundtrack)
    r.add_get("/media/input/{path:.+}", serve_input)
    r.add_get("/media/input-thumb/{path:.+}", serve_input_thumb)
    r.add_post("/api/kits", kit_save)
    r.add_delete("/api/kits/{id}", kit_delete)
    r.add_post("/api/refs/tags", ref_tags)
    r.add_get("/api/loras", lora_list)
    r.add_post("/api/loras", lora_upsert)
    r.add_delete("/api/loras/{key}", lora_delete)
    r.add_post("/api/loras/sync", lora_sync)
    r.add_post("/api/loras/{key}/install", lora_install)
    r.add_post("/api/loras/{key}/uninstall", lora_uninstall)
    r.add_post("/api/checkpoints", checkpoint_add)
    r.add_delete("/api/checkpoints/{id}", checkpoint_delete)
    r.add_get("/api/civitai/search", civitai_search)
    r.add_get("/api/civitai/collections", civitai_collections)
    r.add_get("/api/civitai/collections/{id}", civitai_collection)
    r.add_get("/api/civitai/version/{id}", civitai_version)
    r.add_get("/api/system", system)
    r.add_put("/api/system/settings", system_settings)
    r.add_post("/api/system/{action}", system_action)
    r.add_get("/api/system/prompts", prompts_get)
    r.add_put("/api/system/prompts", prompts_put)
    r.add_get("/api/logs/{name}", logs)
    r.add_get("/api/form", form_state)
    r.add_put("/api/form", form_state)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    port = int((util.settings().get("ports") or {}).get("studio", 7860))
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    web.run_app(make_app(), host="0.0.0.0", port=port, access_log=None)


if __name__ == "__main__":
    main()
