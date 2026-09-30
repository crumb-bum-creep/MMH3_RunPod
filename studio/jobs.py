"""The Studio queue.

Studio owns the queue and hands ComfyUI one job at a time. That makes it
possible to:
  - queue T2V, I2V and R2V in any order; when the next job needs a different
    model family or base checkpoint, the previous models are fully unloaded
    first (the rule that kept RAM flat in the d2103 build);
  - draft auto prompts for queued jobs while the current one renders;
  - pause, reorder and cancel without touching ComfyUI's own queue;
  - recover after a Studio restart: a job ComfyUI finished meanwhile is still
    collected from ComfyUI's history.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
import time
import uuid
from typing import Any

from . import graph, library, loras, memory, paths, prompting, provision, recipes, util
from .comfy import Comfy, ComfyError

log = logging.getLogger("jobs")
JOBS_FILE = paths.DATA / "jobs.json"
RESTART_REQUEST = paths.STATE / "restart_comfy.request"
KEEP_FINISHED = 300
ACTIVE = ("drafting", "waiting", "unloading", "running")
PENDING = ("queued",) + ACTIVE

STAGE_BY_CLASS = {
    "UNETLoader": "loading models", "CLIPLoader": "loading models", "VAELoader": "loading models",
    "LoraLoaderModelOnly": "loading models", "MiniMaxH3SigmaShift": "loading models",
    "ModelPreviewOverrideKJ": "loading models",
    "LoadImage": "reading inputs", "MiniMaxH3ReferencePack": "reading references",
    "MiniMaxH3ImageToVideo": "encoding prompt", "MiniMaxH3ReferenceToVideo": "encoding prompt",
    "SamplerCustomAdvanced": "sampling", "VAEDecode": "decoding video", "VAEDecodeAudio": "decoding audio",
    "VHS_VideoCombine": "saving",
}


class JobError(ValueError):
    pass


def _now() -> float:
    return time.time()


def new_seed() -> int:
    return random.randint(1, 2 ** 50)


class Runner:
    def __init__(self, comfy: Comfy) -> None:
        self.comfy = comfy
        self.jobs: dict[str, dict[str, Any]] = {}
        self.resident: tuple[str, str] | None = None
        self.paused = False
        self.previews: dict[str, tuple[int, bytes, str]] = {}
        self._wake = asyncio.Event()
        self._done: dict[str, asyncio.Future] = {}
        self._titles: dict[str, dict[str, tuple[str, str]]] = {}
        self._drafting: set[str] = set()
        self._dirty = False
        self._last_idle_free = 0.0
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        saved = util.read_json(JOBS_FILE, {}) or {}
        for job in saved.get("jobs") or []:
            if job.get("status") in ("drafting", "waiting", "unloading"):
                job["status"] = "queued"
            self.jobs[job["id"]] = job
        self.paused = bool(saved.get("paused"))
        self.comfy.on_event(self._on_event)
        self._tasks = [asyncio.create_task(self._loop(), name="runner"),
                       asyncio.create_task(self._prefetch_loop(), name="prefetch"),
                       asyncio.create_task(self._persist_loop(), name="persist")]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._save()

    def _save(self) -> None:
        jobs = sorted(self.jobs.values(), key=lambda j: j["created"])
        finished = [j for j in jobs if j["status"] not in PENDING]
        drop = {j["id"] for j in finished[:-KEEP_FINISHED]} if len(finished) > KEEP_FINISHED else set()
        for jid in drop:
            self.jobs.pop(jid, None)
            self.previews.pop(jid, None)
        util.write_json(JOBS_FILE, {"paused": self.paused, "jobs": [j for j in jobs if j["id"] not in drop]})
        self._dirty = False

    def _touch(self, job: dict[str, Any], **values: Any) -> None:
        job.update(values)
        job["updated"] = _now()
        self._dirty = True
        if "status" in values or "prompt_id" in values:
            self._save()  # state changes are persisted at once so a crash never loses a job

    async def _persist_loop(self) -> None:
        while True:
            await asyncio.sleep(2)
            if self._dirty:
                await asyncio.to_thread(self._save)

    # ------------------------------------------------------------------ public API

    def submit(self, form: dict[str, Any]) -> list[dict[str, Any]]:
        """Validate a Create form and enqueue one job, or several (compare / count)."""
        mode = str(form.get("mode") or "").lower()
        if mode not in recipes.FAMILY_OF_MODE:
            raise JobError("pick a mode")
        family = recipes.family_for(mode)
        prompt = str(form.get("prompt") or "").strip()
        idea = str(form.get("idea") or "").strip()
        if not prompt and not idea:
            raise JobError("write a prompt (or an idea for Auto)")
        aspect = str(form.get("aspect") or "9:16")
        if aspect not in graph.ASPECTS:
            raise JobError("unknown aspect ratio")
        mp = min(1.2, max(0.25, float(form.get("megapixels") or 0.7)))
        duration = min(15.0, max(1.0, float(form.get("duration") or 5)))
        checkpoint = str(form.get("checkpoint") or "stock")
        if loras.checkpoint_file(checkpoint, family) is None:
            raise JobError("that base checkpoint has no model for this mode")
        picks = [{"key": str(i.get("key") or i.get("file") or ""), "file": i.get("file"),
                  "strength": float(i.get("strength", 1.0)), "nickname": i.get("nickname") or ""}
                 for i in form.get("loras") or [] if (i.get("key") or i.get("file"))]
        lora_list, warnings = loras.resolve_selection(picks, family)
        if warnings:
            raise JobError("; ".join(warnings))
        start_image = str(form.get("start_image") or "").strip() or None
        end_image = str(form.get("end_image") or "").strip() or None
        refs = form.get("refs") or []
        if mode == "i2v" and not start_image:
            raise JobError("image-to-video needs a start image")
        if mode == "r2v":
            if not refs:
                raise JobError("add at least one reference")
            graph.references_json(refs)  # raises on bad/too many refs
        for rel in [start_image, end_image] + [r.get("file") for r in refs]:
            if rel and not library.safe_rel(paths.INPUT, rel).exists():
                raise JobError(f"input file is missing: {rel}")

        overrides = form.get("overrides") or {}
        if form.get("compare"):
            recipe_ids = (recipes.catalog().get(family) or {}).get("compare") or []
            overrides = {}  # a comparison is between the named recipes as written
        else:
            recipe_ids = [form.get("recipe_id") or None]
        count = 1 if form.get("compare") else max(1, min(4, int(form.get("count") or 1)))
        seed_in = form.get("seed")
        base_seed = int(seed_in) if seed_in not in (None, "", "random") else new_seed()
        group = uuid.uuid4().hex[:8] if len(recipe_ids) > 1 or count > 1 else None

        created = []
        for n in range(count):
            seed = base_seed if n == 0 else new_seed()
            for rid in recipe_ids:
                recipe = recipes.resolve(mode, rid, overrides)
                job = {
                    "id": uuid.uuid4().hex[:12], "created": _now(), "updated": _now(),
                    "status": "queued", "mode": mode, "family": family,
                    "prompt_mode": "auto" if (not prompt and idea) else ("auto" if form.get("prompt_mode") == "auto" else "manual"),
                    "idea": idea, "prompt": prompt, "aspect": aspect, "megapixels": mp, "duration": duration,
                    "seed": seed, "checkpoint": checkpoint, "recipe_id": recipe["id"], "recipe": recipe,
                    "overrides": {k: overrides[k] for k in recipe.get("overridden", [])},
                    "loras": lora_list, "lora_picks": picks, "start_image": start_image, "end_image": end_image,
                    "refs": refs if mode == "r2v" else [], "group": group,
                    "compare": bool(form.get("compare")), "label": recipe.get("label") or recipe["id"],
                    "stage": "", "note": "", "error": None, "step": 0, "total_steps": 0,
                }
                self.jobs[job["id"]] = job
                created.append(job)
        self._save()
        self._wake.set()
        return created

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self._get(job_id)
        if job["status"] in ("queued", "drafting", "waiting", "unloading"):
            self._touch(job, status="cancelled", stage="", note="")
        elif job["status"] == "running":
            job["cancel_requested"] = True
            asyncio.create_task(self._interrupt(job))
        return job

    async def _interrupt(self, job: dict[str, Any]) -> None:
        pid = job.get("prompt_id")
        try:
            if pid:
                await self.comfy.delete_queued(pid)
                await self.comfy.interrupt(pid)
        except ComfyError as exc:
            log.warning("interrupt failed: %s", exc)

    def remove(self, job_id: str) -> None:
        job = self._get(job_id)
        if job["status"] in PENDING:
            raise JobError("cancel it first")
        self.jobs.pop(job_id, None)
        self.previews.pop(job_id, None)
        self._dirty = True

    def clear_finished(self) -> int:
        gone = [j for j, v in self.jobs.items() if v["status"] not in PENDING]
        for jid in gone:
            self.jobs.pop(jid, None)
            self.previews.pop(jid, None)
        self._dirty = True
        return len(gone)

    def move(self, job_id: str, direction: int) -> None:
        job = self._get(job_id)
        queued = [j for j in self._queue() if j["status"] == "queued"]
        if job not in queued:
            raise JobError("only waiting jobs can be moved")
        i = queued.index(job)
        k = i + (1 if direction > 0 else -1)
        if 0 <= k < len(queued):
            job["created"], queued[k]["created"] = queued[k]["created"], job["created"]
            self._dirty = True

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self._dirty = True
        self._wake.set()

    def retry(self, job_id: str) -> list[dict[str, Any]]:
        job = self._get(job_id)
        form = {k: job.get(k) for k in ("mode", "idea", "prompt", "aspect", "megapixels", "duration",
                                         "checkpoint", "start_image", "end_image", "refs",
                                         "recipe_id", "overrides", "seed", "prompt_mode")}
        form["loras"] = job.get("lora_picks") or job.get("loras") or []
        return self.submit(form)

    def update_prompt(self, job_id: str, prompt: str) -> dict[str, Any]:
        job = self._get(job_id)
        if job["status"] not in ("queued", "waiting"):
            raise JobError("the job has already started")
        self._touch(job, prompt=prompt.strip(), error=None)
        return job

    def _get(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise JobError("no such job")
        return job

    def _queue(self) -> list[dict[str, Any]]:
        return sorted((j for j in self.jobs.values() if j["status"] in PENDING), key=lambda j: j["created"])

    def snapshot(self) -> dict[str, Any]:
        jobs = sorted(self.jobs.values(), key=lambda j: j["created"])
        pending = [j for j in jobs if j["status"] in PENDING]
        finished = [j for j in jobs if j["status"] not in PENDING][::-1][:60]
        return {"paused": self.paused, "resident": list(self.resident) if self.resident else None,
                "pending": [self._public(j) for j in pending],
                "finished": [self._public(j) for j in finished]}

    def _public(self, j: dict[str, Any]) -> dict[str, Any]:
        seq = self.previews.get(j["id"], (0, b"", ""))[0]
        out = {k: j.get(k) for k in (
            "id", "status", "mode", "idea", "prompt", "aspect", "megapixels", "duration", "seed", "checkpoint",
            "recipe_id", "label", "group", "compare", "stage", "note", "error", "step", "total_steps",
            "avg_step_ms", "created", "started", "finished", "output", "loras", "refs", "start_image",
            "end_image", "prompt_mode", "overrides", "timings", "eta", "lora_picks")}
        out["preview_seq"] = seq
        return out

    # ------------------------------------------------------------------ runner

    async def _loop(self) -> None:
        await self._recover()
        while True:
            try:
                job = None if self.paused else next((j for j in self._queue() if j["status"] != "running"), None)
                if job is None:
                    await self._idle_housekeeping()
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
                    continue
                await self._run(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("runner loop error")
                await asyncio.sleep(3)

    async def _run(self, job: dict[str, Any]) -> None:
        # 1. prompt
        if not job.get("prompt"):
            self._touch(job, status="drafting", stage="writing prompt")
            ok = await self._ensure_prompt(job)
            if not ok:
                return
        if job["status"] == "cancelled":
            return

        # 2. models on disk + ComfyUI up
        ready, missing = provision.family_ready(job["family"], job["checkpoint"])
        if not ready:
            prog = provision.status()["models"]
            pct = _download_pct([prog[m] for m in missing if m in prog])
            self._touch(job, status="waiting", stage="waiting for models",
                        note=f"Downloading {', '.join(missing)}" + (f" ({pct}%)" if pct is not None else ""))
            await asyncio.sleep(5)
            if job["status"] == "waiting":
                job["status"] = "queued"
            return
        if not await self.comfy.alive():
            self._touch(job, status="waiting", stage="waiting for ComfyUI", note="ComfyUI is starting")
            await asyncio.sleep(5)
            if job["status"] == "waiting":
                job["status"] = "queued"
            return

        try:
            files = self._files(job)
            for l in job["loras"]:
                if not (paths.MODELS / "loras" / l["file"]).exists():
                    raise JobError(f"LoRA not installed: {l['file']}")
            prompt_graph = graph.build({**job, "output_prefix": self._prefix(job),
                                        "crf": (util.settings().get("output") or {}).get("crf", 12)}, files)
        except (JobError, graph.GraphError, recipes.RecipeError, KeyError) as exc:
            self._touch(job, status="failed", error=str(exc), stage="")
            return

        # 3. unload the other family's models before switching
        key = (job["family"], files["unet"])
        mem_cfg = util.settings().get("memory") or {}
        if self.resident and self.resident != key and mem_cfg.get("unload_on_family_switch", True):
            self._touch(job, status="unloading", stage="unloading previous models",
                        note=f"Switching {self.resident[0]} → {key[0]}")
            await self._unload_all()
        if job["status"] == "cancelled":
            return

        # 4. run
        self._titles[job["id"]] = {nid: (n["class_type"], n["_meta"]["title"]) for nid, n in prompt_graph.items()}
        try:
            pid = await self.comfy.queue_prompt(prompt_graph)
        except ComfyError as exc:
            self._touch(job, status="failed", error=str(exc), stage="")
            return
        self.resident = key
        self._touch(job, status="running", prompt_id=pid, started=_now(), stage="starting", note="",
                    step=0, total_steps=job["recipe"]["steps"])
        await self._await_completion(job)

    async def _ensure_prompt(self, job: dict[str, Any]) -> bool:
        """Draft (or reuse the group's) prompt. Returns False when the job failed."""
        group_prompt = self._group_prompt(job)
        if group_prompt:
            self._touch(job, prompt=group_prompt)
            return True
        while job["id"] in self._drafting:  # the prefetcher is already on it
            await asyncio.sleep(0.5)
            if job.get("prompt") or job["status"] in ("failed", "cancelled"):
                return bool(job.get("prompt"))
        self._drafting.add(job["id"])
        try:
            text = await prompting.write(job)
        except Exception as exc:
            self._touch(job, status="failed", error=f"Prompt writer: {exc}", stage="")
            return False
        finally:
            self._drafting.discard(job["id"])
        self._touch(job, prompt=text, stage="")
        for other in self.jobs.values():
            if job.get("group") and other.get("group") == job["group"] and not other.get("prompt"):
                self._touch(other, prompt=text)
        return True

    def _group_prompt(self, job: dict[str, Any]) -> str:
        if not job.get("group"):
            return ""
        for other in self.jobs.values():
            if other.get("group") == job["group"] and other.get("prompt"):
                return other["prompt"]
        return ""

    async def _prefetch_loop(self) -> None:
        """Draft auto prompts for queued jobs ahead of their turn."""
        while True:
            await asyncio.sleep(2)
            try:
                waiting = [j for j in self._queue() if j["status"] == "queued" and not j.get("prompt")
                           and j["id"] not in self._drafting and not self._group_prompt(j)
                           and not j.get("draft_failed")]
                if not waiting or not prompting.api_key():
                    continue
                job = waiting[0]
                self._drafting.add(job["id"])
                try:
                    text = await prompting.write(job)
                    for other in [job] + [o for o in self.jobs.values()
                                          if job.get("group") and o.get("group") == job["group"]]:
                        if not other.get("prompt"):
                            self._touch(other, prompt=text)
                except Exception as exc:
                    job["draft_failed"] = True  # the runner retries once and reports the error
                    log.warning("prefetch draft failed: %s", exc)
                finally:
                    self._drafting.discard(job["id"])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("prefetch loop")

    def _files(self, job: dict[str, Any]) -> dict[str, str]:
        models = util.image_yaml("models.yaml")["models"]
        taeh3 = models["taeh3"]
        unet = loras.checkpoint_file(job["checkpoint"], job["family"])
        if not unet:
            raise JobError("base checkpoint has no model for this mode")
        return {
            "unet": unet,
            "text_encoder": models["text_encoder"]["file"],
            "video_vae": models["video_vae"]["file"],
            "audio_vae": models["audio_vae"]["file"],
            "turbo": models[job["recipe"]["lora"]]["file"],
            "preview_vae": taeh3["file"] if provision.is_present(taeh3) else "",
        }

    def _prefix(self, job: dict[str, Any]) -> str:
        folder = str((util.settings().get("output") or {}).get("folder") or "MMH3").strip("/") or "MMH3"
        return f"{folder}/{job['mode'].upper()}"

    async def _unload_all(self) -> None:
        """Unload every model and wait until the container's memory stops falling."""
        try:
            await self.comfy.free(unload_models=True, free_memory=True)
        except ComfyError as exc:
            log.warning("free failed: %s", exc)
        # /free is handled asynchronously by ComfyUI; settle = 3 samples in a row
        # within 256 MB of each other (typically 2-6 s for a full H3 family).
        last, stable = memory.read().used, 0
        for _ in range(40):
            await asyncio.sleep(0.75)
            used = memory.read().used
            stable = stable + 1 if abs(used - last) < 256 * 1024 ** 2 else 0
            last = used
            if stable >= 3:
                break
        self.resident = None

    async def _idle_housekeeping(self) -> None:
        mem_cfg = util.settings().get("memory") or {}
        if RESTART_REQUEST.exists():
            return
        m = memory.read()
        if (m.limited and m.fraction > float(mem_cfg.get("idle_unload_above", 0.85))
                and _now() - self._last_idle_free > 120 and not any(j["status"] == "running" for j in self.jobs.values())):
            log.info("idle memory at %.0f%%: unloading models", m.fraction * 100)
            self._last_idle_free = _now()
            await self._unload_all()

    async def _await_completion(self, job: dict[str, Any]) -> None:
        pid = job["prompt_id"]
        fut = asyncio.get_running_loop().create_future()
        self._done[pid] = fut
        dead_since = None
        try:
            while not fut.done():
                try:
                    await asyncio.wait_for(asyncio.shield(fut), timeout=10)
                    break
                except asyncio.TimeoutError:
                    pass
                # Fallback: the websocket may have dropped; ask ComfyUI directly.
                try:
                    hist = await self.comfy.history(pid)
                    dead_since = None
                except ComfyError:
                    hist = None
                    dead_since = dead_since or _now()
                    if _now() - dead_since > 90:
                        fut.set_result(("error", "ComfyUI stopped responding (it is being restarted)"))
                        self.resident = None
                        break
                if hist and (hist.get("status") or {}).get("completed") is not None:
                    st = hist["status"]
                    if st.get("status_str") == "success":
                        fut.set_result(("success", None))
                    else:
                        fut.set_result(("error", _history_error(hist)))
            outcome, detail = fut.result()
        finally:
            self._done.pop(pid, None)
        if outcome == "success":
            await self._finalize(job)
        elif outcome == "interrupted" or job.get("cancel_requested"):
            self._touch(job, status="cancelled", stage="", finished=_now())
        else:
            self._touch(job, status="failed", error=detail or "ComfyUI reported an error", stage="", finished=_now())
        self._titles.pop(job["id"], None)
        await self._after_job()

    async def _finalize(self, job: dict[str, Any]) -> None:
        hist = None
        for _ in range(5):
            try:
                hist = await self.comfy.history(job["prompt_id"])
            except ComfyError:
                hist = None
            if hist:
                break
            await asyncio.sleep(1)
        files = _output_files(hist or {})
        audio = [f for f in files if f.lower().endswith("-audio.mp4")]
        chosen = (audio or files or [None])[0]
        finished = _now()
        timings = {"total_s": round(finished - (job.get("started") or finished), 1)}
        if job.get("sampling_started") and job.get("sampling_done"):
            timings["sampling_s"] = round(job["sampling_done"] - job["sampling_started"], 1)
        if not chosen:
            self._touch(job, status="failed", error="ComfyUI finished but saved no video", stage="", finished=finished)
            return
        if audio and (util.settings().get("output") or {}).get("delete_silent_twin", True):
            twin = paths.OUTPUT / re.sub(r"(?i)-audio\.mp4$", ".mp4", chosen)
            try:
                twin.unlink()
            except OSError:
                pass
        record = self._record(job, timings, finished)
        try:
            library.write_sidecar(chosen, record)
        except (OSError, library.LibraryError) as exc:
            log.warning("sidecar write failed: %s", exc)
        await asyncio.to_thread(library.refresh_index)
        self._touch(job, status="done", stage="", output={"file": chosen}, finished=finished, timings=timings,
                    eta=None)

    def _record(self, job: dict[str, Any], timings: dict[str, Any], finished: float) -> dict[str, Any]:
        width, height = graph.resolution(job["aspect"], job["megapixels"])
        return {
            "studio": 1, "mode": job["mode"], "prompt_mode": job["prompt_mode"], "idea": job["idea"],
            "prompt": job["prompt"], "aspect": job["aspect"], "megapixels": job["megapixels"],
            "duration": job["duration"], "width": width, "height": height,
            "frames": graph.frame_count(job["duration"]), "seed": job["seed"], "checkpoint": job["checkpoint"],
            "recipe_id": job["recipe_id"], "recipe": job["recipe"], "overrides": job.get("overrides") or {},
            "loras": job["loras"], "lora_picks": job.get("lora_picks") or [], "start_image": job["start_image"], "end_image": job["end_image"],
            "refs": job["refs"], "reference_tags": graph.reference_tags(job["refs"]) if job["refs"] else [],
            "group": job.get("group"), "compare": job.get("compare"), "label": job.get("label"),
            "prompt_id": job.get("prompt_id"), "created": job["created"], "started": job.get("started"),
            "finished": finished, "timings": timings,
        }

    async def _after_job(self) -> None:
        mem_cfg = util.settings().get("memory") or {}
        m = memory.read()
        if m.limited and m.fraction > float(mem_cfg.get("trim_after_job_above", 0.70)):
            log.info("memory at %.0f%% after job: dropping cached outputs", m.fraction * 100)
            try:
                await self.comfy.free(unload_models=False, free_memory=True)
            except ComfyError:
                pass
        self._wake.set()

    async def _recover(self) -> None:
        """Jobs that were running when Studio stopped: collect them from ComfyUI's history."""
        for job in [j for j in self.jobs.values() if j["status"] == "running"]:
            pid = job.get("prompt_id")
            hist = None
            for _ in range(30):
                if await self.comfy.alive():
                    try:
                        hist = await self.comfy.history(pid) if pid else None
                    except ComfyError:
                        hist = None
                    break
                await asyncio.sleep(2)
            if hist and (hist.get("status") or {}).get("status_str") == "success":
                await self._finalize(job)
            elif hist:
                self._touch(job, status="failed", error=_history_error(hist), stage="")
            else:
                try:
                    q = await self.comfy.queue_state()
                    live = any(pid in str(item) for item in (q.get("queue_running") or []) + (q.get("queue_pending") or []))
                except ComfyError:
                    live = False
                if live:
                    await self._await_completion(job)
                else:
                    self._touch(job, status="failed", error="Studio restarted while this job was running", stage="")

    # ------------------------------------------------------------------ events

    def _job_for_pid(self, pid: str | None) -> dict[str, Any] | None:
        if not pid:
            return next((j for j in self.jobs.values() if j["status"] == "running"), None)
        return next((j for j in self.jobs.values() if j.get("prompt_id") == pid), None)

    async def _on_event(self, kind: str, data: dict[str, Any]) -> None:
        pid = data.get("prompt_id")
        if kind == "kj_preview_override":
            job = self._job_for_pid(None)
            if job and data.get("image"):
                seq = self.previews.get(job["id"], (0, b"", ""))[0] + 1
                try:
                    self.previews[job["id"]] = (seq, base64.b64decode(data["image"]), data.get("mime") or "image/jpeg")
                except ValueError:
                    pass
                step, total = data.get("step"), data.get("total")
                values: dict[str, Any] = {"stage": "sampling"}
                if step is not None:
                    values["step"] = int(step)
                if total:
                    values["total_steps"] = int(total)
                if data.get("avg_step_ms"):
                    values["avg_step_ms"] = float(data["avg_step_ms"])
                    remaining = max(0, (values.get("total_steps") or job.get("total_steps") or 0) - (step or 0))
                    values["eta"] = round(remaining * values["avg_step_ms"] / 1000 + _decode_estimate(job), 1)
                if not job.get("sampling_started"):
                    values["sampling_started"] = _now()
                self._touch(job, **values)
            return
        job = self._job_for_pid(pid)
        if kind == "executing" and job:
            node = data.get("node")
            if node is None:
                return
            ct, title = (self._titles.get(job["id"]) or {}).get(str(node), ("", ""))
            stage = STAGE_BY_CLASS.get(ct, title.lower() or "working")
            values = {"stage": stage}
            if ct in ("VAEDecode", "VAEDecodeAudio") and not job.get("sampling_done"):
                values["sampling_done"] = _now()
            self._touch(job, **values)
        elif kind == "progress" and job:
            ct, _ = (self._titles.get(job["id"]) or {}).get(str(data.get("node")), ("", ""))
            if ct == "SamplerCustomAdvanced":
                values = {"stage": "sampling", "step": int(data.get("value") or 0), "total_steps": int(data.get("max") or 0)}
                if not job.get("sampling_started"):
                    values["sampling_started"] = _now()
                self._touch(job, **values)
        elif kind in ("execution_success", "execution_error", "execution_interrupted") and pid in self._done:
            fut = self._done[pid]
            if not fut.done():
                if kind == "execution_success":
                    fut.set_result(("success", None))
                elif kind == "execution_interrupted":
                    fut.set_result(("interrupted", None))
                else:
                    fut.set_result(("error", _event_error(data)))
        elif kind == "studio_disconnected":
            for j in self.jobs.values():
                if j["status"] == "running":
                    self._touch(j, note="Lost connection to ComfyUI, waiting for it…")
        elif kind == "studio_connected":
            for j in self.jobs.values():
                if j["status"] == "running" and j.get("note", "").startswith("Lost connection"):
                    self._touch(j, note="")


def _decode_estimate(job: dict[str, Any]) -> float:
    """Rough seconds for VAE decode + save after sampling, from clip length."""
    return 8.0 + 2.5 * float(job.get("duration") or 5) * float(job.get("megapixels") or 0.7)


def _download_pct(rows: list[dict[str, Any]]) -> int | None:
    done = sum(int(r.get("done") or 0) for r in rows)
    total = sum(int(r.get("total") or 0) for r in rows)
    return int(100 * done / total) if total else None


def _output_files(hist: dict[str, Any]) -> list[str]:
    files: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            fn = x.get("filename")
            if isinstance(fn, str) and fn.lower().endswith(".mp4") and x.get("type", "output") == "output":
                sub = str(x.get("subfolder") or "").strip("/")
                files.append(f"{sub}/{fn}".strip("/"))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(hist.get("outputs") or {})
    return list(dict.fromkeys(files))


def _event_error(data: dict[str, Any]) -> str:
    msg = str(data.get("exception_message") or "").strip()
    node = data.get("node_type") or ""
    if "out of memory" in msg.lower() or "OutOfMemory" in str(data.get("exception_type")):
        msg = "GPU out of memory. Try a shorter clip or lower quality. " + msg[:200]
    return (f"{node}: {msg}" if node else msg)[:800] or "ComfyUI reported an error"


def _history_error(hist: dict[str, Any]) -> str:
    for m in (hist.get("status") or {}).get("messages") or []:
        if isinstance(m, list) and len(m) == 2 and m[0] == "execution_error":
            return _event_error(m[1])
    return "ComfyUI reported an error"
