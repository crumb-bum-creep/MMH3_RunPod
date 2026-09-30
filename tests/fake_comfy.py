"""A stand-in ComfyUI for tests and local UI work.

Speaks the parts of ComfyUI's API that Studio uses: POST /prompt, GET /history,
/queue, /object_info, /system_stats, POST /free, /interrupt, and the /ws event
stream (execution_start, executing, progress, kj_preview_override,
execution_success / _error / _interrupted). Each prompt "renders" a short real
mp4 with audio via ffmpeg, named the way VideoHelperSuite names files.

    python tests/fake_comfy.py --port 8188 --output /tmp/ws/ComfyUI/output [--step 0.3]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import re
import subprocess
import uuid
from pathlib import Path

from aiohttp import WSMsgType, web

SAMPLERS = ["euler", "euler_ancestral", "dpmpp_2m", "res_multistep", "seeds_2", "lcm"]
SCHEDULERS = ["simple", "normal", "karras", "beta", "sgm_uniform"]


class Fake:
    def __init__(self, output: Path, step_s: float, fail_word: str) -> None:
        self.output = output
        self.step_s = step_s
        self.fail_word = fail_word
        self.clients: dict[str, set[web.WebSocketResponse]] = {}
        self.history: dict[str, dict] = {}
        self.queue: list[tuple[str, dict, str]] = []
        self.running: str | None = None
        self.interrupt = False
        self.counter = 0
        self.frees: list[dict] = []
        self.prompts: list[dict] = []
        self.wake = asyncio.Event()

    async def send(self, client: str, kind: str, data: dict) -> None:
        for ws in list(self.clients.get(client, ())):
            try:
                await ws.send_str(json.dumps({"type": kind, "data": data}))
            except Exception:
                pass

    async def worker(self) -> None:
        while True:
            if not self.queue:
                self.wake.clear()
                await self.wake.wait()
                continue
            pid, prompt, client = self.queue.pop(0)
            self.running, self.interrupt = pid, False
            try:
                await self.execute(pid, prompt, client)
            finally:
                self.running = None

    async def execute(self, pid: str, prompt: dict, client: str) -> None:
        await self.send(client, "execution_start", {"prompt_id": pid})
        order = sorted(prompt, key=int)
        sampler = next((n for n in order if prompt[n]["class_type"] == "SamplerCustomAdvanced"), None)
        steps = next((prompt[n]["inputs"].get("steps") for n in order if prompt[n]["class_type"] == "BasicScheduler"), 8)
        text = json.dumps(prompt)
        status = "success"
        outputs: dict = {}
        messages = [["execution_start", {"prompt_id": pid}]]
        for nid in order:
            ct = prompt[nid]["class_type"]
            await self.send(client, "executing", {"node": nid, "prompt_id": pid})
            await asyncio.sleep(0.05)
            if nid == sampler:
                for s in range(1, steps + 1):
                    if self.interrupt:
                        break
                    await asyncio.sleep(self.step_s)
                    await self.send(client, "progress", {"value": s, "max": steps, "node": nid, "prompt_id": pid})
                    await self.send(client, "kj_preview_override", {
                        "node_id": nid, "image": _frame(s, steps), "mime": "image/jpeg", "step": s, "total": steps,
                        "avg_step_ms": self.step_s * 1000})
                if self.fail_word and self.fail_word in text:
                    err = {"prompt_id": pid, "node_id": nid, "node_type": ct,
                           "exception_message": "Allocation on device failed: CUDA out of memory.", "exception_type": "torch.OutOfMemoryError"}
                    await self.send(client, "execution_error", err)
                    messages.append(["execution_error", err])
                    status = "error"
                    break
            if self.interrupt:
                await self.send(client, "execution_interrupted", {"prompt_id": pid})
                messages.append(["execution_interrupted", {"prompt_id": pid}])
                status = "error"
                break
            if ct == "VHS_VideoCombine":
                prefix = prompt[nid]["inputs"]["filename_prefix"]
                outputs[nid] = {"gifs": self.render(prefix, steps)}
        if status == "success":
            await self.send(client, "executing", {"node": None, "prompt_id": pid})
            await self.send(client, "execution_success", {"prompt_id": pid})
            messages.append(["execution_success", {"prompt_id": pid}])
        self.history[pid] = {"prompt": [0, pid, prompt, {}, []], "outputs": outputs,
                             "status": {"status_str": status, "completed": status == "success", "messages": messages}}

    def render(self, prefix: str, steps: int) -> list[dict]:
        sub, _, base = prefix.rpartition("/")
        folder = self.output / sub
        folder.mkdir(parents=True, exist_ok=True)
        # Same counter rule as VideoHelperSuite: max existing <base>_<n>... + 1
        matcher = re.compile(rf"{re.escape(base)}_(\d+)\D*\..+", re.IGNORECASE)
        n = 1 + max([int(m.group(1)) for f in folder.iterdir() if (m := matcher.fullmatch(f.name))] or [0])
        stem = folder / f"{base}_{n:05d}"
        hue = (n * 67) % 360
        vf = f"hue=h={hue}:s=1.6,drawtext=text='{base} {n}':fontsize=48:fontcolor=white:x=(w-tw)/2:y=h*0.8"
        common = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=24:duration=2"]
        subprocess.run(common + ["-vf", vf, "-pix_fmt", "yuv420p", f"{stem}.mp4"], check=False)
        subprocess.run(common + ["-f", "lavfi", "-i", "sine=frequency=330:duration=2", "-vf", vf, "-pix_fmt", "yuv420p",
                                 "-shortest", f"{stem}-audio.mp4"], check=False)
        if subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", f"{stem}.mp4", "-frames:v", "1", f"{stem}.png"]).returncode:
            # drawtext needs fontconfig; fall back to no text
            pass
        return [{"filename": f"{stem.name}-audio.mp4", "subfolder": sub, "type": "output", "format": "video/h264-mp4",
                 "frame_rate": 24, "fullpath": f"{stem}-audio.mp4"}]


def _frame(step: int, total: int) -> str:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (216, 384), (20, 26, 34))
    d = ImageDraw.Draw(im)
    for y in range(0, 384, 4):
        v = int(40 + 180 * (y / 384) * step / total)
        d.line([(0, y), (216, y)], fill=(v, int(v * 0.7), 60 + step * 10))
    d.ellipse([60, 120, 156, 216], fill=(240, 169, 59))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()


def make_app(fake: Fake) -> web.Application:
    app = web.Application()

    async def stats(_):
        return web.json_response({"system": {"comfyui_version": "0.36.0-fake"}, "devices": []})

    async def object_info(_):
        return web.json_response({
            "KSamplerSelect": {"input": {"required": {"sampler_name": [SAMPLERS]}}},
            "BasicScheduler": {"input": {"required": {"scheduler": ["COMBO", {"options": SCHEDULERS}]}}},
        })

    async def prompt(request):
        body = await request.json()
        g = body.get("prompt") or {}
        for nid, n in g.items():
            if not re.fullmatch(r"\d+", nid) or "class_type" not in n:
                return web.json_response({"error": {"message": "bad node"}, "node_errors": {}}, status=400)
        pid = uuid.uuid4().hex
        fake.prompts.append(g)
        fake.queue.append((pid, g, body.get("client_id", "")))
        fake.wake.set()
        return web.json_response({"prompt_id": pid, "number": len(fake.prompts), "node_errors": {}})

    async def history(request):
        pid = request.match_info["pid"]
        return web.json_response({pid: fake.history[pid]} if pid in fake.history else {})

    async def queue_get(_):
        return web.json_response({"queue_running": [[0, fake.running]] if fake.running else [],
                                  "queue_pending": [[i, p] for i, (p, _, _) in enumerate(fake.queue)]})

    async def queue_post(request):
        body = await request.json()
        fake.queue = [q for q in fake.queue if q[0] not in (body.get("delete") or [])]
        return web.json_response({})

    async def free(request):
        fake.frees.append(await request.json())
        return web.json_response({})

    async def interrupt(_):
        fake.interrupt = True
        return web.json_response({})

    async def debug(_):
        return web.json_response({"frees": fake.frees, "prompts": len(fake.prompts)})

    async def ws(request):
        sock = web.WebSocketResponse(heartbeat=20)
        await sock.prepare(request)
        cid = request.query.get("clientId", "")
        fake.clients.setdefault(cid, set()).add(sock)
        await sock.send_str(json.dumps({"type": "status", "data": {"sid": cid}}))
        async for msg in sock:
            if msg.type == WSMsgType.CLOSE:
                break
        fake.clients[cid].discard(sock)
        return sock

    async def start(_app):
        _app["worker"] = asyncio.create_task(fake.worker())

    app.router.add_get("/system_stats", stats)
    app.router.add_get("/object_info", object_info)
    app.router.add_post("/prompt", prompt)
    app.router.add_get("/history/{pid}", history)
    app.router.add_get("/queue", queue_get)
    app.router.add_post("/queue", queue_post)
    app.router.add_post("/free", free)
    app.router.add_post("/interrupt", interrupt)
    app.router.add_get("/_debug", debug)
    app.router.add_get("/ws", ws)
    app.on_startup.append(start)
    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8188)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--step", type=float, default=0.3)
    ap.add_argument("--fail-word", default="FAILME")
    a = ap.parse_args()
    web.run_app(make_app(Fake(a.output, a.step, a.fail_word)), host="127.0.0.1", port=a.port, access_log=None)


if __name__ == "__main__":
    main()
