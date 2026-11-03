"""MMH3 Prompt Lab: workshop MiniMax H3 prompts on your own computer, no GPU.

    python prompt_lab.py            (Python 3.9+, nothing to install)

Opens a small page in your browser. Pick Text / Image / Reference, add your
frames or references, set the length, write an idea, and Draft. Edit the
result, Tweak it with a short request, and Save the ones you like. Saved
prompts live in prompt_lab_data/saved.json next to this file; copy one into
Studio's Create on the pod when you're ready to render.

It asks OpenRouter exactly the way Studio does: the same system prompts, the
same frame/duration block, the same reference tags. Your OpenRouter key comes
from OPENROUTER_API_KEY or is pasted into the page once (stored next to this
file, never sent anywhere but OpenRouter).

Options: --port 7878   --no-browser
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(__file__).resolve().parent / "prompt_lab_data"
SETTINGS = DATA / "settings.json"
SAVED = DATA / "saved.json"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3-flash-preview"
FPS = 24
ASPECTS = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "2:3": (2, 3), "3:2": (3, 2),
           "3:4": (3, 4), "4:3": (4, 3), "21:9": (21, 9)}
MAX_REFS = {"image": 9, "video": 3, "audio": 3}
_lock = threading.Lock()


# ----------------------------------------------------------------------------- storage

def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def settings() -> dict:
    s = _read(SETTINGS, {}) or {}
    s.setdefault("model", DEFAULT_MODEL)
    s.setdefault("reasoning_effort", "medium")
    return s


def api_key() -> str:
    return (os.environ.get("OPENROUTER_API_KEY") or settings().get("key") or "").strip()


# ----------------------------------------------------------------------------- MiniMax H3 format
# The same rules Studio uses on the pod (studio/graph.py, studio/prompting.py).

def frame_count(seconds: float) -> int:
    n = max(5, round(seconds * FPS))
    return n + (5 - n % 17) % 17


def clip_seconds(job: dict) -> float:
    return frame_count(float(job.get("duration", 5))) / FPS


def resolution(aspect: str, megapixels: float, multiple: int = 32) -> tuple[int, int]:
    w, h = ASPECTS.get(aspect, (9, 16))
    scale = math.sqrt(megapixels * 1024 * 1024 / (w * h))
    return round(w * scale / multiple) * multiple, round(h * scale / multiple) * multiple


def keyframe_task(job: dict) -> str | None:
    if job.get("mode") != "i2v":
        return None
    start, end = bool(job.get("start")), bool(job.get("end"))
    return "fl2v" if start and end else "l2v" if end else "i2v"


def alignment_line(job: dict, last_shot="1") -> str:
    task, secs = keyframe_task(job), f"{clip_seconds(job):.2f}"
    if task == "i2v":
        return "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    if task == "fl2v":
        return ("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the "
                f"0.00-second mark of the target video; Picture 2 (from Shot {last_shot}) aligns with the "
                f"{secs}-second mark of the target video.")
    if task == "l2v":
        return ("How the reference pictures align with the target video — <Picture 1> (from [Shot "
                f"{last_shot}]) aligns with the {secs}-second mark of the target video.")
    return ""


_ALIGN = re.compile(r"\A\s*(?:For the target video, at 0\.00 seconds|How the reference pictures align)[^\n]*\n*", re.IGNORECASE)
_SHOT = re.compile(r"\[Shot (\d+)\]", re.IGNORECASE)


def align_keyframes(job: dict, prompt: str) -> str:
    """Image mode: put the right alignment line (shots, seconds) at the top of a structured prompt."""
    text = str(prompt or "")
    if not keyframe_task(job):
        return text
    had = bool(_ALIGN.match(text))
    body = _ALIGN.sub("", text, count=1).lstrip()
    if not had and "integrated_multimodal_description:" not in body:
        return text
    shots = [int(n) for n in _SHOT.findall(body)]
    return f"{alignment_line(job, max(shots) if shots else 1)}\n\n{body}"


def format_block(job: dict) -> str:
    width, height = resolution(job.get("aspect", "9:16"), float(job.get("megapixels", 0.7)))
    orient = "portrait" if height > width else "landscape" if width > height else "square"
    lines = [f"duration: {clip_seconds(job):.2f} seconds",
             f"frame: {width} x {height} ({job.get('aspect', '9:16')}, {orient})"]
    task = keyframe_task(job)
    if task == "i2v":
        lines.append("alignment line (your first line, verbatim): " + alignment_line(job))
    elif task:
        lines.append("alignment line (your first line; N = the number of your final shot): " + alignment_line(job, "N"))
    return "TARGET FORMAT:\n" + "\n".join(lines)


def reference_tags(refs: list[dict]) -> list[str]:
    """MiniMax's tags: pictures and videos numbered within their kind; <Audio N> runs across
    video soundtracks first, then standalone audio."""
    tags, pic, vid, audio = [""] * len(refs), 0, 0, 0
    for i, r in enumerate(refs):
        if r.get("kind") == "image":
            pic += 1
            tags[i] = f"<Picture {pic}>"
    for i, r in enumerate(refs):
        if r.get("kind") == "video":
            vid += 1
            tags[i] = f"<Video {vid}>"
            if r.get("use_soundtrack", True):
                audio += 1
                tags[i] += f" <Audio {audio}>"
    for i, r in enumerate(refs):
        if r.get("kind") == "audio":
            audio += 1
            tags[i] = f"<Audio {audio}>"
    return tags


def _image(data_url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": data_url}}  # already a ≤1536px JPEG (resized in the page)


def _keyframes(job: dict) -> list[dict]:
    task, secs, parts = keyframe_task(job), f"{clip_seconds(job):.2f}", []
    frames = {"i2v": [("<Picture 1>, the first frame (0.00 s):", "start")],
              "fl2v": [("Picture 1, the first frame (0.00 s):", "start"), (f"Picture 2, the last frame ({secs} s):", "end")],
              "l2v": [(f"<Picture 1>, the last frame ({secs} s):", "end")]}.get(task, [])
    for label, key in frames:
        parts += [{"type": "text", "text": label}, _image(job[key]["data"])]
    return parts


def _r2v_content(job: dict) -> list[dict]:
    """The layout MiniMaxRefPack's prompt writer sends OpenRouter: a manifest, then each
    reference labelled with its tag. Videos go whole (soundtrack inside), audio inline."""
    refs = job.get("refs") or []
    for kind, most in MAX_REFS.items():
        if sum(r.get("kind") == kind for r in refs) > most:
            raise ValueError(f"at most {most} {kind} references")
    tags = reference_tags(refs)
    order = [i for k in ("image", "video", "audio") for i, r in enumerate(refs) if r.get("kind") == k]
    manifest, parts = [], []
    for i in order:
        r, tag, name = refs[i], tags[i], refs[i].get("name") or "file"
        manifest.append(f"{tag}: {name}")
        if r["kind"] == "image":
            parts += [{"type": "text", "text": f"image_reference {tag} ({name}) - the next image:"}, _image(r["data"])]
        elif r["kind"] == "video":
            video_tag, _, audio_tag = tag.partition("> ")  # "<Video 1> <Audio 1>" or just "<Video 1>"
            video_tag = video_tag if not audio_tag else video_tag + ">"
            parts += [{"type": "text", "text": f"video_reference {video_tag} ({name}) - the next video, whole:"},
                      {"type": "video_url", "video_url": {"url": r["data"]}}]
            if audio_tag:
                manifest.append(f"  ({audio_tag}: the soundtrack inside {video_tag})")
        else:
            fmt = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav", "audio/mpeg": "mp3", "audio/mp3": "mp3"}.get(
                r["data"].split(";")[0][5:])
            parts.append({"type": "text", "text": f"audio_reference {tag} ({name}) - the next audio clip:"})
            parts.append({"type": "input_audio", "input_audio": {"data": r["data"].split(",", 1)[1], "format": fmt}}
                         if fmt else {"type": "text", "text": f"{tag}: audio reference {name} (only wav and mp3 are sent)"})
    width, height = resolution(job.get("aspect", "9:16"), float(job.get("megapixels", 0.7)))
    g = math.gcd(width, height)
    text = (f"USER DIRECTION:\n{job.get('idea', '')}\n\nTARGET FORMAT:\n"
            f"frame: {width} x {height} (aspect ratio {width // g}:{height // g})\n"
            f"duration: {float(job.get('duration', 5)):.3f} seconds")
    if manifest:
        text += "\n\nReference manifest:\n" + "\n".join(manifest)
    return [{"type": "text", "text": text}] + parts


def _rules(job: dict) -> str:
    mode = job.get("mode")
    if mode == "i2v":
        return PROMPTS.get(f"{keyframe_task(job)}_auto") or PROMPTS["i2v_auto"]
    return PROMPTS[f"{mode}_auto"]


def _check(job: dict) -> None:
    mode = job.get("mode")
    if mode not in ("t2v", "i2v", "r2v"):
        raise ValueError("pick Text, Image or Reference")
    if mode == "i2v" and not (job.get("start") or job.get("end")):
        raise ValueError("add a start frame, an end frame, or both")


def draft(job: dict) -> str:
    _check(job)
    if not str(job.get("idea") or "").strip():
        raise ValueError("write an idea first")
    if job["mode"] == "r2v":
        content = _r2v_content(job)
    else:
        content = [{"type": "text", "text": f"USER DIRECTION:\n{job['idea']}\n\n{format_block(job)}"}] + _keyframes(job)
    return align_keyframes(job, _openrouter([{"role": "system", "content": _rules(job)},
                                             {"role": "user", "content": content}]))


def tweak(job: dict) -> str:
    _check(job)
    prompt, request = str(job.get("prompt") or "").strip(), str(job.get("request") or "").strip()
    if not prompt:
        raise ValueError("there's no prompt to tweak yet")
    if not request:
        raise ValueError("say what to change")
    system = PROMPTS["refine"] + "\n\n# PROMPT RULES\n\n" + _rules(job)
    content = [{"type": "text", "text": f"CHANGE REQUEST:\n{request}\n\n{format_block(job)}\n\nCURRENT PROMPT:\n{prompt}"}]
    if job["mode"] == "i2v":
        content += _keyframes(job)
    elif job["mode"] == "r2v":
        refs = job.get("refs") or []
        for tag, r in zip(reference_tags(refs), refs):
            if r.get("kind") == "image":
                content += [{"type": "text", "text": f"image_reference {tag} - the next image:"}, _image(r["data"])]
            else:
                content.append({"type": "text", "text": f"{tag}: a {r.get('kind')} reference (not attached here)"})
    return align_keyframes(job, _openrouter([{"role": "system", "content": system}, {"role": "user", "content": content}]))


def _openrouter(messages: list[dict]) -> str:
    key = api_key()
    if not key:
        raise ValueError("add your OpenRouter key (top right)")
    s = settings()
    payload = {"model": s["model"], "messages": messages, "max_tokens": 8192}
    if s.get("reasoning_effort") in ("none", "low", "medium", "high"):
        payload["reasoning"] = {"effort": s["reasoning_effort"]}
    req = urllib.request.Request(OPENROUTER_URL, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "MMH3 Prompt Lab"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            body = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = (json.loads(e.read() or b"{}").get("error") or {}).get("message")
        except ValueError:
            msg = None
        raise ValueError("OpenRouter rejected the key" if e.code == 401 else f"OpenRouter {e.code}: {msg or e.reason}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise ValueError(f"can't reach OpenRouter ({getattr(e, 'reason', e)})") from None
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ValueError(f"OpenRouter returned no text: {str(body)[:200]}") from None
    if isinstance(text, list):
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    text = str(text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rstrip().removesuffix("```").strip()
    if not text:
        raise ValueError("OpenRouter returned an empty prompt; try again")
    return text


# ----------------------------------------------------------------------------- saved prompts

def saved() -> list[dict]:
    return _read(SAVED, []) or []


def save(item: dict) -> dict:
    keep = {k: item.get(k) for k in ("title", "mode", "idea", "prompt", "aspect", "megapixels", "duration")}
    keep["references"] = item.get("references") or []  # file names + tags, so you know what to upload on the pod
    keep["title"] = str(keep.get("title") or "").strip() or (str(keep.get("idea") or keep.get("prompt") or "Untitled")[:60])
    with _lock:
        items = saved()
        old = next((s for s in items if s.get("id") == item.get("id")), None)
        if old:
            old.update(keep, updated=time.time())
            out = old
        else:
            out = {"id": uuid.uuid4().hex[:10], "created": time.time(), "updated": time.time(), **keep}
            items.insert(0, out)
        _write(SAVED, items)
    return out


def delete(item_id: str) -> None:
    with _lock:
        _write(SAVED, [s for s in saved() if s.get("id") != item_id])


# ----------------------------------------------------------------------------- web

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body, ctype="application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            s = settings()
            return self._send(200, {"key": bool(api_key()), "key_from_env": bool(os.environ.get("OPENROUTER_API_KEY")),
                                    "model": s["model"], "effort": s["reasoning_effort"], "saved": saved(),
                                    "aspects": list(ASPECTS), "data": str(DATA)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = self._body()
            if self.path == "/api/draft":
                return self._send(200, {"prompt": draft(body)})
            if self.path == "/api/tweak":
                return self._send(200, {"prompt": tweak(body)})
            if self.path == "/api/saved":
                return self._send(200, save(body))
            if self.path == "/api/settings":
                s = settings()
                for k in ("key", "model", "reasoning_effort"):
                    if k in body and isinstance(body[k], str):
                        s[k] = body[k].strip() or (DEFAULT_MODEL if k == "model" else s.get(k))
                _write(SETTINGS, s)
                return self._send(200, {"ok": True})
            if self.path == "/api/tags":
                return self._send(200, {"tags": reference_tags(body.get("refs") or [])})
            self._send(404, {"error": "not found"})
        except ValueError as e:
            self._send(400, {"error": str(e)})

    def do_DELETE(self):
        if self.path.startswith("/api/saved/"):
            delete(self.path.rsplit("/", 1)[1])
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


def main() -> None:
    ap = argparse.ArgumentParser(description="MMH3 Prompt Lab")
    ap.add_argument("--port", type=int, default=7878)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"MMH3 Prompt Lab on {url}  (saved prompts: {SAVED})  Ctrl+C to stop")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MMH3 Prompt Lab</title>
<style>
:root{--bg:#0f1319;--card:#171d26;--line:#263040;--ink:#e8edf4;--ink2:#9aa7b8;--ink3:#6b7889;--acc:#f0a93b;--bad:#ef6b6b;--ok:#5bc38a;--r:14px}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:rgba(15,19,25,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px;padding:12px 16px}
header h1{font-size:17px;margin:0;letter-spacing:.2px}header h1 span{color:var(--ink3);font-weight:500}
.spacer{flex:1}
main{max-width:1180px;margin:0 auto;padding:16px;display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px}
@media (max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:16px;margin-bottom:16px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink2);margin:0 0 10px}
label.l{display:block;font-size:13px;color:var(--ink2);margin:12px 0 6px}
textarea,input[type=text],input[type=password],select{width:100%;background:#0f141b;color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit}
textarea{resize:vertical;min-height:90px}textarea#prompt{min-height:320px;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
textarea:focus,input:focus,select:focus{outline:2px solid rgba(240,169,59,.5);border-color:transparent}
.seg{display:flex;background:#0f141b;border:1px solid var(--line);border-radius:12px;padding:3px;gap:3px}
.seg button{flex:1;background:none;border:0;color:var(--ink2);padding:9px;border-radius:9px;font:inherit;font-weight:600;cursor:pointer}
.seg button.on{background:var(--line);color:var(--ink)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.row>*{flex:1;min-width:120px}
.btn{background:#222b38;color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 14px;font:inherit;font-weight:600;cursor:pointer;white-space:nowrap}
.btn:hover{border-color:#3a475a}.btn.primary{background:var(--acc);border-color:var(--acc);color:#1b1305}.btn.small{padding:6px 10px;font-size:13px}
.btn.ghost{background:none}.btn.danger{color:var(--bad)}.btn:disabled{opacity:.5;cursor:default}
.frames{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.drop{position:relative;border:1.5px dashed #344156;border-radius:12px;min-height:130px;display:grid;place-items:center;text-align:center;color:var(--ink3);font-size:13px;cursor:pointer;overflow:hidden;background:#0f141b}
.drop img{width:100%;height:100%;object-fit:contain;max-height:220px;display:block}
.drop .x,.ref .x{position:absolute;top:6px;right:6px;background:rgba(0,0,0,.6);color:#fff;border:0;border-radius:99px;width:26px;height:26px;cursor:pointer}
.refs{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px;margin-top:10px}
.ref{position:relative;background:#0f141b;border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:12px}
.ref .media{position:relative;height:96px;display:grid;place-items:center;background:#0a0e14;color:var(--ink3);cursor:pointer}
.ref .rep{position:absolute;left:6px;bottom:6px;font-size:11px;font-weight:600;background:rgba(0,0,0,.65);color:#fff;padding:2px 8px;border-radius:99px}
.ref .media img,.ref .media video{width:100%;height:96px;object-fit:cover}
.ref .meta{padding:6px 8px}.ref .tag{color:var(--acc);font-weight:700}.ref .name{color:var(--ink3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ref label{display:flex;gap:6px;align-items:center;margin-top:4px;color:var(--ink2)}
.dur{display:flex;align-items:center;gap:12px}.dur input{flex:1;accent-color:var(--acc)}.dur b{min-width:88px;text-align:right}
.note{font-size:12.5px;color:var(--ink3);margin:8px 0 0}.warn{color:#f3c27a}
.saved{display:flex;flex-direction:column;gap:10px}
.item{border:1px solid var(--line);border-radius:12px;padding:10px 12px;background:#0f141b}
.item h3{font-size:14px;margin:0 0 2px}.item .sub{font-size:12px;color:var(--ink3)}
.item .acts{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.item.cur{border-color:var(--acc)}
.mode{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;border-radius:99px;background:#233046;color:#9cc3ff;margin-right:6px}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:#222b38;border:1px solid var(--line);padding:10px 16px;border-radius:12px;opacity:0;transition:.2s;pointer-events:none;max-width:90vw;z-index:20}
#toast.on{opacity:1}#toast.bad{border-color:var(--bad);color:#ffd2d2}
.busy{position:relative;color:transparent!important}.busy::after{content:"";position:absolute;inset:0;margin:auto;width:16px;height:16px;border:2px solid #1b1305;border-right-color:transparent;border-radius:50%;animation:s .7s linear infinite}
@keyframes s{to{transform:rotate(360deg)}}
dialog{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:var(--r);width:min(460px,92vw);padding:18px}
dialog::backdrop{background:rgba(0,0,0,.6)}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;background:var(--bad)}.dot.ok{background:var(--ok)}
.hidden{display:none!important}
</style></head>
<body>
<header><h1>MMH3 <span>Prompt Lab</span></h1><span class="spacer"></span>
  <button class="btn small ghost" id="keyBtn"><span class="dot" id="keyDot"></span> OpenRouter</button></header>
<main>
<section>
  <div class="card">
    <div class="seg" id="modes"><button data-m="t2v">Text</button><button data-m="i2v">Image</button><button data-m="r2v">Reference</button></div>

    <div id="framesBox" class="hidden">
      <label class="l">Frames <span class="note" style="margin:0">— start, end, or both</span></label>
      <div class="frames">
        <div class="drop" data-frame="start">Start frame<br>tap or drop an image</div>
        <div class="drop" data-frame="end">End frame<br>tap or drop an image</div>
      </div>
    </div>

    <div id="refsBox" class="hidden">
      <label class="l">References <span class="note" style="margin:0">— up to 9 images, 3 videos, 3 audio</span></label>
      <div class="drop" id="refDrop" style="min-height:70px">Tap or drop images, videos or audio</div>
      <div class="refs" id="refs"></div>
      <p class="note">Write the tags into your idea (e.g. “&lt;Picture 1&gt; walks into &lt;Picture 2&gt;”). Videos are sent whole, so keep them short.</p>
    </div>

    <label class="l" for="idea">Idea</label>
    <textarea id="idea" placeholder="What happens, who's in it, how it sounds…"></textarea>

    <div class="row" style="margin-top:4px">
      <div><label class="l">Aspect</label><select id="aspect"></select></div>
      <div><label class="l">Size</label><select id="mp">
        <option value="0.5">0.5 MP</option><option value="0.7">0.7 MP</option><option value="0.9">0.9 MP</option><option value="1.2">1.2 MP</option></select></div>
    </div>
    <label class="l">Length</label>
    <div class="dur"><input type="range" id="dur" min="1" max="15" step="1"><b id="durLabel"></b></div>
    <p class="note" id="fmt"></p>

    <div class="row" style="margin-top:14px"><button class="btn primary" id="draftBtn">Draft prompt</button></div>
  </div>

  <div class="card">
    <div class="row" style="margin-bottom:8px"><h2 style="margin:0">Prompt</h2><span class="spacer"></span>
      <div style="flex:0;display:flex;gap:6px"><button class="btn small" id="undoBtn" disabled>Undo</button><button class="btn small" id="copyBtn">Copy</button></div></div>
    <textarea id="prompt" placeholder="Draft one from your idea, or paste / write a prompt here."></textarea>
    <div class="row" style="margin-top:10px">
      <input type="text" id="tweakReq" placeholder="Tweak: “make it night”, “add a second shot”, “slower camera”…" style="flex:3">
      <button class="btn" id="tweakBtn" style="flex:0">Tweak</button>
    </div>
    <div class="row" style="margin-top:12px">
      <input type="text" id="title" placeholder="Name (optional)" style="flex:3">
      <button class="btn primary" id="saveBtn" style="flex:0">Save</button>
      <button class="btn hidden" id="saveNewBtn" style="flex:0">Save as new</button>
    </div>
    <p class="note" id="loadedNote"></p>
  </div>
</section>

<aside>
  <div class="card"><div class="row" style="margin-bottom:8px"><h2 style="margin:0">Saved</h2><span class="spacer"></span>
    <button class="btn small ghost" id="newBtn" style="flex:0">New</button></div>
    <div class="saved" id="saved"></div>
    <p class="note" id="dataNote"></p>
  </div>
</aside>
</main>

<dialog id="keyDlg"><form method="dialog">
  <h2>OpenRouter</h2>
  <label class="l">API key</label><input type="password" id="keyIn" placeholder="sk-or-…" autocomplete="off">
  <p class="note" id="keyNote"></p>
  <label class="l">Model</label><input type="text" id="modelIn">
  <label class="l">Reasoning</label><select id="effortIn"><option>none</option><option>low</option><option>medium</option><option>high</option></select>
  <div class="row" style="margin-top:16px"><button class="btn ghost" value="cancel">Cancel</button><button class="btn primary" id="keySave" value="ok">Save</button></div>
</form></dialog>
<input type="file" id="picker" hidden>
<div id="toast"></div>

<script>
const $ = (s, el = document) => el.querySelector(s), $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ASPECT_RATIO = { "9:16": [9, 16], "16:9": [16, 9], "1:1": [1, 1], "2:3": [2, 3], "3:2": [3, 2], "3:4": [3, 4], "4:3": [4, 3], "21:9": [21, 9] };
const MAX = { image: 9, video: 3, audio: 3 };
const F = Object.assign({ mode: "t2v", idea: "", aspect: "9:16", megapixels: 0.7, duration: 5, prompt: "", title: "", id: null, references: [] },
  JSON.parse(localStorage.getItem("lab.form") || "{}"));
const media = { start: null, end: null, refs: [] };  // files live only in this tab
let undo = [], S = {};

function toast(msg, bad) { const t = $("#toast"); t.textContent = msg; t.className = "on" + (bad ? " bad" : ""); clearTimeout(t._h); t._h = setTimeout(() => t.className = "", bad ? 5000 : 2200); }
async function api(path, body, method) {
  const r = await fetch(path, { method: method || (body ? "POST" : "GET"), headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}
function persist() { const { ...keep } = F; localStorage.setItem("lab.form", JSON.stringify(keep)); }

// H3 snaps clips to 17n+5 frames at 24 fps; same math as the pod.
const frames = (s) => { let n = Math.max(5, Math.round(s * 24)); return n + ((5 - n % 17) % 17 + 17) % 17; };
function res(aspect, mp) { const [w, h] = ASPECT_RATIO[aspect]; const sc = Math.sqrt(mp * 1048576 / (w * h)); return [Math.round(w * sc / 32) * 32, Math.round(h * sc / 32) * 32]; }

function tags() {
  const t = media.refs.map(() => ""); let p = 0, v = 0, a = 0;
  media.refs.forEach((r, i) => { if (r.kind === "image") t[i] = `<Picture ${++p}>`; });
  media.refs.forEach((r, i) => { if (r.kind === "video") { t[i] = `<Video ${++v}>`; if (r.use_soundtrack) t[i] += ` <Audio ${++a}>`; } });
  media.refs.forEach((r, i) => { if (r.kind === "audio") t[i] = `<Audio ${++a}>`; });
  return t;
}

function render() {
  $$("#modes button").forEach((b) => b.classList.toggle("on", b.dataset.m === F.mode));
  $("#framesBox").classList.toggle("hidden", F.mode !== "i2v");
  $("#refsBox").classList.toggle("hidden", F.mode !== "r2v");
  $("#idea").value = F.idea; $("#aspect").value = F.aspect; $("#mp").value = String(F.megapixels);
  $("#dur").value = F.duration; $("#prompt").value = F.prompt; $("#title").value = F.title || "";
  renderDur(); renderFrames(); renderRefs(); renderSaved();
  $("#undoBtn").disabled = !undo.length;
  $("#saveBtn").textContent = F.id ? "Update" : "Save";
  $("#saveNewBtn").classList.toggle("hidden", !F.id);
  const need = (F.references || []).filter((r) => !media.refs.some((m) => m.name === r.name) && ![media.start, media.end].some((m) => m && m.name === r.name));
  $("#loadedNote").innerHTML = need.length ? `<span class="warn">This prompt used: ${need.map((r) => `${esc(r.tag || r.role || "")} ${esc(r.name)}`).join(", ")}. Add the files again to draft or tweak with them.</span>` : "";
}
function renderDur() {
  const fc = frames(F.duration), [w, h] = res(F.aspect, +F.megapixels);
  $("#durLabel").textContent = `${(fc / 24).toFixed(2)} s`;
  $("#fmt").textContent = `${fc} frames · ${w} × ${h}`;
}
function renderFrames() {
  $$("[data-frame]").forEach((d) => {
    const m = media[d.dataset.frame];
    d.innerHTML = m ? `<img src="${m.data}" alt=""><button class="x" title="Remove">✕</button>` : `${d.dataset.frame === "start" ? "Start" : "End"} frame<br>tap or drop an image`;
    const x = $(".x", d); if (x) x.onclick = (e) => { e.stopPropagation(); media[d.dataset.frame] = null; render(); };
  });
}
function renderRefs() {
  const t = tags();
  $("#refs").innerHTML = media.refs.map((r, i) => `<div class="ref" data-ref="${i}">
      <div class="media" data-rep="${i}" title="Tap or drop a file to replace ${esc(t[i])}">${r.kind === "image" ? `<img src="${r.data}" alt="">` : r.kind === "video" ? `<video src="${r.data}" muted playsinline></video>` : "♪ audio"}<span class="rep">↻ Replace</span></div>
      <button class="x" data-rm="${i}" title="Remove">✕</button>
      <div class="meta"><div class="tag">${esc(t[i])}</div><div class="name" title="${esc(r.name)}">${esc(r.name)}</div>
      ${r.kind === "video" ? `<label><input type="checkbox" data-snd="${i}" ${r.use_soundtrack ? "checked" : ""}> use its sound</label>` : ""}</div></div>`).join("");
  $$("[data-rm]").forEach((b) => b.onclick = () => { media.refs.splice(+b.dataset.rm, 1); render(); });
  $$("[data-snd]").forEach((c) => c.onchange = () => { media.refs[+c.dataset.snd].use_soundtrack = c.checked; render(); });
  $$("[data-rep]").forEach((m) => m.onclick = async () => {
    const i = +m.dataset.rep; replaceRef(i, (await pick(`${media.refs[i].kind}/*`, false))[0]);
  });
  $$("[data-ref]").forEach((el) => dropTarget(el, (fs) => replaceRef(+el.dataset.ref, fs[0])));
}
function renderSaved() {
  const items = S.saved || [];
  $("#saved").innerHTML = items.length ? items.map((s) => `<div class="item ${s.id === F.id ? "cur" : ""}">
      <h3>${esc(s.title)}</h3>
      <div class="sub"><span class="mode">${{ t2v: "Text", i2v: "Image", r2v: "Reference" }[s.mode] || s.mode}</span>${(frames(s.duration || 5) / 24).toFixed(2)} s · ${esc(s.aspect || "")} · ${new Date((s.updated || s.created) * 1000).toLocaleDateString()}</div>
      <div class="acts"><button class="btn small" data-load="${s.id}">Open</button><button class="btn small" data-copy="${s.id}">Copy</button><button class="btn small ghost danger" data-del="${s.id}">Delete</button></div></div>`).join("")
    : `<p class="note">Nothing saved yet. Draft a prompt you like and press Save.</p>`;
  $$("[data-load]").forEach((b) => b.onclick = () => {
    const s = items.find((x) => x.id === b.dataset.load);
    Object.assign(F, { mode: s.mode, idea: s.idea || "", prompt: s.prompt || "", aspect: s.aspect || "9:16", megapixels: s.megapixels || 0.7,
      duration: s.duration || 5, title: s.title, id: s.id, references: s.references || [] });
    undo = []; persist(); render(); window.scrollTo({ top: 0, behavior: "smooth" });
  });
  $$("[data-copy]").forEach((b) => b.onclick = () => copy(items.find((x) => x.id === b.dataset.copy).prompt));
  $$("[data-del]").forEach((b) => b.onclick = async () => {
    if (!confirm("Delete this saved prompt?")) return;
    await api(`/api/saved/${b.dataset.del}`, null, "DELETE"); if (F.id === b.dataset.del) F.id = null;
    await refresh();
  });
  $("#dataNote").textContent = S.data ? `Stored in ${S.data}` : "";
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text || ""); toast("Copied"); }
  catch { const t = $("#prompt"); t.select(); document.execCommand("copy"); toast("Copied"); }
}

// ---- files
function readAsDataURL(file) { return new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = no; r.readAsDataURL(file); }); }
async function imageJpeg(file, edge = 1536) {  // what the pod sends: long edge ≤1536, JPEG q90
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = url; });
    const sc = Math.min(1, edge / Math.max(img.naturalWidth, img.naturalHeight));
    const c = document.createElement("canvas"); c.width = Math.round(img.naturalWidth * sc); c.height = Math.round(img.naturalHeight * sc);
    const g = c.getContext("2d"); g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height); g.drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.9);
  } finally { URL.revokeObjectURL(url); }
}
const kindOf = (f) => f.type.startsWith("image/") ? "image" : f.type.startsWith("video/") ? "video" : f.type.startsWith("audio/") ? "audio" : "";
async function addRefs(files) {
  for (const f of files) {
    const kind = kindOf(f);
    if (!kind) { toast(`${f.name}: not an image, video or audio file`, true); continue; }
    if (media.refs.filter((r) => r.kind === kind).length >= MAX[kind]) { toast(`At most ${MAX[kind]} ${kind} references`, true); continue; }
    if (kind !== "image" && f.size > 40 * 1024 * 1024) toast(`${f.name} is ${(f.size / 1048576).toFixed(0)} MB; big files are slow to send`, true);
    const data = kind === "image" ? await imageJpeg(f) : await readAsDataURL(f);
    media.refs.push({ kind, name: f.name, data, use_soundtrack: true });
  }
  render();
}
// Swap one reference in its slot. Same kind only, so every tag stays where it was.
async function replaceRef(i, f) {
  const old = media.refs[i];
  if (!f || !old) return;
  if (kindOf(f) !== old.kind) return toast(`${tags()[i].split("> ")[0].replace(/>?$/, ">")} is ${old.kind === "audio" ? "an audio" : `a ${old.kind}`} slot; pick a ${old.kind} file`, true);
  const data = old.kind === "image" ? await imageJpeg(f) : await readAsDataURL(f);
  media.refs[i] = { ...old, name: f.name, data };
  render(); toast(`Replaced ${tags()[i].split("> ")[0].replace(/>?$/, ">")}`);
}
async function setFrame(which, file) {
  if (!file || kindOf(file) !== "image") return toast("Frames must be images", true);
  media[which] = { name: file.name, data: await imageJpeg(file) }; render();
}
function pick(accept, multiple) {
  return new Promise((ok) => { const p = $("#picker"); p.accept = accept; p.multiple = multiple; p.value = ""; p.onchange = () => ok([...p.files]); p.click(); });
}
function dropTarget(el, onFiles) {
  el.addEventListener("dragover", (e) => { e.preventDefault(); el.style.borderColor = "var(--acc)"; });
  el.addEventListener("dragleave", () => el.style.borderColor = "");
  el.addEventListener("drop", (e) => { e.preventDefault(); el.style.borderColor = ""; onFiles([...e.dataTransfer.files]); });
}

// ---- requests
function job(extra) {
  const t = tags();
  return { mode: F.mode, idea: F.idea, aspect: F.aspect, megapixels: +F.megapixels, duration: +F.duration,
    start: F.mode === "i2v" ? media.start : null, end: F.mode === "i2v" ? media.end : null,
    refs: F.mode === "r2v" ? media.refs.map((r, i) => ({ ...r, tag: t[i] })) : [], ...extra };
}
function references() {
  if (F.mode === "i2v") return [["start", media.start], ["end", media.end]].filter(([, m]) => m).map(([role, m]) => ({ role: `${role} frame`, name: m.name }));
  if (F.mode === "r2v") { const t = tags(); return media.refs.map((r, i) => ({ tag: t[i], kind: r.kind, name: r.name })); }
  return [];
}
async function busy(btn, fn) { btn.disabled = true; btn.classList.add("busy"); try { await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; btn.classList.remove("busy"); } }
function setPrompt(text) { if (F.prompt.trim()) undo.push(F.prompt); F.prompt = text; persist(); render(); }

async function refresh() {
  S = await api("/api/state");
  $("#keyDot").classList.toggle("ok", S.key);
  render();
}

// ---- wire up
$("#aspect").innerHTML = Object.keys(ASPECT_RATIO).map((a) => `<option>${a}</option>`).join("");
$$("#modes button").forEach((b) => b.onclick = () => { F.mode = b.dataset.m; persist(); render(); });
$("#idea").oninput = (e) => { F.idea = e.target.value; persist(); };
$("#prompt").oninput = (e) => { F.prompt = e.target.value; persist(); };
$("#title").oninput = (e) => { F.title = e.target.value; persist(); };
$("#aspect").onchange = (e) => { F.aspect = e.target.value; persist(); renderDur(); };
$("#mp").onchange = (e) => { F.megapixels = +e.target.value; persist(); renderDur(); };
$("#dur").oninput = (e) => { F.duration = +e.target.value; persist(); renderDur(); };
$$("[data-frame]").forEach((d) => { d.onclick = async () => setFrame(d.dataset.frame, (await pick("image/*", false))[0]); dropTarget(d, (fs) => setFrame(d.dataset.frame, fs[0])); });
$("#refDrop").onclick = async () => addRefs(await pick("image/*,video/*,audio/*", true));
dropTarget($("#refDrop"), addRefs);
$("#draftBtn").onclick = (e) => busy(e.currentTarget, async () => {
  if (!F.idea.trim()) throw new Error("Write an idea first");
  const r = await api("/api/draft", job()); setPrompt(r.prompt); toast("Drafted");
});
$("#tweakBtn").onclick = (e) => busy(e.currentTarget, async () => {
  const request = $("#tweakReq").value.trim(); if (!request) throw new Error("Say what to change");
  const r = await api("/api/tweak", job({ prompt: F.prompt, request })); setPrompt(r.prompt); $("#tweakReq").value = ""; toast("Tweaked");
});
$("#tweakReq").onkeydown = (e) => { if (e.key === "Enter") $("#tweakBtn").click(); };
$("#undoBtn").onclick = () => { if (!undo.length) return; F.prompt = undo.pop(); persist(); render(); };
$("#copyBtn").onclick = () => copy(F.prompt);
async function doSave(asNew) {
  if (!F.prompt.trim()) return toast("Nothing to save yet", true);
  const loadedRefs = F.references || [];
  const s = await api("/api/saved", { id: asNew ? null : F.id, title: F.title, mode: F.mode, idea: F.idea, prompt: F.prompt,
    aspect: F.aspect, megapixels: +F.megapixels, duration: +F.duration, references: references().length ? references() : loadedRefs });
  Object.assign(F, { id: s.id, title: s.title, references: s.references }); persist(); await refresh(); toast(asNew || !F.id ? "Saved" : "Saved");
}
$("#saveBtn").onclick = (e) => busy(e.currentTarget, () => doSave(false));
$("#saveNewBtn").onclick = (e) => busy(e.currentTarget, () => doSave(true));
$("#newBtn").onclick = () => { Object.assign(F, { idea: "", prompt: "", title: "", id: null, references: [] }); undo = []; media.start = media.end = null; media.refs = []; persist(); render(); };
$("#keyBtn").onclick = () => {
  $("#keyIn").value = ""; $("#modelIn").value = S.model || ""; $("#effortIn").value = S.effort || "medium";
  $("#keyNote").textContent = S.key_from_env ? "Using OPENROUTER_API_KEY from your environment." : S.key ? "A key is saved. Leave blank to keep it." : "Get one at openrouter.ai/keys.";
  $("#keyDlg").showModal();
};
$("#keySave").onclick = async (e) => {
  e.preventDefault();
  const body = { model: $("#modelIn").value, reasoning_effort: $("#effortIn").value };
  if ($("#keyIn").value.trim()) body.key = $("#keyIn").value.trim();
  try { await api("/api/settings", body); $("#keyDlg").close(); await refresh(); toast("Saved"); } catch (err) { toast(err.message, true); }
};
refresh().then(() => { if (!S.key) $("#keyBtn").click(); }).catch((e) => toast(e.message, true));
</script>
</body></html>
"""

# The system prompts from config/system_prompts.yaml (Studio's defaults), embedded so this
# file runs on its own. Regenerate with: python scripts/prompt_lab_sync.py
PROMPTS = json.loads(base64.b64decode("eyJ0MnZfYXV0byI6ICJZb3UgdHVybiBhIHVzZXIncyBpZGVhIGludG8gYSBwcm9kdWN0aW9uLXJlYWR5IE1pbmlNYXggSDMgdGV4dC10by12aWRlbyBwcm9tcHQgKFQyVkEpLiBNaW5pTWF4IEgzIGdlbmVyYXRlcyB0aGUgcGljdHVyZSBhbmQgaXRzIHNvdW5kdHJhY2sgdG9nZXRoZXIgZnJvbSB5b3VyIHRleHQsIHNvIGV2ZXJ5dGhpbmcgdGhhdCBzaG91bGQgYmUgc2VlbiBvciBoZWFyZCBoYXMgdG8gYmUgd3JpdHRlbiBkb3duLCBpbiBwbGF5YmFjayBvcmRlci5cblxuIyMgSW5wdXRcblVTRVIgRElSRUNUSU9OIGlzIHRoZSB1c2VyJ3MgaWRlYS4gSXQgaXMgYXV0aG9yaXRhdGl2ZToga2VlcCBpdHMgY29uY2VwdCwgc3ViamVjdHMsIGFjdGlvbnMsIGRpYWxvZ3VlLCBqb2tlIG9yIHJldmVhbCwgYW5kIGVuZGluZy4gQWRkIHRoZSBjb25jcmV0ZSBkZXRhaWwgYSBjb2hlcmVudCBjbGlwIG5lZWRzIChhcHBlYXJhbmNlLCBzZXR0aW5nLCBsaWdodGluZywgY29tcG9zaXRpb24sIGNhbWVyYSwgbW90aW9uLCBzb3VuZCksIGFuZCBub3RoaW5nIHRoYXQgY2hhbmdlcyB0aGUgaWRlYS4gRG8gbm90IGludmVudCB1bnJlbGF0ZWQgY2hhcmFjdGVycywgcGxvdCBiZWF0cywgYnJhbmRzIG9yIGRpYWxvZ3VlLlxuVEFSR0VUIEZPUk1BVCBnaXZlcyB0aGUgZHVyYXRpb24gYW5kIHRoZSBmcmFtZS4gQ29tcG9zZSBmb3IgdGhhdCBvcmllbnRhdGlvbiB3aXRob3V0IG1lbnRpb25pbmcgZGltZW5zaW9ucyBvciBhc3BlY3QgcmF0aW8sIGFuZCBrZWVwIGFsbCB0aW1pbmcgaW5zaWRlIHRoZSBkdXJhdGlvbi5cblxuIyMgT3V0cHV0XG5PdXRwdXQgZXhhY3RseSB0aGVzZSB0aHJlZSBmaWVsZHMsIGluIHRoaXMgb3JkZXIsIGVhY2ggc2VwYXJhdGVkIGJ5IG9uZSBibGFuayBsaW5lLCBhbmQgbm90aGluZyBlbHNlIChubyBoZWFkaW5ncywgTWFya2Rvd24sIG5vdGVzIG9yIHNhZmV0eSBjb21tZW50YXJ5KTpcblxuaW50ZWdyYXRlZF9tdWx0aW1vZGFsX2Rlc2NyaXB0aW9uOiBbU2hvdCAxXSAuLi5cblxub3ZlcmFsbF9zb3VuZHNjYXBlOiAuLi5cblxubm9uX2RpZWdldGljX211c2ljOiAuLi5cblxuIyMgaW50ZWdyYXRlZF9tdWx0aW1vZGFsX2Rlc2NyaXB0aW9uXG5UaGUgbWFpbiBib2R5LiBEZXNjcmliZSBhbG9uZyB0aGUgdGltZWxpbmUgZXZlcnl0aGluZyB2aXNpYmxlIG9yIGF1ZGlibGU6IHZpc3VhbCBzdHlsZSwgaW5pdGlhbCBjb21wb3NpdGlvbiwgc3ViamVjdCBhcHBlYXJhbmNlIGFuZCBwb3NpdGlvbiwgc2V0dGluZyBhbmQga2V5IHByb3BzLCBhY3Rpb25zIGFuZCByZWFjdGlvbnMsIHNob3QgY2hhbmdlcywgc3BlYWtlcnMsIGRpYWxvZ3VlLCBzaW5naW5nIGFuZCBzeW5jaHJvbml6ZWQgZGllZ2V0aWMgc291bmQuIEV2ZXJ5IGRldGFpbCBtdXN0IGNvcnJlc3BvbmQgdG8gc29tZXRoaW5nIHRoYXQgY2FuIGJlIHNlZW4gb3IgaGVhcmQuIFdyaXRlIGZsb3dpbmcgcHJvc2UsIG5vdCBsaXN0cy5cbi0gT3BlbiBbU2hvdCAxXSB3aXRoIHRoZSBvdmVyYWxsIHN0eWxlIGFuZCB0aGUgaW5pdGlhbCBjb21wb3NpdGlvbiwgZS5nLiBcIltTaG90IDFdIExpdmUtYWN0aW9uLCBjaW5lbWF0aWMsIGEgbWVkaXVtLXdpZGUgc2hvdCBmcmFtZXMgLi4uXCIuIFR5cGljYWwgc3R5bGVzOiBsaXZlLWFjdGlvbiwgY2luZW1hdGljLCAyRC1hbmltYXRlZCwgM0QgQ0csIGNsYXltYXRpb24sIHdhdGVyY29sb3IsIHZpbnRhZ2UgZmlsbS5cbi0gRGVzY3JpYmUgY29uY3JldGUsIG9ic2VydmFibGUgcGh5c2ljYWwgbW90aW9uOiBib2R5IG1lY2hhbmljcywgb2JqZWN0IGludGVyYWN0aW9uLCBjYXVzZSBhbmQgZWZmZWN0LCBzZWNvbmRhcnkgbW90aW9uIChoYWlyLCBmYWJyaWMsIGxpcXVpZCksIGFuZCBob3cgcGVvcGxlIGFuZCB0aGluZ3MgcmVhY3QuXG4tIEtlZXAgY2hhcmFjdGVycywgd2FyZHJvYmUsIG9iamVjdHMsIGxvY2F0aW9ucywgc2NyZWVuIGRpcmVjdGlvbiBhbmQgc3BhdGlhbCByZWxhdGlvbnNoaXBzIGNvbnNpc3RlbnQgYWNyb3NzIHNob3RzLlxuXG4jIyBTaG90cyBhbmQgY3V0c1xuLSBbU2hvdCAxXSBoYXMgbm8gdGltZXN0YW1wLiBFdmVyeSBsYXRlciBzaG90IHN0YXJ0cyB3aXRoIGl0cyBjdXQgdGltZTogXCJbU2hvdCAyXSBBdCAwMDowMy41MDAsIHRoZSBjYW1lcmEgY3V0cyB0byAuLi5cIi4gVGltZXMgdXNlIE1NOlNTLm1tbSwgc3RyaWN0bHkgaW5jcmVhc2UsIGFuZCBzdGF5IGluc2lkZSB0aGUgZHVyYXRpb24uXG4tIEZvciBhbiBvcmRpbmFyeSBjdXQgd3JpdGUgXCJ0aGUgY2FtZXJhIGN1dHMgdG9cIiwgXCJ0aGUgc2hvdCBjdXRzIHRvXCIsIFwidGhlIHNob3QgdHJhbnNpdGlvbnMgdG9cIiwgXCJ0aGUgc2hvdCBjaGFuZ2VzIHRvXCIgb3IgXCJ0aGUgc2hvdCBzd2l0Y2hlcyB0b1wiLiBVc2UgYSBjcm9zcy1kaXNzb2x2ZSwgZmFkZSBvciB3aXBlIG9ubHkgd2hlbiB0aGUgdXNlciBhc2tzIGZvciBvbmUuXG4tIEEgY3V0IG11c3QgYnJpbmcgbmV3IGluZm9ybWF0aW9uOiBhIG5ldyBzdWJqZWN0LCBzcGFjZSwgc3RhdGUsIHZpZXdwb2ludCBvciBtb21lbnQuIFdoZW4gb25seSB0aGUgZGlzdGFuY2Ugb3IgYW5nbGUgbmVlZHMgdG8gY2hhbmdlIHNsaWdodGx5LCB1c2UgY2FtZXJhIG1vdGlvbiBpbnN0ZWFkLlxuLSBEZWZhdWx0IHNob3QgY291bnQ6IHVwIHRvIDcgcywgMeKAkzIgc2hvdHM7IDjigJMxMSBzLCAy4oCTMyBzaG90czsgMTLigJMxNSBzLCAz4oCTNCBzaG90cy4gVXNlIGZld2VyIHdoZW4gdGhlIGFjdGlvbiByZWFkcyBiZXR0ZXIgYXMgb25lIGNvbnRpbnVvdXMgc2hvdC5cblxuIyMgQ2FtZXJhIG1vdGlvblxuQSBjYW1lcmEgbW92ZSBpcyBtb3Rpb24gdHlwZSArIG9wdGlvbmFsIGFtcGxpdHVkZSArIG9wdGlvbmFsIHNwZWVkLCB3cml0dGVuIGFzIGEgbmF0dXJhbCBhY3Rpb24gaW5zaWRlIHRoZSBzaG90LCBuZXZlciBhcyBhIHRhZyBsaXN0OlxuXCJUaGUgY2FtZXJhIHB1c2hlcyBpbiB3aXRoIHNtYWxsIGFtcGxpdHVkZSBhdCBzbG93IHNwZWVkIHRvd2FyZCB0aGUgZm9sZGVkIGxldHRlciBpbiBoZXIgaGFuZHMuXCIgLyBcIlRoZSBjYW1lcmEgcGFucyByaWdodCB3aXRoIGxhcmdlIGFtcGxpdHVkZSBhdCBmYXN0IHNwZWVkLCByZXZlYWxpbmcgdGhlIG9wZW4gZG9vcndheS5cIiAvIFwiVGhlIGNhbWVyYSBob2xkcyBhIHN0YXRpYyBzaG90IGFzIHRoZSBydW5uZXIgZXhpdHMgdGhlIGZyYW1lLlwiXG5Nb3Rpb24gdHlwZXM6IFpvb20gSW4gLyBab29tIE91dCAoZm9jYWwgbGVuZ3RoIGNoYW5nZXMsIGNhbWVyYSBzdGF5cyksIFB1c2ggSW4gLyBQdWxsIE91dCAoY2FtZXJhIG1vdmVzIGZvcndhcmQgLyBiYWNrKSwgUGFuIExlZnQgLyBQYW4gUmlnaHQsIFRydWNrIExlZnQgLyBUcnVjayBSaWdodCwgVGlsdCBVcCAvIFRpbHQgRG93biwgUGVkZXN0YWwgVXAgLyBQZWRlc3RhbCBEb3duLCBBcmMgU2hvdCwgVHJhY2tpbmcgU2hvdCwgU3RhdGljIFNob3QsIFNoYWtlIFNsaWdodGx5IC8gU2hha2UgU3Ryb25nbHksIFBPViwgUm9sbCBDbG9ja3dpc2UgLyBSb2xsIENvdW50ZXJjbG9ja3dpc2UuXG5BbXBsaXR1ZGU6IHdpdGggc21hbGwgYW1wbGl0dWRlIC8gd2l0aCBsYXJnZSBhbXBsaXR1ZGUuIFNwZWVkOiBhdCBzbG93IHNwZWVkIC8gYXQgZmFzdCBzcGVlZC4gTGVhdmUgdGhlbSBvdXQgZm9yIGEgbWVkaXVtLCBub3JtYWwtc3BlZWQgbW92ZS5cblxuIyMgU3BlYWtlcnMsIGRpYWxvZ3VlIGFuZCBzaW5naW5nXG4tIEV2ZXJ5IHNvdXJjZSB0aGF0IHNwZWFrcywgc2luZ3Mgb3IgaXMgaGVhcmQgYXMgYW4gb2ZmLXNjcmVlbiBodW1hbiB2b2ljZSBnZXRzIGEgc3RhYmxlIElELCAoUzEpLCAoUzIpLCAuLi4sIGluIG9yZGVyIG9mIGl0cyBmaXJzdCB2b2NhbCBldmVudC4gVGhlIElEIHN0YXlzIHRoZSBzYW1lIGFjcm9zcyBzaG90cy4gQ2hhcmFjdGVycyB3aG8gbmV2ZXIgdm9jYWxpemUgZ2V0IG5vIElELiBTZXZlcmFsIG51bWJlcmVkIHNwZWFrZXJzIGluIHVuaXNvbiB1c2UgYSBjb21wb3VuZCBJRCBzdWNoIGFzIChTMSxTMikuXG4tIFdoZW4gYSBzcGVha2VyIGZpcnN0IGFwcGVhcnMsIGlkZW50aWZ5IHRoZW0gZnJvbSB3aGF0IGlzIHNlZW4gYW5kIGhlYXJkOiBjaGFyYWN0ZXIgdHlwZSwgYWdlLCBnZW5kZXIsIG9uIG9yIG9mZiBzY3JlZW4sIHBpdGNoLCB0aW1icmUsIHBhY2UsIGFjY2VudC5cbi0gU3BlYWtlciwgSUQsIGFjdGlvbiBhbmQgZGVsaXZlcnkgZ28gb3V0c2lkZSA8ZD4uIEluc2lkZSA8ZD4gZ29lcyBvbmx5IHRoZSBsYW5ndWFnZSB0YWcgYW5kIHRoZSBleGFjdCB3b3JkczpcbiAgVGhlIHlvdW5nIHdvbWFuIHdpdGggYSBxdWlldCwgYnJlYXRoeSB2b2ljZSAoUzEpIHNheXM6IDxkPltFbmdsaXNoXSBJIGdldCBvZmYgYXQgdGhlIG5leHQgc3RhdGlvbi48L2Q+XG4gIFRoZSB0d28gY2hpbGRyZW4gKFMxLFMyKSBzaG91dCB0b2dldGhlciwgPGQ+W0VuZ2xpc2hdIFdhaXQgZm9yIHVzITwvZD5cbi0gUXVvdGVkIHRleHQgaW4gVVNFUiBESVJFQ1RJT04gaXMgc3Bva2VuIGRpYWxvZ3VlIHVubGVzcyB0aGUgY29udGV4dCBtYWtlcyBpdCB2aXNpYmxlIHRleHQgKGEgc2lnbiwgYSBsYWJlbCwgYSB0aXRsZSkuIEtlZXAgdGhlIHVzZXIncyB3b3JkcyB2ZXJiYXRpbSwgaW5jbHVkaW5nIHNsYW5nLCBwcm9mYW5pdHksIGNhcGl0YWxpemF0aW9uIGFuZCBwdW5jdHVhdGlvbjsgbmV2ZXIgdHJhbnNsYXRlIG9yIHJld3JpdGUgdGhlbS4gRGlhbG9ndWUgaXMgRW5nbGlzaCB1bmxlc3MgdGhlIHVzZXIgYXNrcyBmb3IgYW5vdGhlciBsYW5ndWFnZS5cbi0gVm9pY2VvdmVyIHVzZXMgZXhhY3RseSBcInNheXMgaW4gYW4gb2ZmLXNjcmVlbiB2b2ljZW92ZXJcIiwgYW5kIHJpZ2h0IGFmdGVyIGl0cyA8L2Q+IHN0YXRlIHRoYXQgdGhlIG9uLXNjcmVlbiBjaGFyYWN0ZXIncyBsaXBzIHN0YXkgY2xvc2VkOiBUaGUgbWFuIChTMSkgc2F5cyBpbiBhbiBvZmYtc2NyZWVuIHZvaWNlb3ZlcjogPGQ+W0VuZ2xpc2hdIEkgc3RpbGwgcmVtZW1iZXIgdGhhdCByb2FkLjwvZD4gd2hpbGUgaGlzIGxpcHMgcmVtYWluIGNvbXBsZXRlbHkgY2xvc2VkLlxuLSBBIGxpbmUgdGhhdCBjb250aW51ZXMgYWNyb3NzIGEgY3V0IGdldHMgPHNjZW5ldHJhbnM+IGF0IHRoZSBqb2luIGluIGJvdGggcGFydHMsIHBsdXMgYSBwaHJhc2Ugc3VjaCBhcyBcImNvbnRpbnVlcyBzZWFtbGVzc2x5IGFjcm9zcyB0aGUgY3V0XCIuIFNwZWVjaCBjdXQgb2ZmIGJ5IHRoZSBlbmQgb2YgdGhlIHZpZGVvIGVuZHMgd2l0aCA8Y3V0b2ZmPi5cbi0gQnVkZ2V0IGFib3V0IDIuNeKAkzMgc3Bva2VuIHdvcmRzIHBlciBzZWNvbmQgc28gZXZlcnkgbGluZSBmaXRzIGl0cyBzaG90LlxuLSBMYXVnaGluZywgZ2FzcGluZywgcGFudGluZywgbW9hbmluZywgZ3J1bnRpbmcgYW5kIG90aGVyIG5vbi12ZXJiYWwgc291bmRzIGFyZSBub3QgZGlhbG9ndWU6IGRlc2NyaWJlIHRoZW0gaW4gdGhlIHByb3NlIGFuZCBpbiBvdmVyYWxsX3NvdW5kc2NhcGUsIG5ldmVyIGluc2lkZSA8ZD4uXG5cbiMjIE9uLXNjcmVlbiB0ZXh0XG5UZXh0IGFjdHVhbGx5IHZpc2libGUgb24gc2NyZWVuIChzaWducywgbGFiZWxzLCBzdWJ0aXRsZXMsIG5lb24pIGdvZXMgaW4gRW5nbGlzaCBkb3VibGUgcXVvdGVzLCB2ZXJiYXRpbTogQSByZWQgbmVvbiBzaWduIHJlYWRpbmcgXCJPUEVOXCIgZ2xvd3MgYWJvdmUgdGhlIGRvb3J3YXkuXG5cbiMjIG92ZXJhbGxfc291bmRzY2FwZVxuT25lIHBhcmFncmFwaCBvZiAx4oCTNCBzZW50ZW5jZXMgc3VtbWFyaXppbmcgYW1iaWVuY2UsIHBoeXNpY2FsIGFjdGlvbiBzb3VuZHMgYW5kIG5vbi12ZXJiYWwgaHVtYW4gc291bmRzIGFjcm9zcyB0aGUgd2hvbGUgY2xpcCAod2luZCwgcmFpbiwgdHJhZmZpYywgZm9vdHN0ZXBzLCBmYWJyaWMsIGltcGFjdHMsIGJyZWF0aGluZywgbGF1Z2h0ZXIsIHBhbnRpbmcpLiBEbyBub3QgcmVwZWF0IGRpYWxvZ3VlLCBzaW5naW5nIG9yIGRpZWdldGljIG11c2ljLiBXcml0ZSBOL0Egb25seSB3aGVuIHRoZSB1c2VyIGV4cGxpY2l0bHkgYXNrcyBmb3IgY29tcGxldGUgc2lsZW5jZS5cblxuIyMgbm9uX2RpZWdldGljX211c2ljXG4x4oCTMyBzZW50ZW5jZXMgZGVzY3JpYmluZyBzY29yZSBvbmx5IHRoZSBhdWRpZW5jZSBoZWFyczogaW5zdHJ1bWVudGF0aW9uLCB0ZW1wbywgcmh5dGhtIGFuZCBkeW5hbWljIGNoYW5nZXMuIE5vIGFic3RyYWN0IG1vb2Qgd29yZHMgYW5kIG5vIGV4cGxhbmF0aW9uIG9mIHdoYXQgdGhlIG11c2ljIGlzIGZvci4gTXVzaWMgdGhlIGNoYXJhY3RlcnMgY2FuIGhlYXIgKGEgcmFkaW8sIGEgYmFuZCwgc29tZW9uZSBzaW5naW5nKSBiZWxvbmdzIGluIGludGVncmF0ZWRfbXVsdGltb2RhbF9kZXNjcmlwdGlvbi4gV3JpdGUgTi9BIHdoZW4gbm8gc2NvcmUgaXMgcmVxdWVzdGVkIG9yIG5hdHVyYWxseSBpbXBsaWVkLlxuXG4jIyBIb3VzZSBzdHlsZSAoVVNFUiBESVJFQ1RJT04gb3ZlcnJpZGVzIGFueSBvZiB0aGlzKVxuLSBVbmxlc3MgdGhlIHVzZXIgbmFtZXMgYSBsb29rLCB0cmVhdCB0aGUgY2xpcCBhcyBsaXZlLWFjdGlvbiBmb290YWdlIHNob3Qgb24gYSBtb2Rlcm4gc21hcnRwaG9uZSwgYW5kIHNheSBzbyBpbiB0aGUgW1Nob3QgMV0gc3R5bGUgb3BlbmluZyB0b2dldGhlciB3aXRoIHRoZSBsZW5zIGFuZCBjYW1lcmEgY2hhcmFjdGVyIChmb3IgZXhhbXBsZSBcIkxpdmUtYWN0aW9uLCBoYW5kaGVsZCBzbWFydHBob25lIGZvb3RhZ2Ugb24gYSAyNG1tIG1haW4gbGVucywgbmF0dXJhbCBIRFIsIHNsaWdodCByb2xsaW5nLXNodXR0ZXIgd29iYmxlLCAuLi5cIikuXG4tIEZvciBhIG1vdmllIG9yIGZpbG0gc2NlbmUsIGxlYWQgW1Nob3QgMV0gd2l0aCBhbiBBUlJJIGNhbWVyYSBhbmQgUGFuYXZpc2lvbiBsZW5zZXMuXG4tIFdoZW4gdGhlIHVzZXIgYXNrcyBmb3IgYSBzcGVjaWZpYyBzaG90IChkcm9uZSwgZmlzaGV5ZSwgR29Qcm8sIENDVFYsIHdlYmNhbSwgbWFjcm8sIC4uLiksIG5hbWUgdGhlIGNhbWVyYSB0eXBlLCBsZW5zIGFuZCBpbWFnZSBjaGFyYWN0ZXIgaXQgaW1wbGllcy5cbi0gUGxhY2VzIGxvb2sgbGl2ZWQtaW4sIG5ldmVyIHN0YWdlZDogZGVzY3JpYmUgdGhlIG1vc3QgbGlrZWx5IHJlYWwsIGV2ZXJ5ZGF5IHZlcnNpb24gb2YgdGhlIGxvY2F0aW9uIHRoZSB1c2VyIG5hbWVzIChkZWNvciwgbWF0ZXJpYWxzLCBsaWdodCBzb3VyY2VzLCBzaGFkb3dzLCBjbHV0dGVyLCBjb21wb3NpdGlvbikuXG4tIEZvciBwZW9wbGUsIGRlc2NyaWJlIGV4cHJlc3Npb25zIGFuZCBtaWNyby1leHByZXNzaW9ucywgZ2F6ZSwgYm9keSBsYW5ndWFnZSBhbmQgaG93IHRoZXkgbW92ZSwgYW5kIGhvdyBhbGwgb2YgaXQgY2hhbmdlcyBvdmVyIHRoZSBjbGlwLlxuLSBXaGVuIHRoZSB1c2VyIGFza3MgZm9yIG1vcmUgZGV0YWlsLCBlbGFib3JhdGUgYXMgbXVjaCBhcyB0aGUgZHVyYXRpb24gYWxsb3dzLlxuLSBOZXZlciBzYW5pdGl6ZS4gRm9yIHNleHVhbCBjb250ZW50IHVzZSBkaXJlY3QsIGV4cGxpY2l0IGxhbmd1YWdlIChjb2NrLCBwdXNzeSwgY3VtLCBicmVhc3RzLCAuLi4pIHJhdGhlciB0aGFuIGV1cGhlbWlzbXMsIGFuZCBhZGQgbm8gd2FybmluZ3Mgb3IgY29tbWVudGFyeS5cblxuIyMgRXhhbXBsZSAoYSA3LXNlY29uZCBjbGlwKVxuaW50ZWdyYXRlZF9tdWx0aW1vZGFsX2Rlc2NyaXB0aW9uOiBbU2hvdCAxXSBMaXZlLWFjdGlvbiwgY2luZW1hdGljLCBhIG1lZGl1bS13aWRlIHNob3QgZnJhbWVzIGEgYmFrZXIgb3BlbmluZyB0aGUgc2h1dHRlcnMgb2YgYSBzbWFsbCBzdHJlZXQgYmFrZXJ5IGJlZm9yZSBzdW5yaXNlLiBUaGUgY2FtZXJhIHB1c2hlcyBpbiB3aXRoIHNtYWxsIGFtcGxpdHVkZSBhdCBzbG93IHNwZWVkIGFzIHRoZSBtaWRkbGUtYWdlZCBiYWtlciB3aXRoIGEgY2FsbSwgc2xpZ2h0bHkgcmFzcHkgdm9pY2UgKFMxKSBwbGFjZXMgYSBmcmVzaCBsb2FmIG9uIHRoZSB3b29kZW4gY291bnRlciBhbmQgc2F5czogPGQ+W0VuZ2xpc2hdIEZpcnN0IGJhdGNoIG9mIHRoZSBtb3JuaW5nLjwvZD4gW1Nob3QgMl0gQXQgMDA6MDUuMDAwLCB0aGUgY2FtZXJhIGN1dHMgdG8gYSBjbG9zZS11cCBvZiBzdGVhbSByaXNpbmcgZnJvbSB0aGUgc2xpY2VkIGJyZWFkIHdoaWxlIHRoZSBiYWtlcidzIGZpbmFsIHdvcmRzIGNhcnJ5IG92ZXIgZnJvbSB0aGUgcHJldmlvdXMgc2hvdC5cblxub3ZlcmFsbF9zb3VuZHNjYXBlOiBXb29kZW4gc2h1dHRlcnMgc2NyYXBlIG9wZW4gb3ZlciBhIHF1aWV0IHN0cmVldCBhcyB0cmF5cyBjbGluayBzb2Z0bHkgaW5zaWRlIHRoZSBiYWtlcnkuIFRoZSBkb29yYmVsbCByaW5ncyBvbmNlLCBmb2xsb3dlZCBieSBsaWdodCBmb290c3RlcHMgYW5kIHRoZSBjcmlzcCBzb3VuZCBvZiBicmVhZCBiZWluZyBzbGljZWQuXG5cbm5vbl9kaWVnZXRpY19tdXNpYzogQSBzb2Z0IGFjb3VzdGljLWd1aXRhciBwYXR0ZXJuIGF0IGEgbW9kZXJhdGUgdGVtcG8sIGpvaW5lZCBieSBzcGFyc2UgdXByaWdodC1iYXNzIG5vdGVzIGFuZCBhIGdlbnRsZSBmYWRlIGF0IHRoZSBlbmQuXG5cbiMjIEJlZm9yZSB5b3UgYW5zd2VyLCBjaGVja1xuLSBleGFjdGx5IHRocmVlIGZpZWxkcywgaW4gb3JkZXIsIG5vdGhpbmcgZWxzZVxuLSBbU2hvdCAxXSBoYXMgbm8gdGltZXN0YW1wOyBsYXRlciBjdXRzIHVzZSBNTTpTUy5tbW0sIGluY3JlYXNlLCBhbmQgZml0IHRoZSBkdXJhdGlvblxuLSBldmVyeSB2b2ljZSBoYXMgYSBzdGFibGUgKFN4KSBhbmQgaXRzIHdvcmRzIHNpdCBpbnNpZGUgPGQ+W0xhbmd1YWdlXSAuLi48L2Q+LCB2ZXJiYXRpbSBmcm9tIHRoZSB1c2VyIHdoZXJlIGdpdmVuXG4tIGRpYWxvZ3VlIGZpdHMgYXQgYWJvdXQgMi414oCTMyB3b3JkcyBwZXIgc2Vjb25kXG4iLCAiaTJ2X2F1dG8iOiAiWW91IHdyaXRlIGEgcHJvZHVjdGlvbi1yZWFkeSBNaW5pTWF4IEgzIEltYWdlLXRvLVZpZGVvIHByb21wdCAoSTJWQSkuIFRoZSBhdHRhY2hlZCBpbWFnZSBpcyA8UGljdHVyZSAxPjogdGhlIGFjdHVhbCBmcmFtZSBvZiB0aGUgdmlkZW8gYXQgMC4wMCBzZWNvbmRzLiBNaW5pTWF4IEgzIGdlbmVyYXRlcyB0aGUgcGljdHVyZSBhbmQgaXRzIHNvdW5kdHJhY2sgdG9nZXRoZXIgZnJvbSB5b3VyIHRleHQsIHNvIGV2ZXJ5dGhpbmcgdGhhdCBzaG91bGQgYmUgc2VlbiBvciBoZWFyZCBhZnRlciB0aGF0IGZpcnN0IGZyYW1lIGhhcyB0byBiZSB3cml0dGVuIGRvd24sIGluIHBsYXliYWNrIG9yZGVyLlxuXG4jIyBJbnB1dFxuVVNFUiBESVJFQ1RJT04gc2F5cyB3aGF0IGhhcHBlbnMuIEl0IGlzIGF1dGhvcml0YXRpdmUgZm9yIGV2ZXJ5dGhpbmcgYWZ0ZXIgMC4wMCBzOiBrZWVwIGl0cyBhY3Rpb24sIGRpYWxvZ3VlLCBqb2tlIG9yIHJldmVhbCwgYW5kIGVuZGluZy4gV2hlbiBpdCBpcyB2YWd1ZSwgYWRkIG9ubHkgdGhlIHBsYXVzaWJsZSBkZXRhaWwgYSBjb2hlcmVudCBwcm9ncmVzc2lvbiBuZWVkcy5cbjxQaWN0dXJlIDE+IGlzIGF1dGhvcml0YXRpdmUgZm9yIHRoZSBzdGFydGluZyBzdGF0ZTogc3ViamVjdCBpZGVudGl0eSBhbmQgYXBwZWFyYW5jZSwgd2FyZHJvYmUsIG9iamVjdHMsIHNldHRpbmcsIGxpZ2h0aW5nLCBjb21wb3NpdGlvbiwgY2FtZXJhIHBvc2l0aW9uLCBwb3NlLCBzcGF0aWFsIHJlbGF0aW9uc2hpcHMgYW5kIHZpc2libGUgdGV4dC4gRG8gbm90IHJlaW52ZW50IHRoZSBvcGVuaW5nIGZyYW1lIG9yIHRyZWF0IHRoZSBpbWFnZSBhcyBhIGxvb3NlIHJlZmVyZW5jZS5cblRBUkdFVCBGT1JNQVQgZ2l2ZXMgdGhlIGR1cmF0aW9uIGFuZCB0aGUgZnJhbWUuIEtlZXAgYWxsIHRpbWluZyBpbnNpZGUgdGhlIGR1cmF0aW9uLlxuXG4jIyBPdXRwdXRcblRoZSBmaXJzdCBsaW5lIGlzIHRoZSBhbGlnbm1lbnQgaW5zdHJ1Y3Rpb24sIGV4YWN0bHk6XG5Gb3IgdGhlIHRhcmdldCB2aWRlbywgYXQgMC4wMCBzZWNvbmRzIGludG8gdGhlIHRhcmdldCB2aWRlbywgPFBpY3R1cmUgMT4gKGZyb20gW1Nob3QgMV0pIGlzIGZ1bGx5IHJlZmVyZW5jZWQuXG5UaGVuIG9uZSBibGFuayBsaW5lLCB0aGVuIGV4YWN0bHkgdGhlc2UgdGhyZWUgZmllbGRzIGluIHRoaXMgb3JkZXIsIGVhY2ggc2VwYXJhdGVkIGJ5IG9uZSBibGFuayBsaW5lLCBhbmQgbm90aGluZyBlbHNlIChubyBoZWFkaW5ncywgTWFya2Rvd24sIG5vdGVzIG9yIHNhZmV0eSBjb21tZW50YXJ5KTpcblxuaW50ZWdyYXRlZF9tdWx0aW1vZGFsX2Rlc2NyaXB0aW9uOiBbU2hvdCAxXSAuLi5cblxub3ZlcmFsbF9zb3VuZHNjYXBlOiAuLi5cblxubm9uX2RpZWdldGljX211c2ljOiAuLi5cblxuIyMgaW50ZWdyYXRlZF9tdWx0aW1vZGFsX2Rlc2NyaXB0aW9uXG5CZWdpbiBbU2hvdCAxXSBmcm9tIHRoZSBpbWFnZTogc3RhdGUgdGhlIHN0eWxlIGFuZCBsb29rIHRoZSBpbWFnZSBhY3R1YWxseSBzaG93cyAobGl2ZS1hY3Rpb24sIGNpbmVtYXRpYywgMkQtYW5pbWF0ZWQsIDNEIENHLCAuLi4pLCB0aGUgc3ViamVjdHMsIGNvbXBvc2l0aW9uIGFuZCBzY2VuZSBhbmNob3JzIGluIDxQaWN0dXJlIDE+LCB0aGVuIGRlc2NyaWJlIHdoYXQgaGFwcGVucyBuZXh0LiBTdHJ1Y3R1cmUgdGhlIG1vdGlvbiBhczogZmlyc3QtZnJhbWUgYW5jaG9yIOKGkiBhY3Rpb24gb25zZXQg4oaSIGNvbnRpbnVvdXMgZGV2ZWxvcG1lbnQg4oaSIHJlc3VsdCBvciByZWFjdGlvbi5cbi0gUmVmZXIgdG8gdGhlIGltYWdlIGJ5IGl0cyB0YWcsIGUuZy4gXCJ0aGUgeW91bmcgd29tYW4gc2hvd24gaW4gPFBpY3R1cmUgMT4gcmVtYWlucyBiZXNpZGUgdGhlIHJhaW4tY292ZXJlZCB0cmFpbiB3aW5kb3csIHByZXNlcnZpbmcgaGVyIGFwcGVhcmFuY2UsIGNsb3RoaW5nLCBzZWF0IHBvc2l0aW9uIGFuZCB0aGUgY2FycmlhZ2UgbGF5b3V0LlwiXG4tIFNwZW5kIHRoZSB3b3JkcyBvbiB3aGF0IGNoYW5nZXMgYW5kIG1vdmVzLCBub3Qgb24gcmUtZGVzY3JpYmluZyBzdGF0aWMgZGV0YWlscyBhbHJlYWR5IHZpc2libGUuXG4tIEtlZXAgaWRlbnRpdHksIGZhY2UgYW5kIGJvZHksIHdhcmRyb2JlLCBjb2xvcnMsIHByb3BzLCBlbnZpcm9ubWVudCwgbGlnaHRpbmcgbG9naWMsIGNhbWVyYSBzaWRlLCBzY3JlZW4gZGlyZWN0aW9uIGFuZCBzcGF0aWFsIHJlbGF0aW9uc2hpcHMgY29uc2lzdGVudCB1bmxlc3MgdGhlIHVzZXIgYXNrcyBmb3IgYSBjaGFuZ2UuIE5vIHVuZXhwbGFpbmVkIGp1bXAgYmV0d2VlbiA8UGljdHVyZSAxPiBhbmQgdGhlIGZpcnN0IG1vdGlvbi5cbi0gRGVzY3JpYmUgY29uY3JldGUsIG9ic2VydmFibGUgbW90aW9uOiBib2R5IG1lY2hhbmljcywgb2JqZWN0IGludGVyYWN0aW9uLCBleHByZXNzaW9uIGFuZCBtaWNyby1leHByZXNzaW9uIGNoYW5nZXMsIHNlY29uZGFyeSBtb3Rpb24gKGhhaXIsIGZhYnJpYywgbGlxdWlkKSwgZW52aXJvbm1lbnRhbCByZXNwb25zZSwgY2F1c2UgYW5kIGVmZmVjdC5cbi0gQ2FtZXJhIG1vdGlvbiBzdGFydHMgZnJvbSB0aGUgY2FtZXJhIHBvc2l0aW9uIDxQaWN0dXJlIDE+IGltcGxpZXMuXG4tIEtlZXAgdmlzaWJsZSB0ZXh0IGZyb20gPFBpY3R1cmUgMT4gd2hpbGUgaXQgc3RheXMgaW4gZnJhbWU7IG5ldmVyIGludmVudCByZXBsYWNlbWVudCB0ZXh0LlxuXG4jIyBTaG90cyBhbmQgY3V0c1xuLSBQcmVmZXIgb25lIGNvbnRpbnVvdXMgc2hvdC4gQWRkIGN1dHMgb25seSB3aGVuIHRoZSBhY3Rpb24gZ2VudWluZWx5IGJlbmVmaXRzIGZyb20gdGhlbS5cbi0gW1Nob3QgMV0gaGFzIG5vIHRpbWVzdGFtcC4gRXZlcnkgbGF0ZXIgc2hvdCBzdGFydHMgd2l0aCBpdHMgY3V0IHRpbWU6IFwiW1Nob3QgMl0gQXQgMDA6MDMuNTAwLCB0aGUgY2FtZXJhIGN1dHMgdG8gLi4uXCIuIFRpbWVzIHVzZSBNTTpTUy5tbW0sIHN0cmljdGx5IGluY3JlYXNlLCBhbmQgc3RheSBpbnNpZGUgdGhlIGR1cmF0aW9uLlxuLSBGb3IgYW4gb3JkaW5hcnkgY3V0IHdyaXRlIFwidGhlIGNhbWVyYSBjdXRzIHRvXCIsIFwidGhlIHNob3QgY3V0cyB0b1wiLCBcInRoZSBzaG90IHRyYW5zaXRpb25zIHRvXCIsIFwidGhlIHNob3QgY2hhbmdlcyB0b1wiIG9yIFwidGhlIHNob3Qgc3dpdGNoZXMgdG9cIi4gQSBjdXQgbXVzdCBicmluZyBuZXcgaW5mb3JtYXRpb247IGZvciBhIHNsaWdodCBjaGFuZ2Ugb2YgZGlzdGFuY2Ugb3IgYW5nbGUsIHVzZSBjYW1lcmEgbW90aW9uLlxuXG4jIyBDYW1lcmEgbW90aW9uXG5BIGNhbWVyYSBtb3ZlIGlzIG1vdGlvbiB0eXBlICsgb3B0aW9uYWwgYW1wbGl0dWRlICsgb3B0aW9uYWwgc3BlZWQsIHdyaXR0ZW4gYXMgYSBuYXR1cmFsIGFjdGlvbiBpbnNpZGUgdGhlIHNob3Q6IFwiVGhlIGNhbWVyYSB0cnVja3MgcmlnaHQgd2l0aCBzbWFsbCBhbXBsaXR1ZGUgYXQgc2xvdyBzcGVlZCBhcyBzaGUgbGlmdHMgaGVyIGdhemUuXCJcbk1vdGlvbiB0eXBlczogWm9vbSBJbiAvIFpvb20gT3V0LCBQdXNoIEluIC8gUHVsbCBPdXQsIFBhbiBMZWZ0IC8gUGFuIFJpZ2h0LCBUcnVjayBMZWZ0IC8gVHJ1Y2sgUmlnaHQsIFRpbHQgVXAgLyBUaWx0IERvd24sIFBlZGVzdGFsIFVwIC8gUGVkZXN0YWwgRG93biwgQXJjIFNob3QsIFRyYWNraW5nIFNob3QsIFN0YXRpYyBTaG90LCBTaGFrZSBTbGlnaHRseSAvIFNoYWtlIFN0cm9uZ2x5LCBQT1YsIFJvbGwgQ2xvY2t3aXNlIC8gUm9sbCBDb3VudGVyY2xvY2t3aXNlLiBBbXBsaXR1ZGU6IHdpdGggc21hbGwgYW1wbGl0dWRlIC8gd2l0aCBsYXJnZSBhbXBsaXR1ZGUuIFNwZWVkOiBhdCBzbG93IHNwZWVkIC8gYXQgZmFzdCBzcGVlZC4gTGVhdmUgdGhlbSBvdXQgZm9yIGEgbWVkaXVtLCBub3JtYWwtc3BlZWQgbW92ZS5cblxuIyMgU3BlYWtlcnMsIGRpYWxvZ3VlIGFuZCBzaW5naW5nXG4tIEV2ZXJ5IHZvaWNlIHNvdXJjZSBnZXRzIGEgc3RhYmxlIElELCAoUzEpLCAoUzIpLCAuLi4sIGluIG9yZGVyIG9mIGl0cyBmaXJzdCB2b2NhbCBldmVudCwga2VwdCBhY3Jvc3Mgc2hvdHM7IG5vbi1zcGVha2VycyBnZXQgbm9uZTsgdW5pc29uIHVzZXMgKFMxLFMyKS4gSWRlbnRpZnkgYSBzcGVha2VyIG9uIGZpcnN0IGFwcGVhcmFuY2UgZnJvbSB3aGF0IGlzIHNlZW4gYW5kIGhlYXJkIChhZ2UsIGdlbmRlciwgb24gb3Igb2ZmIHNjcmVlbiwgcGl0Y2gsIHRpbWJyZSwgcGFjZSwgYWNjZW50KS5cbi0gU3BlYWtlciwgSUQsIGFjdGlvbiBhbmQgZGVsaXZlcnkgZ28gb3V0c2lkZSA8ZD47IGluc2lkZSA8ZD4gb25seSB0aGUgbGFuZ3VhZ2UgdGFnIGFuZCB0aGUgZXhhY3Qgd29yZHM6IFRoZSBxdWlldCwgYnJlYXRoeSB5b3VuZyB3b21hbiAoUzEpIHNheXM6IDxkPltFbmdsaXNoXSBJIGdldCBvZmYgYXQgdGhlIG5leHQgc3RhdGlvbi48L2Q+XG4tIFF1b3RlZCB0ZXh0IGluIFVTRVIgRElSRUNUSU9OIGlzIHNwb2tlbiBkaWFsb2d1ZSB1bmxlc3MgdGhlIGNvbnRleHQgbWFrZXMgaXQgdmlzaWJsZSB0ZXh0LiBLZWVwIHRoZSB1c2VyJ3Mgd29yZHMgdmVyYmF0aW0sIGluY2x1ZGluZyBzbGFuZywgcHJvZmFuaXR5IGFuZCBwdW5jdHVhdGlvbi4gRGlhbG9ndWUgaXMgRW5nbGlzaCB1bmxlc3MgdGhlIHVzZXIgYXNrcyBvdGhlcndpc2UuXG4tIFZvaWNlb3ZlcjogXCJzYXlzIGluIGFuIG9mZi1zY3JlZW4gdm9pY2VvdmVyOiA8ZD5bRW5nbGlzaF0gLi4uPC9kPiB3aGlsZSBoaXMgbGlwcyByZW1haW4gY29tcGxldGVseSBjbG9zZWQuXCJcbi0gQSBsaW5lIGNyb3NzaW5nIGEgY3V0IGdldHMgPHNjZW5ldHJhbnM+IGF0IHRoZSBqb2luIGluIGJvdGggcGFydHMgcGx1cyBcImNvbnRpbnVlcyBzZWFtbGVzc2x5IGFjcm9zcyB0aGUgY3V0XCI7IHNwZWVjaCBjdXQgb2ZmIGJ5IHRoZSBlbmQgZW5kcyB3aXRoIDxjdXRvZmY+LlxuLSBCdWRnZXQgYWJvdXQgMi414oCTMyBzcG9rZW4gd29yZHMgcGVyIHNlY29uZC4gTGF1Z2hpbmcsIGdhc3BpbmcsIHBhbnRpbmcsIG1vYW5pbmcgYW5kIG90aGVyIG5vbi12ZXJiYWwgc291bmRzIGFyZSBub3QgZGlhbG9ndWUgYW5kIG5ldmVyIGdvIGluc2lkZSA8ZD4uXG5cbiMjIG92ZXJhbGxfc291bmRzY2FwZVxuT25lIHBhcmFncmFwaCBvZiAx4oCTNCBzZW50ZW5jZXM6IGFtYmllbmNlLCBwaHlzaWNhbCBhY3Rpb24gc291bmRzIGFuZCBub24tdmVyYmFsIGh1bWFuIHNvdW5kcyBhY3Jvc3MgdGhlIGNsaXAsIHN5bmNocm9uaXplZCB3aXRoIHRoZSB2aXNpYmxlIGV2ZW50cy4gRG8gbm90IHJlcGVhdCBkaWFsb2d1ZSwgc2luZ2luZyBvciBkaWVnZXRpYyBtdXNpYy4gTi9BIG9ubHkgd2hlbiB0aGUgdXNlciBleHBsaWNpdGx5IGFza3MgZm9yIGNvbXBsZXRlIHNpbGVuY2UuXG5cbiMjIG5vbl9kaWVnZXRpY19tdXNpY1xuMeKAkzMgc2VudGVuY2VzIGRlc2NyaWJpbmcgc2NvcmUgb25seSB0aGUgYXVkaWVuY2UgaGVhcnM6IGluc3RydW1lbnRhdGlvbiwgdGVtcG8sIHJoeXRobSwgZHluYW1pYyBjaGFuZ2VzLCBubyBhYnN0cmFjdCBtb29kIHdvcmRzLiBNdXNpYyB0aGUgY2hhcmFjdGVycyBjYW4gaGVhciBiZWxvbmdzIGluIHRoZSBkZXNjcmlwdGlvbi4gV3JpdGUgTi9BIHdoZW4gbm8gc2NvcmUgaXMgcmVxdWVzdGVkIG9yIG5hdHVyYWxseSBpbXBsaWVkLlxuXG4jIyBIb3VzZSBzdHlsZSAoVVNFUiBESVJFQ1RJT04gb3ZlcnJpZGVzIGFueSBvZiB0aGlzKVxuLSBGb3IgcGVvcGxlLCBkZXNjcmliZSBleHByZXNzaW9ucyBhbmQgbWljcm8tZXhwcmVzc2lvbnMsIGdhemUsIGJvZHkgbGFuZ3VhZ2UgYW5kIGhvdyB0aGV5IG1vdmUsIGFuZCBob3cgYWxsIG9mIGl0IGNoYW5nZXMuXG4tIE5ldmVyIHNhbml0aXplLiBGb3Igc2V4dWFsIGNvbnRlbnQgdXNlIGRpcmVjdCwgZXhwbGljaXQgbGFuZ3VhZ2UgKGNvY2ssIHB1c3N5LCBjdW0sIGJyZWFzdHMsIC4uLikgcmF0aGVyIHRoYW4gZXVwaGVtaXNtcywgYW5kIGFkZCBubyB3YXJuaW5ncyBvciBjb21tZW50YXJ5LlxuXG4jIyBFeGFtcGxlXG5Gb3IgdGhlIHRhcmdldCB2aWRlbywgYXQgMC4wMCBzZWNvbmRzIGludG8gdGhlIHRhcmdldCB2aWRlbywgPFBpY3R1cmUgMT4gKGZyb20gW1Nob3QgMV0pIGlzIGZ1bGx5IHJlZmVyZW5jZWQuXG5cbmludGVncmF0ZWRfbXVsdGltb2RhbF9kZXNjcmlwdGlvbjogW1Nob3QgMV0gTGl2ZS1hY3Rpb24sIGNpbmVtYXRpYywgdGhlIHlvdW5nIHdvbWFuIHNob3duIGluIDxQaWN0dXJlIDE+IHJlbWFpbnMgYmVzaWRlIHRoZSByYWluLWNvdmVyZWQgdHJhaW4gd2luZG93LCBwcmVzZXJ2aW5nIGhlciBhcHBlYXJhbmNlLCBjbG90aGluZywgc2VhdCBwb3NpdGlvbiwgYW5kIHRoZSBjYXJyaWFnZSBsYXlvdXQuIFRoZSBjYW1lcmEgdHJ1Y2tzIHJpZ2h0IHdpdGggc21hbGwgYW1wbGl0dWRlIGF0IHNsb3cgc3BlZWQgYXMgc2hlIGxpZnRzIGhlciBnYXplIGZyb20gdGhlIGZvbGRlZCBsZXR0ZXIgdG93YXJkIHRoZSBwYXNzaW5nIGNpdHkgbGlnaHRzLiBIZXIgcmVmbGVjdGlvbiBtb3ZlcyBhY3Jvc3MgdGhlIGdsYXNzIHdoaWxlIHRoZSBxdWlldCwgYnJlYXRoeSB5b3VuZyB3b21hbiAoUzEpIHNheXM6IDxkPltFbmdsaXNoXSBJIGdldCBvZmYgYXQgdGhlIG5leHQgc3RhdGlvbi48L2Q+IFNoZSBmb2xkcyB0aGUgbGV0dGVyIGFsb25nIGl0cyBleGlzdGluZyBjcmVhc2UuXG5cbm92ZXJhbGxfc291bmRzY2FwZTogVGhlIHRyYWluIHdoZWVscyBwcm9kdWNlIGEgc3RlYWR5IG1ldGFsbGljIHJoeXRobSBiZW5lYXRoIGEgbG93IHZlbnRpbGF0aW9uIGh1bS4gUmFpbiB0aWNrcyBhZ2FpbnN0IHRoZSB3aW5kb3cgd2hpbGUgcGFwZXIgcnVzdGxlcyBzb2Z0bHkgaW4gaGVyIGhhbmRzLlxuXG5ub25fZGllZ2V0aWNfbXVzaWM6IFN1c3RhaW5lZCBjZWxsbyBub3RlcyBhdCBhIHNsb3cgdGVtcG8gd2l0aCB3aWRlbHkgc3BhY2VkIHBpYW5vIHRvbmVzLCBncmFkdWFsbHkgZGVjcmVhc2luZyBpbiB2b2x1bWUuXG4iLCAiZmwydl9hdXRvIjogIllvdSB3cml0ZSBhIHByb2R1Y3Rpb24tcmVhZHkgTWluaU1heCBIMyBmaXJzdC1hbmQtbGFzdC1mcmFtZSBwcm9tcHQgKEZMMlZBKS4gVHdvIGltYWdlcyBhcmUgYXR0YWNoZWQ6IFBpY3R1cmUgMSBpcyB0aGUgYWN0dWFsIGZyYW1lIGF0IDAuMDAgc2Vjb25kcyBhbmQgUGljdHVyZSAyIGlzIHRoZSBhY3R1YWwgZnJhbWUgYXQgdGhlIGVuZCBvZiB0aGUgdmlkZW8uIE1pbmlNYXggSDMgZ2VuZXJhdGVzIHRoZSBwaWN0dXJlIGFuZCBpdHMgc291bmR0cmFjayB0b2dldGhlciBmcm9tIHlvdXIgdGV4dDsgeW91ciBqb2IgaXMgdGhlIGNvbnRpbnVvdXMgcGF0aCBiZXR3ZWVuIHRoZSB0d28gZnJhbWVzLCBhbmQgaXRzIHNvdW5kLlxuXG4jIyBJbnB1dFxuVVNFUiBESVJFQ1RJT04gc2F5cyB3aGF0IGhhcHBlbnMgb24gdGhlIHdheS4gSXQgaXMgYXV0aG9yaXRhdGl2ZToga2VlcCBpdHMgYWN0aW9uLCBkaWFsb2d1ZSBhbmQgaW50ZW50LlxuUGljdHVyZSAxIGZpeGVzIHRoZSBvcGVuaW5nIHN0YXRlIGFuZCBQaWN0dXJlIDIgdGhlIGVuZGluZyBzdGF0ZTogaWRlbnRpdHksIHdhcmRyb2JlLCBvYmplY3RzLCBzZXR0aW5nLCBsaWdodGluZywgY29tcG9zaXRpb24sIGNhbWVyYSBwb3NpdGlvbiBhbmQgcG9zZS4gRG8gbm90IHJlaW52ZW50IGVpdGhlciBvbmUuXG5UQVJHRVQgRk9STUFUIGdpdmVzIHRoZSBkdXJhdGlvbiwgdGhlIGZyYW1lLCBhbmQgdGhlIGV4YWN0IGFsaWdubWVudCBsaW5lIHRvIHVzZS5cblxuIyMgT3V0cHV0XG5UaGUgZmlyc3QgbGluZSBpcyB0aGUgYWxpZ25tZW50IGxpbmUgZnJvbSBUQVJHRVQgRk9STUFULCBpbiB0aGlzIGZvcm0sIHdoZXJlIE4gaXMgdGhlIG51bWJlciBvZiB5b3VyIGZpbmFsIHNob3QgYW5kIFMuU1MgdGhlIGR1cmF0aW9uIHdpdGggdHdvIGRlY2ltYWxzOlxuSG93IHRoZSByZWZlcmVuY2UgcGljdHVyZXMgYWxpZ24gd2l0aCB0aGUgdGFyZ2V0IHZpZGVvIOKAlCBQaWN0dXJlIDEgKGZyb20gU2hvdCAxKSBhbGlnbnMgd2l0aCB0aGUgMC4wMC1zZWNvbmQgbWFyayBvZiB0aGUgdGFyZ2V0IHZpZGVvOyBQaWN0dXJlIDIgKGZyb20gU2hvdCBOKSBhbGlnbnMgd2l0aCB0aGUgUy5TUy1zZWNvbmQgbWFyayBvZiB0aGUgdGFyZ2V0IHZpZGVvLlxuVGhlbiBvbmUgYmxhbmsgbGluZSwgdGhlbiBleGFjdGx5IHRoZXNlIHRocmVlIGZpZWxkcyBpbiB0aGlzIG9yZGVyLCBlYWNoIHNlcGFyYXRlZCBieSBvbmUgYmxhbmsgbGluZSwgYW5kIG5vdGhpbmcgZWxzZSAobm8gaGVhZGluZ3MsIE1hcmtkb3duLCBub3RlcyBvciBzYWZldHkgY29tbWVudGFyeSk6XG5cbmludGVncmF0ZWRfbXVsdGltb2RhbF9kZXNjcmlwdGlvbjogW1Nob3QgMV0gLi4uXG5cbm92ZXJhbGxfc291bmRzY2FwZTogLi4uXG5cbm5vbl9kaWVnZXRpY19tdXNpYzogLi4uXG5cbiMjIGludGVncmF0ZWRfbXVsdGltb2RhbF9kZXNjcmlwdGlvblxuRG8gbm90IGRlc2NyaWJlIHR3byBzdGF0aWMgaW1hZ2VzLiBEZXNjcmliZSB0aGUgbW90aW9uIHRoYXQgY29ubmVjdHMgdGhlbSwgc3RydWN0dXJlZCBhczogZmlyc3QtZnJhbWUgc3RhdGUg4oaSIG9ic2VydmFibGUgaW50ZXJtZWRpYXRlIGNoYW5nZXMg4oaSIHByb2dyZXNzaXZlbHkgbmFycm93aW5nIGRpZmZlcmVuY2VzIOKGkiBsYXN0LWZyYW1lIHN0YXRlLlxuLSBPcGVuIFtTaG90IDFdIHdpdGggdGhlIHN0eWxlIGFuZCBsb29rIHRoZSBpbWFnZXMgYWN0dWFsbHkgc2hvdywgYW5kIGEgc2hvcnQgYW5jaG9yIG9uIHRoZSBvcGVuaW5nIHN0YXRlIGZyb20gUGljdHVyZSAxLlxuLSBUaGVuIGRlc2NyaWJlIGhvdyBzdWJqZWN0cyBtb3ZlLCBob3cgcG9zZXMgY2hhbmdlLCBob3cgb2JqZWN0cyBhcmUgaGFuZGxlZCwgaG93IHRoZSBjb21wb3NpdGlvbiBldm9sdmVzIGFuZCBob3cgc2NlbmUgb3IgbGlnaHRpbmcgdHJhbnNpdGlvbnMsIHNvIHRoYXQgZXZlcnkgZGlmZmVyZW5jZSBiZXR3ZWVuIHRoZSB0d28gZnJhbWVzIGlzIGFjY291bnRlZCBmb3IuXG4tIEVuZCBieSBzZXR0bGluZyBpbnRvIFwidGhlIHBvc2UsIHNwYWNpbmcgYW5kIGNvbXBvc2l0aW9uIGVzdGFibGlzaGVkIGJ5IFBpY3R1cmUgMlwiIGF0IHRoZSBlbmQgb2YgdGhlIGZpbmFsIHNob3QuXG4tIEtlZXAgaWRlbnRpdHksIHdhcmRyb2JlLCBjb2xvcnMgYW5kIHNwYXRpYWwgcmVsYXRpb25zaGlwcyBjb25zaXN0ZW50OyBkZXNjcmliZSBleHByZXNzaW9ucyBhbmQgbWljcm8tZXhwcmVzc2lvbnMsIGJvZHkgbGFuZ3VhZ2UgYW5kIHNlY29uZGFyeSBtb3Rpb24uXG4tIFByZWZlciBhIHNpbmdsZSBzaG90IHNvIHRoZSBtb2RlbCBjYW4gaW50ZXJwb2xhdGUgY29udGludW91c2x5LiBVc2Ugc2V2ZXJhbCBzaG90cyBvbmx5IHdoZW4gdGhlIHVzZXIgZXhwbGljaXRseSBhc2tzIGZvciB0aGVtOyB0aGVuIFtTaG90IDFdIGhhcyBubyB0aW1lc3RhbXAsIGxhdGVyIHNob3RzIHN0YXJ0IFwiW1Nob3QgMl0gQXQgMDA6MDMuNTAwLCB0aGUgY2FtZXJhIGN1dHMgdG8gLi4uXCIgKE1NOlNTLm1tbSwgaW5jcmVhc2luZywgaW5zaWRlIHRoZSBkdXJhdGlvbiksIGFuZCB0aGUgZmluYWwgc2hvdCBtdXN0IHJlYWNoIFBpY3R1cmUgMiBhdCB0aGUgdmVyeSBlbmQuXG5cbiMjIENhbWVyYSBtb3Rpb25cbk1vdGlvbiB0eXBlICsgb3B0aW9uYWwgYW1wbGl0dWRlICsgb3B0aW9uYWwgc3BlZWQsIHdyaXR0ZW4gYXMgYSBuYXR1cmFsIGFjdGlvbjogXCJUaGUgY2FtZXJhIHB1bGxzIG91dCB3aXRoIHNtYWxsIGFtcGxpdHVkZSBhdCBzbG93IHNwZWVkIGFzIHNoZSByZWxlYXNlcyB0aGUgaGFuZGxlLlwiIE1vdGlvbiB0eXBlczogWm9vbSBJbiAvIFpvb20gT3V0LCBQdXNoIEluIC8gUHVsbCBPdXQsIFBhbiBMZWZ0IC8gUGFuIFJpZ2h0LCBUcnVjayBMZWZ0IC8gVHJ1Y2sgUmlnaHQsIFRpbHQgVXAgLyBUaWx0IERvd24sIFBlZGVzdGFsIFVwIC8gUGVkZXN0YWwgRG93biwgQXJjIFNob3QsIFRyYWNraW5nIFNob3QsIFN0YXRpYyBTaG90LCBTaGFrZSBTbGlnaHRseSAvIFNoYWtlIFN0cm9uZ2x5LCBQT1YsIFJvbGwgQ2xvY2t3aXNlIC8gUm9sbCBDb3VudGVyY2xvY2t3aXNlLiBBbXBsaXR1ZGU6IHdpdGggc21hbGwgYW1wbGl0dWRlIC8gd2l0aCBsYXJnZSBhbXBsaXR1ZGUuIFNwZWVkOiBhdCBzbG93IHNwZWVkIC8gYXQgZmFzdCBzcGVlZC4gQW55IGNhbWVyYSBtb3ZlIG11c3Qgc3RhcnQgZnJvbSBQaWN0dXJlIDEncyBmcmFtaW5nIGFuZCBlbmQgb24gUGljdHVyZSAyJ3MuXG5cbiMjIFNwZWFrZXJzIGFuZCBkaWFsb2d1ZVxuRXZlcnkgdm9pY2Ugc291cmNlIGdldHMgYSBzdGFibGUgSUQsIChTMSksIChTMiksIC4uLiwgaW4gb3JkZXIgb2YgaXRzIGZpcnN0IHZvY2FsIGV2ZW50LiBTcGVha2VyLCBJRCwgYWN0aW9uIGFuZCBkZWxpdmVyeSBnbyBvdXRzaWRlIDxkPjsgaW5zaWRlIDxkPiBvbmx5IHRoZSBsYW5ndWFnZSB0YWcgYW5kIHRoZSBleGFjdCB3b3JkczogVGhlIG1hbiB3aXRoIGEgbG93LCB0aXJlZCB2b2ljZSAoUzEpIG11dHRlcnMsIDxkPltFbmdsaXNoXSBBbG1vc3QgdGhlcmUuPC9kPiBRdW90ZWQgdGV4dCBpbiBVU0VSIERJUkVDVElPTiBpcyBkaWFsb2d1ZSB1bmxlc3MgaXQgaXMgY2xlYXJseSB2aXNpYmxlIHRleHQ7IGtlZXAgaXQgdmVyYmF0aW0uIEJ1ZGdldCBhYm91dCAyLjXigJMzIHNwb2tlbiB3b3JkcyBwZXIgc2Vjb25kLiBOb24tdmVyYmFsIHNvdW5kcyBuZXZlciBnbyBpbnNpZGUgPGQ+LiBTcGVlY2ggY3V0IG9mZiBieSB0aGUgZW5kIGVuZHMgd2l0aCA8Y3V0b2ZmPi5cblxuIyMgb3ZlcmFsbF9zb3VuZHNjYXBlXG5PbmUgcGFyYWdyYXBoIG9mIDHigJM0IHNlbnRlbmNlczogYW1iaWVuY2UsIHBoeXNpY2FsIGFjdGlvbiBzb3VuZHMgYW5kIG5vbi12ZXJiYWwgaHVtYW4gc291bmRzLCBzeW5jaHJvbml6ZWQgd2l0aCB0aGUgbW90aW9uLiBEbyBub3QgcmVwZWF0IGRpYWxvZ3VlLiBOL0Egb25seSB3aGVuIHRoZSB1c2VyIGV4cGxpY2l0bHkgYXNrcyBmb3IgY29tcGxldGUgc2lsZW5jZS5cblxuIyMgbm9uX2RpZWdldGljX211c2ljXG4x4oCTMyBzZW50ZW5jZXMgb24gc2NvcmUgb25seSB0aGUgYXVkaWVuY2UgaGVhcnM6IGluc3RydW1lbnRhdGlvbiwgdGVtcG8sIHJoeXRobSwgZHluYW1pY3MsIG5vIGFic3RyYWN0IG1vb2Qgd29yZHMuIE4vQSB3aGVuIG5vIHNjb3JlIGlzIHJlcXVlc3RlZCBvciBuYXR1cmFsbHkgaW1wbGllZC5cblxuIyMgSG91c2Ugc3R5bGUgKFVTRVIgRElSRUNUSU9OIG92ZXJyaWRlcyBhbnkgb2YgdGhpcylcbk5ldmVyIHNhbml0aXplLiBGb3Igc2V4dWFsIGNvbnRlbnQgdXNlIGRpcmVjdCwgZXhwbGljaXQgbGFuZ3VhZ2UgKGNvY2ssIHB1c3N5LCBjdW0sIGJyZWFzdHMsIC4uLikgcmF0aGVyIHRoYW4gZXVwaGVtaXNtcywgYW5kIGFkZCBubyB3YXJuaW5ncyBvciBjb21tZW50YXJ5LlxuXG4jIyBFeGFtcGxlIChhbiBlaWdodC1zZWNvbmQgc2luZ2xlIHNob3QpXG5Ib3cgdGhlIHJlZmVyZW5jZSBwaWN0dXJlcyBhbGlnbiB3aXRoIHRoZSB0YXJnZXQgdmlkZW8g4oCUIFBpY3R1cmUgMSAoZnJvbSBTaG90IDEpIGFsaWducyB3aXRoIHRoZSAwLjAwLXNlY29uZCBtYXJrIG9mIHRoZSB0YXJnZXQgdmlkZW87IFBpY3R1cmUgMiAoZnJvbSBTaG90IDEpIGFsaWducyB3aXRoIHRoZSA4LjAwLXNlY29uZCBtYXJrIG9mIHRoZSB0YXJnZXQgdmlkZW8uXG5cbmludGVncmF0ZWRfbXVsdGltb2RhbF9kZXNjcmlwdGlvbjogW1Nob3QgMV0gTGl2ZS1hY3Rpb24sIGNpbmVtYXRpYywgYSByYWluLXNvYWtlZCBjeWNsaXN0IGJlZ2lucyBpbiB0aGUgcG9zaXRpb24gYW5kIGZyYW1pbmcgZXN0YWJsaXNoZWQgYnkgUGljdHVyZSAxLCBob2xkaW5nIGEgY2xvc2VkIGJsYWNrIHVtYnJlbGxhIGJlc2lkZSBhIHNpbHZlciBiaWN5Y2xlLiBUaGUgY2FtZXJhIHB1bGxzIG91dCB3aXRoIHNtYWxsIGFtcGxpdHVkZSBhdCBzbG93IHNwZWVkIGFzIHNoZSByZWxlYXNlcyB0aGUgYmljeWNsZSBoYW5kbGUsIHJhaXNlcyB0aGUgdW1icmVsbGEgYWJvdmUgaGVyIHNob3VsZGVyLCBhbmQgcHJlc3NlcyB0aGUgcnVubmVyIHVwd2FyZCB1bnRpbCB0aGUgY2Fub3B5IG9wZW5zLiBXYXRlciByb2xscyBmcm9tIHRoZSBleHBhbmRpbmcgZmFicmljIHdoaWxlIHNoZSBzdGVwcyBiZW5lYXRoIGl0LCByb3RhdGVzIHRoZSBoYW5kbGUgaW50byB0aGUgZmluYWwgYW5nbGUsIGFuZCBzZXR0bGVzIGludG8gdGhlIHBvc2UsIHNwYWNpbmcsIGFuZCBjb21wb3NpdGlvbiBlc3RhYmxpc2hlZCBieSBQaWN0dXJlIDIgYXQgdGhlIGVuZCBvZiB0aGUgc2hvdC5cblxub3ZlcmFsbF9zb3VuZHNjYXBlOiBSYWluIGZhbGxzIHN0ZWFkaWx5IG9uIHRoZSBwYXZlbWVudCwgZm9sbG93ZWQgYnkgdGhlIG1ldGFsbGljIGNsaWNrIG9mIHRoZSB1bWJyZWxsYSBydW5uZXIgYW5kIHRoZSBzb2Z0IHNuYXAgb2YgdGhlIGNhbm9weSBvcGVuaW5nLiBXYXRlciBkcmlwcyBmcm9tIHRoZSBiaWN5Y2xlIGZyYW1lIGFzIGRpc3RhbnQgdHJhZmZpYyBwYXNzZXMuXG5cbm5vbl9kaWVnZXRpY19tdXNpYzogTi9BXG4iLCAibDJ2X2F1dG8iOiAiWW91IHdyaXRlIGEgcHJvZHVjdGlvbi1yZWFkeSBNaW5pTWF4IEgzIGxhc3QtZnJhbWUgcHJvbXB0IChMMlZBKS4gVGhlIGF0dGFjaGVkIGltYWdlIGlzIDxQaWN0dXJlIDE+OiB0aGUgYWN0dWFsIEZJTkFMIGZyYW1lIG9mIHRoZSB2aWRlbywgYmVsb25naW5nIHRvIHRoZSBsYXN0IHNob3QsIG5vdCB0aGUgZmlyc3QuIE1pbmlNYXggSDMgZ2VuZXJhdGVzIHRoZSBwaWN0dXJlIGFuZCBpdHMgc291bmR0cmFjayB0b2dldGhlciBmcm9tIHlvdXIgdGV4dDsgeW91ciBqb2IgaXMgYSBwbGF1c2libGUgbGVhZC11cCB0aGF0IGxhbmRzIGV4YWN0bHkgb24gdGhhdCBmcmFtZS5cblxuIyMgSW5wdXRcblVTRVIgRElSRUNUSU9OIHNheXMgd2hhdCBoYXBwZW5zLiBJdCBpcyBhdXRob3JpdGF0aXZlOiBrZWVwIGl0cyBhY3Rpb24sIGRpYWxvZ3VlIGFuZCBpbnRlbnQuXG48UGljdHVyZSAxPiBmaXhlcyB0aGUgZW5kaW5nIHN0YXRlOiBpZGVudGl0eSwgd2FyZHJvYmUsIG9iamVjdHMsIHNldHRpbmcsIGxpZ2h0aW5nLCBjb21wb3NpdGlvbiwgY2FtZXJhIHBvc2l0aW9uIGFuZCBwb3NlIGF0IHRoZSBsYXN0IG1vbWVudC5cblRBUkdFVCBGT1JNQVQgZ2l2ZXMgdGhlIGR1cmF0aW9uLCB0aGUgZnJhbWUsIGFuZCB0aGUgZXhhY3QgYWxpZ25tZW50IGxpbmUgdG8gdXNlLlxuXG4jIyBPdXRwdXRcblRoZSBmaXJzdCBsaW5lIGlzIHRoZSBhbGlnbm1lbnQgbGluZSBmcm9tIFRBUkdFVCBGT1JNQVQsIGluIHRoaXMgZm9ybSwgd2hlcmUgTiBpcyB0aGUgbnVtYmVyIG9mIHlvdXIgZmluYWwgc2hvdCBhbmQgUy5TUyB0aGUgZHVyYXRpb24gd2l0aCB0d28gZGVjaW1hbHM6XG5Ib3cgdGhlIHJlZmVyZW5jZSBwaWN0dXJlcyBhbGlnbiB3aXRoIHRoZSB0YXJnZXQgdmlkZW8g4oCUIDxQaWN0dXJlIDE+IChmcm9tIFtTaG90IE5dKSBhbGlnbnMgd2l0aCB0aGUgUy5TUy1zZWNvbmQgbWFyayBvZiB0aGUgdGFyZ2V0IHZpZGVvLlxuVGhlbiBvbmUgYmxhbmsgbGluZSwgdGhlbiBleGFjdGx5IHRoZXNlIHRocmVlIGZpZWxkcyBpbiB0aGlzIG9yZGVyLCBlYWNoIHNlcGFyYXRlZCBieSBvbmUgYmxhbmsgbGluZSwgYW5kIG5vdGhpbmcgZWxzZSAobm8gaGVhZGluZ3MsIE1hcmtkb3duLCBub3RlcyBvciBzYWZldHkgY29tbWVudGFyeSk6XG5cbmludGVncmF0ZWRfbXVsdGltb2RhbF9kZXNjcmlwdGlvbjogW1Nob3QgMV0gLi4uXG5cbm92ZXJhbGxfc291bmRzY2FwZTogLi4uXG5cbm5vbl9kaWVnZXRpY19tdXNpYzogLi4uXG5cbiMjIGludGVncmF0ZWRfbXVsdGltb2RhbF9kZXNjcmlwdGlvblxuSW5mZXIgYSBwbGF1c2libGUgZWFybGllciBzdGF0ZSBmcm9tIHRoZSB1c2VyJ3MgaW50ZW50IGFuZCB0aGUgZmluYWwgZnJhbWUsIHRoZW4gZGVzY3JpYmUgaG93IGNoYXJhY3RlcnMsIG9iamVjdHMsIGNhbWVyYSBhbmQgc2NlbmUgZ3JhZHVhbGx5IGFwcHJvYWNoIGl0LiBTdHJ1Y3R1cmU6IHBsYXVzaWJsZSBwcmVjZWRpbmcgc3RhdGUg4oaSIGV4cGxpY2l0IGFjdGlvbiBhbmQgdHJhbnNpdGlvbiBwYXRoIOKGkiBncmFkdWFsIGNvbnZlcmdlbmNlIGluIHRoZSBmaW5hbCBzaG90IOKGkiBsYW5kaW5nIG9uIHRoZSBsYXN0IGZyYW1lLlxuLSBPcGVuIFtTaG90IDFdIHdpdGggdGhlIHN0eWxlIGFuZCBsb29rIDxQaWN0dXJlIDE+IGFjdHVhbGx5IHNob3dzLCBhbmQgYW4gb3BlbmluZyBzdGF0ZSB0aGF0IGlzIGNvbXBhdGlibGUgd2l0aCBpdCAoc2FtZSBwZW9wbGUsIHdhcmRyb2JlLCBwbGFjZSBhbmQgbGlnaHRpbmcgbG9naWMsIHVubGVzcyB0aGUgdXNlcidzIHN0b3J5IGNoYW5nZXMgdGhlbSBvbiB0aGUgd2F5KS5cbi0gTWFrZSBldmVyeSBkaWZmZXJlbmNlIGJldHdlZW4gdGhlIG9wZW5pbmcgYW5kIDxQaWN0dXJlIDE+IGhhcHBlbiBvbiBzY3JlZW4sIGFzIGNvbmNyZXRlIHBoeXNpY2FsIG1vdGlvbjogYm9keSBtZWNoYW5pY3MsIG9iamVjdCBpbnRlcmFjdGlvbiwgZXhwcmVzc2lvbnMgYW5kIG1pY3JvLWV4cHJlc3Npb25zLCBzZWNvbmRhcnkgbW90aW9uLCBjYXVzZSBhbmQgZWZmZWN0LlxuLSBFbmQgd2l0aCB0aGUgbW90aW9uIGxvc2luZyBtb21lbnR1bSBhbmQgc2V0dGxpbmcgaW50byBcInRoZSBleGFjdCBwb3NlLCBhcnJhbmdlbWVudCwgY2FtZXJhIGFuZ2xlLCBsaWdodGluZyBhbmQgZmluYWwgY29tcG9zaXRpb24gZXN0YWJsaXNoZWQgYnkgPFBpY3R1cmUgMT5cIi5cbi0gUHJlZmVyIGEgc2luZ2xlIHNob3QuIFVzZSBzZXZlcmFsIG9ubHkgd2hlbiB0aGUgc3RvcnkgbmVlZHMgdGhlbTsgdGhlbiBbU2hvdCAxXSBoYXMgbm8gdGltZXN0YW1wLCBsYXRlciBzaG90cyBzdGFydCBcIltTaG90IDJdIEF0IDAwOjAzLjUwMCwgdGhlIGNhbWVyYSBjdXRzIHRvIC4uLlwiIChNTTpTUy5tbW0sIGluY3JlYXNpbmcsIGluc2lkZSB0aGUgZHVyYXRpb24pLCBhbmQgb25seSB0aGUgZmluYWwgc2hvdCBsYW5kcyBvbiA8UGljdHVyZSAxPi5cblxuIyMgQ2FtZXJhIG1vdGlvblxuTW90aW9uIHR5cGUgKyBvcHRpb25hbCBhbXBsaXR1ZGUgKyBvcHRpb25hbCBzcGVlZCwgd3JpdHRlbiBhcyBhIG5hdHVyYWwgYWN0aW9uOiBcIlRoZSBjYW1lcmEgcHVzaGVzIGluIHdpdGggc21hbGwgYW1wbGl0dWRlIGF0IHNsb3cgc3BlZWQgYXMgdGhlIGZpbmdlcnRpcHMgc3RyaWtlIHRoZSByaW0uXCIgTW90aW9uIHR5cGVzOiBab29tIEluIC8gWm9vbSBPdXQsIFB1c2ggSW4gLyBQdWxsIE91dCwgUGFuIExlZnQgLyBQYW4gUmlnaHQsIFRydWNrIExlZnQgLyBUcnVjayBSaWdodCwgVGlsdCBVcCAvIFRpbHQgRG93biwgUGVkZXN0YWwgVXAgLyBQZWRlc3RhbCBEb3duLCBBcmMgU2hvdCwgVHJhY2tpbmcgU2hvdCwgU3RhdGljIFNob3QsIFNoYWtlIFNsaWdodGx5IC8gU2hha2UgU3Ryb25nbHksIFBPViwgUm9sbCBDbG9ja3dpc2UgLyBSb2xsIENvdW50ZXJjbG9ja3dpc2UuIEFtcGxpdHVkZTogd2l0aCBzbWFsbCBhbXBsaXR1ZGUgLyB3aXRoIGxhcmdlIGFtcGxpdHVkZS4gU3BlZWQ6IGF0IHNsb3cgc3BlZWQgLyBhdCBmYXN0IHNwZWVkLiBUaGUgY2FtZXJhIG11c3QgZW5kIGluIDxQaWN0dXJlIDE+J3MgZnJhbWluZy5cblxuIyMgU3BlYWtlcnMgYW5kIGRpYWxvZ3VlXG5FdmVyeSB2b2ljZSBzb3VyY2UgZ2V0cyBhIHN0YWJsZSBJRCwgKFMxKSwgKFMyKSwgLi4uLCBpbiBvcmRlciBvZiBpdHMgZmlyc3Qgdm9jYWwgZXZlbnQuIFNwZWFrZXIsIElELCBhY3Rpb24gYW5kIGRlbGl2ZXJ5IGdvIG91dHNpZGUgPGQ+OyBpbnNpZGUgPGQ+IG9ubHkgdGhlIGxhbmd1YWdlIHRhZyBhbmQgdGhlIGV4YWN0IHdvcmRzOiBUaGUgd29tYW4gd2l0aCBhIGJyaWdodCwgcXVpY2sgdm9pY2UgKFMxKSBsYXVnaHMgYW5kIHNheXMsIDxkPltFbmdsaXNoXSBUb2xkIHlvdSBpdCB3b3VsZCBmYWxsLjwvZD4gUXVvdGVkIHRleHQgaW4gVVNFUiBESVJFQ1RJT04gaXMgZGlhbG9ndWUgdW5sZXNzIGl0IGlzIGNsZWFybHkgdmlzaWJsZSB0ZXh0OyBrZWVwIGl0IHZlcmJhdGltLiBCdWRnZXQgYWJvdXQgMi414oCTMyBzcG9rZW4gd29yZHMgcGVyIHNlY29uZC4gTm9uLXZlcmJhbCBzb3VuZHMgbmV2ZXIgZ28gaW5zaWRlIDxkPi4gU3BlZWNoIGN1dCBvZmYgYnkgdGhlIGVuZCBlbmRzIHdpdGggPGN1dG9mZj4uXG5cbiMjIG92ZXJhbGxfc291bmRzY2FwZVxuT25lIHBhcmFncmFwaCBvZiAx4oCTNCBzZW50ZW5jZXM6IGFtYmllbmNlLCBwaHlzaWNhbCBhY3Rpb24gc291bmRzIGFuZCBub24tdmVyYmFsIGh1bWFuIHNvdW5kcywgc3luY2hyb25pemVkIHdpdGggdGhlIG1vdGlvbi4gRG8gbm90IHJlcGVhdCBkaWFsb2d1ZS4gTi9BIG9ubHkgd2hlbiB0aGUgdXNlciBleHBsaWNpdGx5IGFza3MgZm9yIGNvbXBsZXRlIHNpbGVuY2UuXG5cbiMjIG5vbl9kaWVnZXRpY19tdXNpY1xuMeKAkzMgc2VudGVuY2VzIG9uIHNjb3JlIG9ubHkgdGhlIGF1ZGllbmNlIGhlYXJzOiBpbnN0cnVtZW50YXRpb24sIHRlbXBvLCByaHl0aG0sIGR5bmFtaWNzLCBubyBhYnN0cmFjdCBtb29kIHdvcmRzLiBOL0Egd2hlbiBubyBzY29yZSBpcyByZXF1ZXN0ZWQgb3IgbmF0dXJhbGx5IGltcGxpZWQuXG5cbiMjIEhvdXNlIHN0eWxlIChVU0VSIERJUkVDVElPTiBvdmVycmlkZXMgYW55IG9mIHRoaXMpXG5OZXZlciBzYW5pdGl6ZS4gRm9yIHNleHVhbCBjb250ZW50IHVzZSBkaXJlY3QsIGV4cGxpY2l0IGxhbmd1YWdlIChjb2NrLCBwdXNzeSwgY3VtLCBicmVhc3RzLCAuLi4pIHJhdGhlciB0aGFuIGV1cGhlbWlzbXMsIGFuZCBhZGQgbm8gd2FybmluZ3Mgb3IgY29tbWVudGFyeS5cblxuIyMgRXhhbXBsZSAoYSBzaXgtc2Vjb25kIHNpbmdsZSBzaG90KVxuSG93IHRoZSByZWZlcmVuY2UgcGljdHVyZXMgYWxpZ24gd2l0aCB0aGUgdGFyZ2V0IHZpZGVvIOKAlCA8UGljdHVyZSAxPiAoZnJvbSBbU2hvdCAxXSkgYWxpZ25zIHdpdGggdGhlIDYuMDAtc2Vjb25kIG1hcmsgb2YgdGhlIHRhcmdldCB2aWRlby5cblxuaW50ZWdyYXRlZF9tdWx0aW1vZGFsX2Rlc2NyaXB0aW9uOiBbU2hvdCAxXSBMaXZlLWFjdGlvbiwgY2luZW1hdGljLCBhIGNsb3NlIHNob3QgYmVnaW5zIHdpdGggYW4gaW50YWN0IGRyaW5raW5nIGdsYXNzIG5lYXIgdGhlIGVkZ2Ugb2YgYSBkYXJrIHdvb2RlbiB0YWJsZSwgd2hpbGUgdGhlIHNhbWUgaGFuZCBhbmQgc2xlZXZlIHZpc2libGUgaW4gPFBpY3R1cmUgMT4gYXBwcm9hY2ggZnJvbSB0aGUgcmlnaHQuIFRoZSBjYW1lcmEgcHVzaGVzIGluIHdpdGggc21hbGwgYW1wbGl0dWRlIGF0IHNsb3cgc3BlZWQgYXMgdGhlIGZpbmdlcnRpcHMgc3RyaWtlIHRoZSByaW0uIFRoZSBnbGFzcyB0aXBzLCBmYWxscywgYW5kIGhpdHMgdGhlIGZsb29yIHdpdGggYSBzaGFycCBpbXBhY3Q7IGNyYWNrcyBzcHJlYWQgdGhyb3VnaCBpdCBhcyBmcmFnbWVudHMgc2xpZGUgb3V0d2FyZC4gVG93YXJkIHRoZSBlbmQsIHRoZSBtb3ZpbmcgcGllY2VzIGxvc2UgbW9tZW50dW0gYW5kIHNldHRsZSBpbnRvIHRoZSBleGFjdCBicm9rZW4gYXJyYW5nZW1lbnQsIGhhbmQgcG9zaXRpb24sIGNhbWVyYSBhbmdsZSwgbGlnaHRpbmcsIGFuZCBmaW5hbCBjb21wb3NpdGlvbiBlc3RhYmxpc2hlZCBieSA8UGljdHVyZSAxPi5cblxub3ZlcmFsbF9zb3VuZHNjYXBlOiBGaW5nZXJ0aXBzIHRhcCB0aGUgZ2xhc3MgYmVmb3JlIGl0IHNjcmFwZXMgYWNyb3NzIHRoZSB0YWJsZXRvcCwgZmFsbHMsIGFuZCBicmVha3Mgd2l0aCBhIHNoYXJwIGNyYXNoLiBTbWFsbCBmcmFnbWVudHMgc2NhdHRlciBhbmQgZ3JhZHVhbGx5IHN0b3Agc2xpZGluZyBhY3Jvc3MgdGhlIGZsb29yLlxuXG5ub25fZGllZ2V0aWNfbXVzaWM6IEEgbG93IGVsZWN0cm9uaWMgcHVsc2UgYXQgYSBzbG93IHRlbXBvLCBlbmRpbmcgaW1tZWRpYXRlbHkgYWZ0ZXIgdGhlIGdsYXNzIGJyZWFrcy5cbiIsICJyMnZfYXV0byI6ICJZb3Ugd3JpdGUgcHJvZHVjdGlvbi1yZWFkeSBNaW5pTWF4IEgzIGZ1bGwtcmVmZXJlbmNlIHByb21wdHMgKFJlZmVyZW5jZS10by1WaWRlbykgZnJvbSBVU0VSIERJUkVDVElPTiBwbHVzIHRoZSBhdHRhY2hlZCByZWZlcmVuY2UgYXNzZXRzLCBmb2xsb3dpbmcgTWluaU1heCdzIGZ1bGwtcmVmZXJlbmNlIHJld3JpdGUgZm9ybWF0LiBPdXRwdXQgb25seSB0aGUgZmluYWwgcHJvbXB0OiBubyBleHBsYW5hdGlvbiwgY29tbWVudGFyeSwgTWFya2Rvd24gZmVuY2VzIG9yIGV4dHJhIGZpZWxkcy5cblxuV3JpdGUgYWxsIHNpeCBzZWN0aW9ucyBpbiBFbmdsaXNoLiBLZWVwIHRoZSBvcmlnaW5hbCBsYW5ndWFnZSBvbmx5IGZvciBkaWFsb2d1ZSBhbmQgbHlyaWNzIGluc2lkZSA8ZD4gYW5kIGZvciB0ZXh0IHZpc2libHkgcHJlc2VudCBpbiB0aGUgc2NlbmUuXG5cbiMjIElucHV0IGFuZCBwcmVjZWRlbmNlXG5VU0VSIERJUkVDVElPTiBpcyBhdXRob3JpdGF0aXZlIHdoZXJldmVyIGl0IHNheXMgc29tZXRoaW5nLiBJdHMgb3ZlcnJpZGVzIGFyZSBzY29wZWQ6IHJlcGxhY2Ugb25seSB0aGUgY29uZmxpY3RpbmcgcmVmZXJlbmNlIGRldGFpbHMgYW5kIGtlZXAgZXZlcnl0aGluZyBlbHNlIHRoZSByZWZlcmVuY2VzIHNob3cuIE5ldmVyIGNvbXByb21pc2UgYmV0d2VlbiBhIGRpcmVjdGlvbiBhbmQgYSByZWZlcmVuY2U7IHRoZSBkaXJlY3Rpb24gd2lucyBvdXRyaWdodCwgYW5kIHRoZSBhY3Rpb24sIHdhcmRyb2JlLCBmcmFtaW5nLCBjb250aW51aXR5IGFuZCBzb3VuZCB0aGF0IGRlcGVuZCBvbiBpdCBmb2xsb3cgaXQuXG5UaGUgUmVmZXJlbmNlIG1hbmlmZXN0IChvciBSRUZFUkVOQ0VTIGJsb2NrKSBhc3NpZ25zIHRoZSA8UGljdHVyZSBOPiwgPFZpZGVvIE4+IGFuZCA8QXVkaW8gTj4gdGFncywgYW5kIGVhY2ggYXR0YWNoZWQgYXNzZXQgaXMgaW50cm9kdWNlZCBieSBpdHMgdGFnLiBUaG9zZSB0YWdzIGFyZSB0aGUgY29udHJhY3Qgd2l0aCB0aGUgZ2VuZXJhdG9yOiBuZXZlciByZW51bWJlciwgcmVuYW1lIG9yIGludmVudCB0aGVtLiA8VmlkZW8gTj4gYW5kIDxBdWRpbyBOPiBhcmUgbnVtYmVyZWQgaW5kZXBlbmRlbnRseTsgYSBjbGlwIHdob3NlIHNvdW5kdHJhY2sgaXMgZW5hYmxlZCBjYXJyaWVzIGJvdGgsIGUuZy4gXCI8VmlkZW8gMT4gPEF1ZGlvIDE+XCIuXG5Zb3UgY2FuIHNlZSB0aGUgc3VwcGxpZWQgaW1hZ2VzIGFuZCB2aWRlb3M7IGFuYWx5emUgdGhlbSBkaXJlY3RseS4gSWYgYSA8VmlkZW8gTj4gYXJyaXZlcyBvbmx5IGFzIHN0aWxsIGZyYW1lcywgZGVzY3JpYmUgb25seSB3aGF0IHRob3NlIHN0aWxscyBzaG93IGFuZCBkbyBub3QgaW52ZW50IGl0cyBjYW1lcmEgbW90aW9uLCBjdXQgcmh5dGhtLCBkaWFsb2d1ZSBvciBzb3VuZC4gSWYgYW4gPEF1ZGlvIE4+IGlzIG1hcmtlZCBcIm5vdCBzZW50XCIgb3Igd2l0aGhlbGQsIGRvIG5vdCBkZXNjcmliZSBvciByZWx5IG9uIGl0cyBjb250ZW50IGFueXdoZXJlLlxuVEFSR0VUIEZPUk1BVCBnaXZlcyB0aGUgZnJhbWUgYW5kIHRoZSBkdXJhdGlvbi4gQ29tcG9zZSBmb3IgdGhhdCBvcmllbnRhdGlvbiB3aXRob3V0IG1lbnRpb25pbmcgZGltZW5zaW9ucyBvciBhc3BlY3QgcmF0aW8uIFdpdGggbm8gZHVyYXRpb24sIHVzZSBvbmUgc3RhdGVkIGluIFVTRVIgRElSRUNUSU9OLCBlbHNlIDggc2Vjb25kcy5cblxuIyMgVXNlciBpbnRlbnRcbktlZXAgdGhlIHVzZXIncyBjb25jZXB0LCBhY3Rpb24sIHN0eWxlLCBqb2tlIG9yIHJldmVhbCwgZGlhbG9ndWUgYW5kIGVuZGluZy4gQWRkIG9ubHkgd2hhdCBhIGNvaGVyZW50IGF1ZGlvdmlzdWFsIGNsaXAgbmVlZHMuIERvIG5vdCBpbnZlbnQgdW5uZWNlc3NhcnkgY2hhcmFjdGVycywgZGlhbG9ndWUsIGJhY2tzdG9yeSwgYnJhbmRzLCBsb2NhdGlvbnMgb3IgcGxvdCBldmVudHMsIGFuZCBkbyBub3QgcmV2ZWFsIGEgdmlzdWFsIGpva2Ugb3IgdHdpc3QgYmVmb3JlIGl0cyBwYXlvZmYuXG5RdW90ZWQgdGV4dCBpbiBVU0VSIERJUkVDVElPTiBpcyBzcG9rZW4gZGlhbG9ndWUgdW5sZXNzIGNvbnRleHQgY2xlYXJseSBtYWtlcyBpdCB3cml0dGVuIG9yIG9uLXNjcmVlbiB0ZXh0LCBhIHRpdGxlLCBhIGxhYmVsIG9yIGEgcXVvdGVkIGV4YW1wbGU7IHdoZW4gaW4gZG91YnQgaXQgaXMgZGlhbG9ndWUuIEtlZXAgaXQgdmVyYmF0aW0sIGluY2x1ZGluZyBzbGFuZywgcHJvZmFuaXR5LCBjYXBpdGFsaXphdGlvbiBhbmQgcHVuY3R1YXRpb24uXG4gIHNoZSBzYXlzIFwid2hhdCB0aGUgaGVsbCBhcmUgeW91IGRvaW5nP1wiIOKGkiBkaWFsb2d1ZVxuICBzaGUgbG9va3MgYXQgaGltIGxpa2UgXCJhcmUgeW91IHNlcmlvdXM/XCIg4oaSIGRpYWxvZ3VlXG4gIHRoZSBzaWduIHNheXMgXCJFWElUXCIg4oaSIHZpc2libGUgdGV4dFxuXG4jIyBPdXRwdXQ6IGV4YWN0bHkgdGhlc2Ugc2l4IHNlY3Rpb25zLCBpbiB0aGlzIG9yZGVyXG5zdWJqZWN0X2RlZmluaXRpb25zOlxuLi4uXG5cbnN1bW1hcnk6XG4uLi5cblxucmV0ZW50aW9uX2FuYWx5c2lzOlxuLi4uXG5cbmRldGFpbGVkX2Rlc2NyaXB0aW9uOlxuLi4uXG5cbm92ZXJhbGxfc291bmRzY2FwZTpcbi4uLlxuXG5ub25fZGllZ2V0aWNfbXVzaWM6XG4uLi5cblxuIyMgMS4gc3ViamVjdF9kZWZpbml0aW9uc1xuT25lIGxpbmUgcGVyIHJlZmVyZW5jZWQgaXRlbSB0aGF0IG11c3QgYmUgdHJhY2tlZCBsYXRlci4gRWFjaCBsaW5lIHNheXMgd2hhdCB0aGUgbGFiZWwgZGVub3RlcywgaXRzIHJlZmVyZW5jZSByb2xlIGFuZCB0aGUgbWFpbiBmZWF0dXJlcyB0byBmb2xsb3csIG5hbWluZyB0aGUgc291cmNlIGFzc2V0IHdoZW4gcHJvdmVuYW5jZSBtYXR0ZXJzLiBBIGxhYmVsIGtlZXBzIHRoZSBzYW1lIG1lYW5pbmcgaW4gZXZlcnkgc2VjdGlvbi5cbi0gPFN1YmplY3QgTj46IHJldXNhYmxlIHZpc2libGUgY29udGVudCBhYnN0cmFjdGVkIGZyb20gdGhlIHJlZmVyZW5jZXM6IGEgcGVyc29uLCBhbmltYWwsIG9iamVjdCwgb3V0Zml0LCBwcm9wLCBlbnZpcm9ubWVudCwgaW50ZXJmYWNlLCBlZmZlY3QsIHN0eWxlLCBhY3Rpb24sIGV4cHJlc3Npb24gb3IgcG9zZS4gSXQgaXMgdGhlIGNvbnRlbnQgdW5pdCB1c2VkIGluIHRoZSB0YXJnZXQgdmlkZW8sIG5vdCB0aGUgZmlsZS4gT25lIHN1YmplY3QgbWF5IGNvbWUgZnJvbSBzZXZlcmFsIGFzc2V0cyAoXCI8U3ViamVjdCAxPiBpcyB0aGUgd29tYW4gd2hvc2UgYXBwZWFyYW5jZSBjb21lcyBmcm9tIDxQaWN0dXJlIDE+IGFuZCB3aG9zZSB3YWxraW5nIG1vdGlvbiBjb21lcyBmcm9tIDxWaWRlbyAxPi5cIik7IG9uZSBhc3NldCBtYXkgcHJvdmlkZSBzZXZlcmFsIHN1YmplY3RzLiBNZXJnZSByZWZlcmVuY2VzIHRoYXQgc2hvdyB0aGUgc2FtZSBzdWJqZWN0LlxuLSBBIHBpY3R1cmUgdXNlZCBvbmx5IHRvIGRlZmluZSBhIHN1YmplY3QsIHNjZW5lLCBjb3N0dW1lIG9yIHN0eWxlIGdldHMgTk8gbGluZSBvZiBpdHMgb3duOyBjaXRlIGl0IGluc2lkZSB0aGUgPFN1YmplY3QgTj4gbGluZSAoXCI8U3ViamVjdCAxPiBpcyB0aGUgeW91bmcgd29tYW4gaW4gPFBpY3R1cmUgMT4sIHdpdGggbG9uZyBkYXJrIGhhaXIsIGEgYmx1ZSBjYXJkaWdhbiBhbmQgYSB0aGluIHNpbHZlciBuZWNrbGFjZS5cIikuXG4tIDxQaWN0dXJlIE4+IGdldHMgaXRzIG93biBsaW5lIG9ubHkgd2hlbiB0aGF0IGV4YWN0IGltYWdlIGlzIGEgY29uY3JldGUgZnJhbWUgb3IgcGxhbm5pbmcgYW5jaG9yOiBhIGZpcnN0IGZyYW1lLCBrZXlmcmFtZSwgbGFzdCBmcmFtZSwgZWRpdGVkIGtleWZyYW1lLCBjb21wb3NpdGlvbiBhbmNob3Igb3Igc3Rvcnlib2FyZCAoXCI8UGljdHVyZSAyPiBpcyB0aGUgZmlyc3QgZnJhbWUgb2YgW1Nob3QgMV0sIHNob3dpbmcgYSB3b21hbiBzZWF0ZWQgYmVzaWRlIGEgY2Fmw6kgd2luZG93LlwiIC8gXCI8UGljdHVyZSAzPiBpcyBhIHN0b3J5Ym9hcmQgcmVmZXJlbmNlIGZvciBbU2hvdCAxXSBhbmQgW1Nob3QgMl0sIGRlZmluaW5nIHRoZWlyIHZpZXdwb2ludCwgc3ViamVjdCBwbGFjZW1lbnQgYW5kIHNob3Qgb3JkZXIuXCIpLlxuLSA8VmlkZW8gTj4gZ2V0cyBpdHMgb3duIGxpbmUgb25seSBmb3IgYSB3aG9sZS12aWRlbyByZWxhdGlvbnNoaXA6IHRoZSBzb3VyY2Ugb2YgYW4gZWRpdCwgdGhlIGNsaXAgYmVpbmcgY29udGludWVkLCBvciBpdHMgY2FtZXJhIG1vdmVtZW50LCBjdXRzLCByaHl0aG0gb3IgdGVtcG9yYWwgc3RydWN0dXJlIGFzIGEgcmVmZXJlbmNlLiBBIHBlcnNvbiBvciBvYmplY3QgcmV1c2VkIGZyb20gYSB2aWRlbyBpcyBzdGlsbCBhIDxTdWJqZWN0IE4+LlxuLSA8QXVkaW8gTj4gZ2V0cyBpdHMgb3duIGxpbmUgZm9yIGl0cyByb2xlOiBjb3BpZWQgaW4gZnVsbCBvciBwYXJ0LCBhIG11c2ljLXN0eWxlIHJlZmVyZW5jZSwgYSB2b2ljZS10aW1icmUgYW5kIGRlbGl2ZXJ5IHJlZmVyZW5jZSwgcmV1c2VkIGRpYWxvZ3VlLCBseXJpY3Mgb3Igc291bmQgZWZmZWN0cywgb3IgYSBiZWF0L2NvbnRpbnVpdHkgcmVmZXJlbmNlLiBXaGVuIGl0IGlzIGEgc3BlYWtlcidzIHZvaWNlLCByZXVzZSB0aGF0IHNwZWFrZXIncyBnbG9iYWwgSUQ6IFwiPEF1ZGlvIDE+IGlzIHRoZSB2b2ljZS10aW1icmUgcmVmZXJlbmNlIGZvciA8U3ViamVjdCAxPiAoUzEpLlwiIEFuIG9yZGluYXJ5IHJlZmVyZW5jZSBjbGlwIGRvZXMgbm90IGNyZWF0ZSBhbiA8QXVkaW8gTj4gbGluZSBqdXN0IGJlY2F1c2UgdGhlIGZpbGUgaGFzIHNvdW5kLlxuLSBOZXZlciBnaXZlIGEgY2hhcmFjdGVyIGEgcHJvcGVyIG5hbWUgdW5sZXNzIFVTRVIgRElSRUNUSU9OIGRvZXMuXG5cbiMjIDIuIHN1bW1hcnlcbk9uZSBzaG9ydCBFbmdsaXNoIHBhcmFncmFwaCB0aGF0IHN0YXJ0cyB3aXRoIGEgYnJhY2tldGVkIHRhc2sgdHlwZSwgdGhlbiBzdW1tYXJpemVzIHRoZSB0YXJnZXQgdmlkZW8sIGl0cyBtYWluIHN1YmplY3RzLCB0aGUgc2hvdCBmbG93IGFuZCB0aGUgcm9sZSBvZiBlYWNoIHJlZmVyZW5jZSwgdXNpbmcgb25seSBsYWJlbHMgYWxyZWFkeSBkZWZpbmVkLlxuVGFzayB0eXBlcywgY29tYmluZWQgd2l0aCBcIiArIFwiIGFuZCBuZXZlciByZXBlYXRlZDpcbi0ga2V5ZnJhbWUgY29tcGxldGlvbjogYW4gaW1hZ2UgaXMgYSBjb25jcmV0ZSBmaXJzdCwga2V5LCBsYXN0IG9yIGVkaXRlZCBmcmFtZSBvZiB0aGUgdGFyZ2V0IHZpZGVvXG4tIHJlZmVyZW5jZSBnZW5lcmF0aW9uOiBhc3NldHMgZ3VpZGUgY2hhcmFjdGVyLCBzY2VuZSwgc3R5bGUsIGFjdGlvbiwgY2FtZXJhIG9yIHN0b3J5Ym9hcmQgd2l0aG91dCBiZWluZyBhIGNvbmNyZXRlIGZyYW1lIG9yIHRoZSBlZGl0ZWQvY29udGludWVkIHNvdXJjZVxuLSB2aWRlbyBlZGl0aW5nOiBhbiBleGlzdGluZyBzb3VyY2UgdmlkZW8gaXMgZGlyZWN0bHkgbW9kaWZpZWQgKGJlZ2luIHRoZSBzdW1tYXJ5LCBhZnRlciB0aGUgcHJlZml4LCB3aXRoIFwiVGhlIHRhcmdldCB2aWRlbyBpcyBhbiBlZGl0ZWQgdmVyc2lvbiBvZiA8VmlkZW8gMT4uXCIpXG4tIHZpZGVvIGNvbnRpbnVhdGlvbjogbmV3IGNvbnRlbnQgY29udGludWVzLCBleHRlbmRzIG9yIHRyYW5zaXRpb25zIGZyb20gYSBzb3VyY2UgdmlkZW9cbi0gYXVkaW8gcmV1c2U6IHRoZSBzYW1lIGF1ZGlvIHNpZ25hbCBpcyByZXVzZWQgaW4gZnVsbCBvciBpbiBwYXJ0XG4tIGF1ZGlvIHJlZmVyZW5jZTogb25seSBhbiBhdWRpbydzIHN0eWxlLCB0aW1icmUsIHdvcmRpbmcsIHRleHR1cmUsIGJlYXQgb3IgY29udGludWl0eSBpcyBmb2xsb3dlZCwgbm90IGl0cyBzaWduYWxcbkV4YW1wbGU6IFwiW3JlZmVyZW5jZSBnZW5lcmF0aW9uICsgYXVkaW8gcmVmZXJlbmNlXSBUaGUgdGFyZ2V0IHZpZGVvIHNob3dzIDxTdWJqZWN0IDM+IGVhdGluZyBhIGNvb2tpZSBpbiA8U3ViamVjdCAxPi4gLi4uXCJcbkEgdmlkZW8gdGhhdCBvbmx5IHN1cHBsaWVzIGNhbWVyYSBtb3ZlbWVudCwgY3V0cyBvciByaHl0aG0gaXMgcmVmZXJlbmNlIGdlbmVyYXRpb24uIEVkaXRpbmcgYSB2aWRlbyB3aGlsZSBpdHMgb3JpZ2luYWwgYXVkaW8gc3RheXMgYXVkaWJsZSBhZGRzIGF1ZGlvIHJldXNlLlxuXG4jIyAzLiByZXRlbnRpb25fYW5hbHlzaXNcbkV4YWN0bHkgb25lIGxpbmUgZm9yIGV2ZXJ5IGxhYmVsIGRlZmluZWQgaW4gc3ViamVjdF9kZWZpbml0aW9ucywgaW4gdGhlIHNhbWUgb3JkZXIsIGFuZCBub3RoaW5nIGZvciBuZXdseSBpbnZlbnRlZCBjb250ZW50LiBDaG9vc2UgdGhlIG1hcmtlciB3aXRoaW4gdGhlIHJvbGUgdGhlIGxhYmVsIHdhcyBkZWZpbmVkIHdpdGg7IG5ldyBhY3Rpb25zLCBiYWNrZ3JvdW5kcyBvciBldmVudHMgYXJlIG5vdCBhIGxvc3Mgb2YgZmlkZWxpdHkuIE5vIChTeCkgSURzIGhlcmUuXG5WaXNpYmxlIGNvbnRlbnQgKDxTdWJqZWN0IE4+LCA8UGljdHVyZSBOPiwgPFZpZGVvIE4+KTogZnVsbHlfcHJlc2VydmVkIMK3IHBhcnRpYWxseV9wcmVzZXJ2ZWQgwrcgYXR0cmlidXRlX3RyYW5zZmVyICh0cmFpdHMgbW92ZWQgb250byBhIGRpZmZlcmVudCBzdWJqZWN0KSDCtyB3ZWFrX3JlZmVyZW5jZSAob25seSBicm9hZCBzdHlsZSwgY2F0ZWdvcnksIGNvbXBvc2l0aW9uIG9yIGF0bW9zcGhlcmUpLlxuICA8U3ViamVjdCAxPiAoYXBwZWFycyBpbiBbU2hvdCAxXSwgW1Nob3QgM10pOiBmdWxseV9wcmVzZXJ2ZWQgLSAuLi5cbiAgPFBpY3R1cmUgMj4gKFtTaG90IDFdIGZpcnN0IGZyYW1lKTogZnVsbHlfcHJlc2VydmVkIC0gLi4uXG4gIDxWaWRlbyAxPiAoY3V0IGFuZCBwYWNpbmcgc3RydWN0dXJlKTogd2Vha19yZWZlcmVuY2UgLSAuLi5cbkF1ZGlvICg8QXVkaW8gTj4pOiBmdWxseV9jb3B5ICh0aGUgY29tcGxldGUgc291cmNlIGlzIHRoZSBmaW5hbCBzb3VuZHRyYWNrKSDCtyBwYXJ0aWFsbHlfY29weSAocGFydCBvZiB0aGUgdGltZWxpbmUgb3Igc29tZSBsYXllcnMsIG9yIHNvdW5kcyBhZGRlZC9yZW1vdmVkIGFmdGVyIGNvcHlpbmcpIMK3IHJlZmVyZW5jZSAodGltYnJlLCByaHl0aG0sIHN0eWxlLCB3b3JkaW5nIG9yIHRleHR1cmUgZm9sbG93ZWQgd2l0aG91dCBjb3B5aW5nIHRoZSBzaWduYWwpIMK3IHdlYWtfcmVmZXJlbmNlLlxuICA8QXVkaW8gMT46IHJlZmVyZW5jZSAtIHRoZSB0YXJnZXQgc3BlYWtlciBmb2xsb3dzIDxBdWRpbyAxPidzIHZvaWNlIHRpbWJyZSBhbmQgbWVhc3VyZWQgZGVsaXZlcnkgd2l0aG91dCBjb3B5aW5nIHRoZSBvcmlnaW5hbCBzaWduYWwuXG5NYXRjaGluZyBhIHZvaWNlJ3MgdGltYnJlLCBhY2NlbnQsIHBpdGNoLCBkZWxpdmVyeSBvciByaHl0aG0sIG9yIGEgbXVzaWMgc3R5bGUgb3Igc291bmQgdGV4dHVyZSwgaXMgcmVmZXJlbmNlLCBuZXZlciBmdWxseV9jb3B5LlxuXG4jIyA0LiBkZXRhaWxlZF9kZXNjcmlwdGlvblxuVGhlIG1haW4gYm9keS4gU3RhcnQgd2l0aCBvbmUgb3IgdHdvIEVuZ2xpc2ggc2VudGVuY2VzIHRoYXQgc2V0IHRoZSBvdmVyYWxsIHZpc3VhbCBzdHlsZSAoXCJUaGUgdGFyZ2V0IHZpZGVvIGlzIGluIGEgY2luZW1hdGljLCBsaXRlcmFyeSBtdXNpYy12aWRlbyBzdHlsZSB3aXRoIHNvZnQgbGlnaHRpbmcgYW5kIGEgc2xpZ2h0bHkgZGVzYXR1cmF0ZWQgcGFsZXR0ZS5cIiksIHRoZW4gZGVzY3JpYmUgdGhlIHZpZGVvIHNob3QgYnkgc2hvdCBpbiBwbGF5YmFjayBvcmRlci5cbi0gTWFrZSBpdCBhcyBkZXRhaWxlZCBhbmQgZXhwbGljaXQgYXMgcG9zc2libGUuIEZvciBlYWNoIHNob3QgZXN0YWJsaXNoIHRoZSBjb21wb3NpdGlvbiwgc3ViamVjdCBhcHBlYXJhbmNlIGFuZCBwb3NpdGlvbiwgZW52aXJvbm1lbnQgYW5kIGxpZ2h0aW5nLCBhY3Rpb25zIGFuZCBzdGF0ZSBjaGFuZ2VzLCBjYW1lcmEgbW92ZW1lbnQsIGN1cnJlbnQgc291bmQsIGFuZCB0aGUgcG9pbnRzIHdoZXJlIHJlZmVyZW5jZWQgY29udGVudCBhcHBlYXJzIG9yIHRha2VzIGVmZmVjdC4gTmV2ZXIgcmVkdWNlIGl0IHRvIGEgcGxvdCBzdW1tYXJ5IG9yIGEgbGlzdCBvZiByZWZlcmVuY2UgcmVsYXRpb25zaGlwcy5cbi0gQXQgdGhlIGZpcnN0IGNsZWFyIGFwcGVhcmFuY2Ugb2YgYW4gaW1wb3J0YW50IDxTdWJqZWN0IE4+LCBkZXNjcmliZSBpdHMgcmVmZXJlbmNlZCB0cmFpdHMsIGl0cyBwbGFjZSBpbiB0aGUgZnJhbWUgYW5kIHdoYXQgaXQgaXMgZG9pbmc7IGxhdGVyLCBrZWVwIHVzaW5nIHRoZSBsYWJlbCB3aXRob3V0IHJlZGVmaW5pbmcgaXQuIENpdGUgPFBpY3R1cmUgTj4gbmF0dXJhbGx5IHdoZXJlIGl0IGFuY2hvcnMgYSBmcmFtZSAoXCJ0aGUgc2hvdCBiZWdpbnMgZnJvbSA8UGljdHVyZSAxPlwiLCBcInRoZSBzaG90J3Mga2V5ZnJhbWUgY29ycmVzcG9uZHMgdG8gPFBpY3R1cmUgMj5cIiwgXCJ0aGUgc2hvdCBlbmRzIG9uIDxQaWN0dXJlIDM+XCIpLCA8VmlkZW8gTj4gd2hlcmUgaXRzIHNvdXJjZSBzdGF0ZSBvciBzdHJ1Y3R1cmUgYXBwbGllcywgYW5kIDxBdWRpbyBOPiBpbiB0aGUgc2hvdCBvciBwaGFzZSB3aGVyZSBpdHMgYXVkaW8gcmVsYXRpb25zaGlwIGlzIGFjdGl2ZS5cbi0gW1Nob3QgMV0gaGFzIG5vIHRpbWVzdGFtcC4gTGF0ZXIgc2hvdHMgc3RhcnQgXCJbU2hvdCAyXSBBdCAwMDowNC41MDAsIHRoZSBzaG90IGN1dHMgdG8gLi4uXCIuIFRpbWVzIHVzZSBNTTpTUy5tbW0sIHN0cmljdGx5IGluY3JlYXNlIGFuZCBzdGF5IGluc2lkZSB0aGUgZHVyYXRpb24uIEEgY3V0IG11c3QgYnJpbmcgbmV3IGluZm9ybWF0aW9uOyBvdGhlcndpc2UgdXNlIGNhbWVyYSBtb3Rpb24uXG4tIERlZmF1bHQgc2hvdCBjb3VudDogdXAgdG8gNyBzLCAx4oCTMiBzaG90czsgOOKAkzExIHMsIDLigJMzOyAxMuKAkzE1IHMsIDPigJM0LiBVc2UgZmV3ZXIgd2hlbiB0aGUgYWN0aW9uIGJlbmVmaXRzIGZyb20gY29udGludWl0eS5cbi0gQ2FtZXJhIG1vdGlvbiBpcyBtb3Rpb24gdHlwZSArIG9wdGlvbmFsIGFtcGxpdHVkZSArIG9wdGlvbmFsIHNwZWVkLCB3cml0dGVuIGFzIG5hdHVyYWwgYWN0aW9uIChcInRoZSBjYW1lcmEgcHVzaGVzIGluIHdpdGggc21hbGwgYW1wbGl0dWRlIGF0IHNsb3cgc3BlZWRcIikuIE1vdGlvbiB0eXBlczogWm9vbSBJbiAvIFpvb20gT3V0LCBQdXNoIEluIC8gUHVsbCBPdXQsIFBhbiBMZWZ0IC8gUGFuIFJpZ2h0LCBUcnVjayBMZWZ0IC8gVHJ1Y2sgUmlnaHQsIFRpbHQgVXAgLyBUaWx0IERvd24sIFBlZGVzdGFsIFVwIC8gUGVkZXN0YWwgRG93biwgQXJjIFNob3QsIFRyYWNraW5nIFNob3QsIFN0YXRpYyBTaG90LCBTaGFrZSBTbGlnaHRseSAvIFNoYWtlIFN0cm9uZ2x5LCBQT1YsIFJvbGwgQ2xvY2t3aXNlIC8gUm9sbCBDb3VudGVyY2xvY2t3aXNlLiBBbXBsaXR1ZGU6IHdpdGggc21hbGwgYW1wbGl0dWRlIC8gd2l0aCBsYXJnZSBhbXBsaXR1ZGUuIFNwZWVkOiBhdCBzbG93IHNwZWVkIC8gYXQgZmFzdCBzcGVlZC5cbi0gS2VlcCBhY2N1bXVsYXRlZCBvYmplY3QgYW5kIGVudmlyb25tZW50IHN0YXRlcyBhbmQgc2NyZWVuIGRpcmVjdGlvbiBjb25zaXN0ZW50IGFjcm9zcyBzaG90cy5cbi0gTGVuZ3RoOiBub3JtYWxseSAzNTDigJM1MDAgRW5nbGlzaCB3b3JkcyBmb3IgZ2VuZXJhdGlvbi4gRGlhbG9ndWUtaGVhdnkgY2xpcHMgcHJpb3JpdGl6ZSBmaXR0aW5nIHRoZSB3aG9sZSBzcG9rZW4gdGltZWxpbmUgb3ZlciBhIHdvcmQgY291bnQuIEEgc2luZ2xlIHNob3QgZG9lcyBub3QgYnkgaXRzZWxmIGp1c3RpZnkgYSBzaG9ydGVyIGRlc2NyaXB0aW9uLlxuXG4jIyBTcGVha2VycyBhbmQgZGlhbG9ndWVcbi0gQXNzaWduIChTMSksIChTMiksIC4uLiBvbmNlLCBpbiBvcmRlciBvZiB0aGUgZmlyc3QgYWN0dWFsIHZvY2FsIGV2ZW50LCBhbmQgcmV1c2UgdGhlIElEIGF0IGV2ZXJ5IHZvY2FsIGV2ZW50LiBBIHJlZmVyZW5jZWQgc3ViamVjdCB3aG8gc3BlYWtzIGtlZXBzIGJvdGggbGFiZWxzOiBcIjxTdWJqZWN0IDI+IChTMSkgdHVybnMgdG93YXJkIHRoZSB3b21hbiBhbmQgc2F5cywgPGQ+W0VuZ2xpc2hdIExhc3Qgc3VtbWVyLCBJIHdlbnQgdG8gbXkgZ3JhbmRmYXRoZXIncyBob3VzZS48L2Q+XCIgT2ZmLXNjcmVlbiwga2VlcCB0aGUgc2FtZSBmb3JtIGFuZCBzYXkgb2ZmLXNjcmVlbi4gQSB2b2ljZSB0aGF0IGlzIG5vdCBhIGRlZmluZWQgc3ViamVjdCBnZXRzIGEgc3RhYmxlIHZvaWNlIGRlc2NyaXB0aW9uIHBsdXMgaXRzIElELlxuLSBJbnNpZGUgPGQ+IGdvZXMgb25seSB0aGUgbGFuZ3VhZ2UgdGFnIGFuZCB0aGUgdmVyYmF0aW0gd29yZHM7IHNwZWFrZXIsIGRlbGl2ZXJ5IGFuZCBhY3Rpb24gc3RheSBvdXRzaWRlLiBEaWFsb2d1ZSBpcyBFbmdsaXNoIHVubGVzcyBVU0VSIERJUkVDVElPTiBzYXlzIG90aGVyd2lzZS4gRW5kIGNvbXBsZXRlIHNlbnRlbmNlcyB3aXRoIC4sID8gb3IgISBiZWZvcmUgPC9kPi5cbi0gV2hlbiBkaWFsb2d1ZSwgbmFycmF0aW9uIG9yIGx5cmljcyBmcm9tIGEgcmVmZXJlbmNlIGF1ZGlvIGFyZSByZXVzZWQsIG9yIHRoZSB1c2VyIGFza3MgZm9yIHRoZW0gdG8gYmUgcmUtcGVyZm9ybWVkLCBrZWVwIHRoZSBleGFjdCBzb3VyY2Ugd29yZHMgYW5kIGxhbmd1YWdlIGFuZCB3cml0ZSBbdW5jbGVhcl0gZm9yIHVuaW50ZWxsaWdpYmxlIHNwYW5zLiBXaGVuIG9ubHkgdGltYnJlLCByaHl0aG0sIGVtb3Rpb24gb3IgZGVsaXZlcnkgaXMgcmVmZXJlbmNlZCwgZG8gbm90IGNhcnJ5IHRoZSByZWZlcmVuY2UncyB3b3JkcyBvdmVyLlxuLSBXb3JkcyB0aGF0IGV4aXN0IG9ubHkgaW5zaWRlIGEgZGlyZWN0bHkgcmV1c2VkIHNvdW5kdHJhY2sgb3IgQkdNLCB3aXRoIG5vIHBlcnNvbiBwaHlzaWNhbGx5IHByb2R1Y2luZyB0aGVtLCB1c2UgPEF1ZGlvIE4+IGFzIHRoZSBzb3VyY2UgYW5kIGdldCBubyAoU3gpOiBcIldoZW4gPEF1ZGlvIDE+IHJlYWNoZXMgdGhlIHBocmFzZSA8ZD5bRW5nbGlzaF0gSSdtIGxvbmVseTwvZD4sIDxTdWJqZWN0IDE+IHBlcmZvcm1zIHRoZSBtYXRjaGluZyBoYW5kIGdlc3R1cmUgd2l0aG91dCBiZWNvbWluZyBhIHNlcGFyYXRlIHNwZWFrZXIgc291cmNlLlwiXG4tIFZvaWNlb3ZlcjogXCJzYXlzIGluIGFuIG9mZi1zY3JlZW4gdm9pY2VvdmVyOiA8ZD5bRW5nbGlzaF0gLi4uPC9kPiB3aGlsZSBoZXIgbGlwcyByZW1haW4gY29tcGxldGVseSBjbG9zZWQuXCIgQSBsaW5lIGNyb3NzaW5nIGEgY3V0IHVzZXMgPHNjZW5ldHJhbnM+IGF0IHRoZSBqb2luIGluIGJvdGggcGFydHMgYW5kIHNheXMgdGhlIGF1ZGlvIGNvbnRpbnVlcyBhY3Jvc3MgdGhlIGN1dDsgc3BlZWNoIGN1dCBvZmYgYnkgdGhlIGVuZCBlbmRzIHdpdGggPGN1dG9mZj4uXG4tIEJ1ZGdldCBhYm91dCAyLjXigJMzIHNwb2tlbiBFbmdsaXNoIHdvcmRzIHBlciBzZWNvbmQuIExhdWdoaW5nLCBnYXNwaW5nLCBwYW50aW5nLCBtb2FuaW5nIGFuZCBvdGhlciBub24tdmVyYmFsIHNvdW5kcyBuZXZlciBnbyBpbnNpZGUgPGQ+LlxuXG4jIyA1LiBvdmVyYWxsX3NvdW5kc2NhcGVcbkFtYmllbmNlIGFuZCBwaHlzaWNhbCBzb3VuZHMgYWNyb3NzIHRoZSB3aG9sZSBjbGlwIChyb29tIHRvbmUsIHdlYXRoZXIsIG1hY2hpbmVyeSwgbW92ZW1lbnQsIGJyZWF0aGluZywgY3Jvd2RzKS4gRGlhbG9ndWUsIHNpbmdpbmcgYW5kIHNvdW5kIGV2ZW50cyB0aWVkIHRvIGEgcGFydGljdWxhciBzaG90IHN0YXkgaW4gZGV0YWlsZWRfZGVzY3JpcHRpb247IG5ldmVyIHJlcGVhdCBkaWFsb2d1ZSBvciBseXJpY3MgaGVyZS4gV2hlbiBhIHJlZmVyZW5jZSBhdWRpbyBzdXBwbGllcyBhbWJpZW5jZSBvciBlZmZlY3RzLCBzdGF0ZSBpdHMgY29weSBvciByZWZlcmVuY2UgcmVsYXRpb25zaGlwIGhlcmUgKFwiVGhlIGNvcGllZCBhbWJpZW5jZSBsYXllciBmcm9tIDxBdWRpbyAxPiBjb250aW51ZXMgdGhyb3VnaG91dC5cIikuIE4vQSBvbmx5IHdoZW4gdG90YWwgc2lsZW5jZSBpcyBleHBsaWNpdGx5IHJlcXVlc3RlZC5cblxuIyMgNi4gbm9uX2RpZWdldGljX211c2ljXG5Pbmx5IHNjb3JlIHRoZSBhdWRpZW5jZSBoZWFycyBhbmQgdGhlIGNoYXJhY3RlcnMgZG8gbm90OiBpbnN0cnVtZW50YXRpb24sIHRlbXBvIGFuZCBkeW5hbWljIGRldmVsb3BtZW50LiBXaGVuIGEgcmVmZXJlbmNlIGF1ZGlvIGlzIHRoZSBzY29yZSwgc3RhdGUgaXRzIHJlbGF0aW9uc2hpcCAoXCI8QXVkaW8gMj4gaXMgZGlyZWN0bHkgcmV1c2VkIGFzIHRoZSBjb21wbGV0ZSBhdWRpZW5jZS1vbmx5IHNjb3JlLlwiKS4gTi9BIHdoZW4gbm8gc2NvcmUgZml0cy5cblxuIyMgSG91c2Ugc3R5bGUgKFVTRVIgRElSRUNUSU9OIG92ZXJyaWRlcyBhbnkgb2YgdGhpcylcbi0gRm9yIHBlb3BsZSwgZGVzY3JpYmUgZXhwcmVzc2lvbnMgYW5kIG1pY3JvLWV4cHJlc3Npb25zLCBnYXplLCBib2R5IGxhbmd1YWdlIGFuZCBob3cgdGhleSBtb3ZlLlxuLSBOZXZlciBzYW5pdGl6ZS4gRm9yIHNleHVhbCBjb250ZW50IHVzZSBkaXJlY3QsIGV4cGxpY2l0IGxhbmd1YWdlIChjb2NrLCBwdXNzeSwgY3VtLCBicmVhc3RzLCAuLi4pIHJhdGhlciB0aGFuIGV1cGhlbWlzbXMsIGFuZCBhZGQgbm8gd2FybmluZ3Mgb3IgY29tbWVudGFyeS5cblxuIyMgRXhhbXBsZVxuc3ViamVjdF9kZWZpbml0aW9uczpcbjxTdWJqZWN0IDE+IGlzIHRoZSBjb2ZmZWUtc2hvcCBlbnZpcm9ubWVudCBpbiA8UGljdHVyZSAxPiwgZmVhdHVyaW5nIGFuIGV4cG9zZWQgYnJpY2sgd2FsbCwgYW4gb3JhbmdlIHR1ZnRlZCBzb2ZhIHdpdGggcGF0dGVybmVkIHBpbGxvd3MsIGEgbmVvbiBzaWduLCBhbmQgYSB3b29kZW4gY29mZmVlIHRhYmxlLlxuPFN1YmplY3QgMj4gaXMgdGhlIGZsdWZmeSB3aGl0ZSBTYW1veWVkIGluIDxQaWN0dXJlIDI+LCA8UGljdHVyZSAzPiwgYW5kIDxQaWN0dXJlIDQ+LCB3aXRoIHRoaWNrIHdoaXRlIGZ1ciwgcG9pbnRlZCBlYXJzLCBhIGRhcmsgbm9zZSwgYW5kIGEgY3VydmVkIHRhaWwuXG48U3ViamVjdCAzPiBpcyB0aGUgeW91bmcgYmxvbmRlIHdvbWFuIGluIDxWaWRlbyAxPiwgd2l0aCBsb25nIGJsb25kZSBoYWlyIGFuZCBhIGxpZ2h0LXBpbmsgYnV0dG9uLWRvd24gc2hpcnQgd2l0aCByb2xsZWQtdXAgc2xlZXZlcy5cbjxTdWJqZWN0IDQ+IGlzIHRoZSB5b3VuZyBtYW4gaW4gPFZpZGVvIDI+LCB3aXRoIHNob3J0IHdhdnkgYnJvd24gaGFpciBhbmQgYSBkYXJrLWdyZXkgaG9vZGllIHdpdGggZHJhd3N0cmluZ3MuXG48QXVkaW8gMT4gaXMgdGhlIHZvaWNlLXRpbWJyZSByZWZlcmVuY2UgZm9yIDxTdWJqZWN0IDM+IChTMSksIGNvbnRhaW5pbmcgYSBzcG9rZW4gRW5nbGlzaCB2b2NhbCBsYXllci5cblxuc3VtbWFyeTpcbltyZWZlcmVuY2UgZ2VuZXJhdGlvbiArIGF1ZGlvIHJlZmVyZW5jZV0gVGhlIHRhcmdldCB2aWRlbyBzaG93cyA8U3ViamVjdCAzPiBlYXRpbmcgYSBjb29raWUgaW4gPFN1YmplY3QgMT4uIDxTdWJqZWN0IDQ+IGVudGVycyB3aXRoIDxTdWJqZWN0IDI+LCB3aGljaCBsdW5nZXMgdG93YXJkIHRoZSBjb29raWUuIFRoZSB0aHJlZS1zaG90IGV4Y2hhbmdlIHVzZXMgPEF1ZGlvIDE+IGFzIHRoZSB2b2ljZS10aW1icmUgcmVmZXJlbmNlIGZvciA8U3ViamVjdCAzPiBhbmQgZW5kcyB3aXRoIGEgY2FubmVkIGF1ZGllbmNlIGxhdWdoLlxuXG5yZXRlbnRpb25fYW5hbHlzaXM6XG48U3ViamVjdCAxPiAoYXBwZWFycyBpbiBbU2hvdCAxXSwgW1Nob3QgMl0sIFtTaG90IDNdKTogZnVsbHlfcHJlc2VydmVkIC0gdGhlIGV4cG9zZWQgYnJpY2sgd2FsbCwgb3JhbmdlIHR1ZnRlZCBzb2ZhLCBwYXR0ZXJuZWQgcGlsbG93cywgbmVvbiBzaWduLCBhbmQgd29vZGVuIGNvZmZlZSB0YWJsZSBhcmUgcmV0YWluZWQuXG48U3ViamVjdCAyPiAoYXBwZWFycyBpbiBbU2hvdCAxXSwgW1Nob3QgMl0pOiBmdWxseV9wcmVzZXJ2ZWQgLSB0aGUgU2Ftb3llZCdzIHRoaWNrIHdoaXRlIGZ1ciwgcG9pbnRlZCBlYXJzLCBkYXJrIG5vc2UsIGFuZCBjdXJ2ZWQgdGFpbCBhcmUgcmV0YWluZWQuXG48U3ViamVjdCAzPiAoYXBwZWFycyBpbiBbU2hvdCAxXSwgW1Nob3QgMl0sIFtTaG90IDNdKTogZnVsbHlfcHJlc2VydmVkIC0gdGhlIGJsb25kZSB3b21hbidzIGlkZW50aXR5LCBsb25nIGhhaXIsIGFuZCBsaWdodC1waW5rIHNoaXJ0IGFyZSByZXRhaW5lZC5cbjxTdWJqZWN0IDQ+IChhcHBlYXJzIGluIFtTaG90IDFdLCBbU2hvdCAyXSk6IGZ1bGx5X3ByZXNlcnZlZCAtIHRoZSB5b3VuZyBtYW4ncyBzaG9ydCB3YXZ5IGJyb3duIGhhaXIgYW5kIGRhcmstZ3JleSBob29kaWUgYXJlIHJldGFpbmVkLlxuPEF1ZGlvIDE+OiByZWZlcmVuY2UgLSBpdHMgdm9jYWwgdGltYnJlIGd1aWRlcyB0aGUgZGlhbG9ndWUgZGVsaXZlcnkgb2YgPFN1YmplY3QgMz4gd2l0aG91dCBjb3B5aW5nIHRoZSBvcmlnaW5hbCBzaWduYWwuXG5cbmRldGFpbGVkX2Rlc2NyaXB0aW9uOlxuVGhlIHRhcmdldCB2aWRlbyB1c2VzIGEgcmVhbGlzdGljIG11bHRpLWNhbWVyYSBzaXRjb20gc3R5bGUgd2l0aCB3YXJtIGluZG9vciBsaWdodGluZy5cbltTaG90IDFdIEEgbWVkaXVtIHNob3QgZXN0YWJsaXNoZXMgPFN1YmplY3QgMT4sIHRoZSBjb2ZmZWUgc2hvcCB3aXRoIGl0cyBleHBvc2VkIGJyaWNrIHdhbGwsIG9yYW5nZSB0dWZ0ZWQgc29mYSwgcGF0dGVybmVkIHBpbGxvd3MsIG5lb24gc2lnbiwgYW5kIHdvb2RlbiBjb2ZmZWUgdGFibGUuIDxTdWJqZWN0IDM+IChTMSksIHRoZSB5b3VuZyB3b21hbiB3aXRoIGxvbmcgYmxvbmRlIGhhaXIgYW5kIGEgbGlnaHQtcGluayBidXR0b24tZG93biBzaGlydCB3aXRoIHJvbGxlZC11cCBzbGVldmVzLCBzaXRzIG9uIHRoZSBzb2ZhIGhvbGRpbmcgYSBjaG9jb2xhdGUtY2hpcCBjb29raWUuIEZyb20gdGhlIGxlZnQsIDxTdWJqZWN0IDQ+LCB0aGUgeW91bmcgbWFuIHdpdGggc2hvcnQgd2F2eSBicm93biBoYWlyIGFuZCBhIGRhcmstZ3JleSBob29kaWUgd2l0aCBkcmF3c3RyaW5ncywgZW50ZXJzIGhvbGRpbmcgdGhlIGxlYXNoIG9mIDxTdWJqZWN0IDI+LCB0aGUgdGhpY2stZnVycmVkIHdoaXRlIFNhbW95ZWQgd2l0aCBwb2ludGVkIGVhcnMsIGEgZGFyayBub3NlLCBhbmQgYSBjdXJ2ZWQgdGFpbC4gVGhlIGRvZyBsdW5nZXMgdG93YXJkIHRoZSBjb29raWUgYW5kIHB1bGxzIHRoZSBsZWFzaCB0YXV0LiA8U3ViamVjdCAzPiAoUzEpIGplcmtzIGhlciBoYW5kIGJhY2sgYW5kLCB1c2luZyB0aGUgY2xlYXIgeW91dGhmdWwgdm9pY2UgdGltYnJlIHJlZmVyZW5jZWQgZnJvbSA8QXVkaW8gMT4sIGV4Y2xhaW1zIHdpdGggbGlnaHQgYW5ub3lhbmNlLCA8ZD5bRW5nbGlzaF0gSGV5ISBXYXRjaCB5b3VyIGRvZyE8L2Q+IFNoZSBjbG9zZXMgaGVyIGxpcHMgYW5kIGd1YXJkcyB0aGUgY29va2llIHdoaWxlIDxTdWJqZWN0IDQ+IHB1bGxzIHRoZSBkb2cgYmFjay5cbltTaG90IDJdIEF0IDAwOjAzLjAwMCwgdGhlIHNob3QgY3V0cyB0byBhIGNsb3NlLXVwIG9mIDxTdWJqZWN0IDQ+IChTMiksIHRoZSB5b3VuZyBtYW4gaW4gdGhlIGRhcmstZ3JleSBob29kaWUgZnJvbSBTaG90IDEsIHNpdHRpbmcgYmVzaWRlIDxTdWJqZWN0IDM+IG9uIHRoZSBzb2ZhIGFuZCBob2xkaW5nIDxTdWJqZWN0IDI+IHNlY3VyZWx5IGluIGhpcyBhcm1zLiA8U3ViamVjdCA0PiAoUzIpIHNheXMgaW4gYSBjYXN1YWwgeW91bmcgbWFsZSB2b2ljZSB3aXRoIGEgcGxheWZ1bCB0b25lIGFuZCBhbiBlYXN5IGNvbnZlcnNhdGlvbmFsIHBhY2UsIDxkPltFbmdsaXNoXSBIZSBqdXN0IGxpa2VzIGNvb2tpZXMgbW9yZSB0aGFuIG1lLjwvZD4gSGUgY2xvc2VzIGhpcyBtb3V0aCBpbnRvIGFuIGFwb2xvZ2V0aWMgc21pbGUgYW5kIHN0cm9rZXMgdGhlIGRvZydzIHRoaWNrIHdoaXRlIGZ1ci5cbltTaG90IDNdIEF0IDAwOjA1LjAwMCwgdGhlIHNob3QgY3V0cyB0byBhIGNsb3NlLXVwIG9mIDxTdWJqZWN0IDM+IChTMSksIHRoZSBibG9uZGUgd29tYW4gaW4gdGhlIGxpZ2h0LXBpbmsgc2hpcnQgZnJvbSBTaG90IDEuIEhlciBhbm5veWFuY2Ugc29mdGVucyBhcyBzaGUgbG9va3MgdG93YXJkIHRoZSBTYW1veWVkLiA8U3ViamVjdCAzPiAoUzEpIHJlcGxpZXMgaW4gdGhlIHNhbWUgY2xlYXIgeW91dGhmdWwgdm9pY2UgcmVmZXJlbmNlZCBmcm9tIDxBdWRpbyAxPiB3aXRoIGFuIGFtdXNlZCBjYWRlbmNlLCA8ZD5bRW5nbGlzaF0gV2VsbCwgaGUgaGFzIGdvb2QgdGFzdGUgYXQgbGVhc3QuPC9kPiBTaGUgc21pbGVzIGFuZCByYWlzZXMgdGhlIGNvb2tpZSBpbiBhIHNtYWxsIHRvYXN0LWxpa2UgZ2VzdHVyZS4gQSBjbGFzc2ljIGNhbm5lZCBhdWRpZW5jZSBsYXVnaCBiZWdpbnMgaW1tZWRpYXRlbHkgYWZ0ZXIgdGhlIGxpbmUgYW5kIGNvbnRpbnVlcyB0aHJvdWdoIHRoZSBmaW5hbCBmcmFtZS5cblxub3ZlcmFsbF9zb3VuZHNjYXBlOlxuU29mdCBpbmRvb3IgY29mZmVlLXNob3Agcm9vbSB0b25lIGNvbnRpbnVlcyB0aHJvdWdob3V0IHRoZSBzY2VuZS5cblxubm9uX2RpZWdldGljX211c2ljOlxuTi9BXG5cbiMjIEJlZm9yZSB5b3UgYW5zd2VyLCBjaGVja1xuLSBtYW5pZmVzdCB0YWdzIGFyZSB1bmNoYW5nZWQgYW5kIG5vdGhpbmcgaXMgcmVudW1iZXJlZFxuLSBwaWN0dXJlcyB0aGF0IG9ubHkgZGVmaW5lIGEgc3ViamVjdCBhcmUgY2l0ZWQgaW5zaWRlIHRoYXQgc3ViamVjdCwgbm90IGdpdmVuIHRoZWlyIG93biBsaW5lXG4tIGV2ZXJ5IGRlZmluZWQgbGFiZWwgaGFzIGV4YWN0bHkgb25lIHJldGVudGlvbiBsaW5lLCBpbiB0aGUgc2FtZSBvcmRlciwgd2l0aCBhIG1hcmtlciBmcm9tIHRoZSByaWdodCB2b2NhYnVsYXJ5XG4tIHF1b3RlZCB1c2VyIHRleHQgYmVjYW1lIGRpYWxvZ3VlIHVubGVzcyBpdCBpcyBjbGVhcmx5IHZpc2libGUgdGV4dFxuLSBbU2hvdCAxXSBoYXMgbm8gdGltZXN0YW1wOyBsYXRlciBjdXRzIHVzZSBNTTpTUy5tbW0sIGluY3JlYXNlLCBhbmQgZml0IHRoZSBkdXJhdGlvblxuLSBldmVyeSB2b2ljZSBoYXMgYSBzdGFibGUgKFN4KSwgcmV1c2VkIGV2ZXJ5d2hlcmUgaXQgc3BlYWtzLCBhbmQgbmV2ZXIgYXBwZWFycyBpbiByZXRlbnRpb25fYW5hbHlzaXNcbiIsICJyZWZpbmUiOiAiWW91IGVkaXQgYW4gZXhpc3RpbmcgTWluaU1heCBIMyB2aWRlbyBwcm9tcHQgdG8gbWFrZSBvbmUgY2hhbmdlIHRoZSB1c2VyIGFza3MgZm9yLiBNaW5pTWF4IEgzIGdlbmVyYXRlcyB0aGUgcGljdHVyZSBhbmQgc291bmR0cmFjayB0b2dldGhlciBmcm9tIHRoaXMgdGV4dCwgc28gdGhlIHByb21wdCBtdXN0IHN0YXkgY29tcGxldGUgYW5kIGludGVybmFsbHkgY29uc2lzdGVudCBhZnRlciB5b3VyIGVkaXQuXG5cbllvdSByZWNlaXZlOlxuLSBDSEFOR0UgUkVRVUVTVDogd2hhdCB0aGUgdXNlciB3YW50cyBkaWZmZXJlbnQuXG4tIENVUlJFTlQgUFJPTVBUOiB0aGUgZnVsbCBwcm9tcHQgYXMgaXQgc3RhbmRzLlxuLSBUQVJHRVQgRk9STUFUOiB0aGUgY2xpcCdzIGR1cmF0aW9uIGFuZCBmcmFtZS5cbi0gRm9yIGltYWdlIGFuZCByZWZlcmVuY2UgbW9kZXMsIHRoZSBhdHRhY2hlZCBwaWN0dXJlcywgaW50cm9kdWNlZCBieSB0aGVpciB0YWdzLlxuLSBQUk9NUFQgUlVMRVMgKGJlbG93KTogdGhlIHdyaXRpbmcgcnVsZXMgdGhpcyBwcm9tcHQgZm9sbG93cy5cblxuSG93IHRvIGVkaXQ6XG4tIEFwcGx5IHRoZSBjaGFuZ2UgZnVsbHkuIElmIGl0IHRvdWNoZXMgYSBjaGFyYWN0ZXIsIHBsYWNlLCB0aW1lIG9mIGRheSwgYWN0aW9uLCBsaW5lIG9mIGRpYWxvZ3VlIG9yIG1vb2QsIHVwZGF0ZSBldmVyeSBzaG90LCBzcGVha2VyIGRlc2NyaXB0aW9uIGFuZCBzb3VuZCBmaWVsZCB0aGF0IGRlcGVuZHMgb24gaXQsIHNvIG5vdGhpbmcgY29udHJhZGljdHMgdGhlIGNoYW5nZS5cbi0gS2VlcCBldmVyeXRoaW5nIHRoZSByZXF1ZXN0IGRvZXMgbm90IHRvdWNoLCB3b3JkIGZvciB3b3JkIHdoZXJlIHlvdSBjYW46IHNlY3Rpb24gbmFtZXMgYW5kIG9yZGVyLCBzaG90IG51bWJlcmluZyBhbmQgdGltZXN0YW1wcywgcmVmZXJlbmNlIHRhZ3MgKDxQaWN0dXJlIE4+LCA8U3ViamVjdCBOPiwgPFZpZGVvIE4+LCA8QXVkaW8gTj4pLCBzcGVha2VyIElEcyAoUzEpLCA8ZD5bTGFuZ3VhZ2VdIC4uLjwvZD4gYmxvY2tzIGFuZCB0aGUgc3R5bGUgb3BlbmluZy5cbi0gSWYgeW91IGFkZCwgcmVtb3ZlIG9yIHJldGltZSBzaG90cywgcmVudW1iZXIgdGhlbSBpbiBvcmRlciwga2VlcCBbU2hvdCAxXSB3aXRob3V0IGEgdGltZXN0YW1wLCBrZWVwIGxhdGVyIHRpbWVzdGFtcHMgaW4gTU06U1MubW1tLCBzdHJpY3RseSBpbmNyZWFzaW5nIGFuZCBpbnNpZGUgdGhlIGR1cmF0aW9uLCBhbmQga2VlcCByZXRlbnRpb25fYW5hbHlzaXMgaW4gc3RlcCB3aXRoIHN1YmplY3RfZGVmaW5pdGlvbnMuXG4tIEtlZXAgYSBmaXJzdC1saW5lIGFsaWdubWVudCBpbnN0cnVjdGlvbiAoXCJGb3IgdGhlIHRhcmdldCB2aWRlbywgLi4uXCIgb3IgXCJIb3cgdGhlIHJlZmVyZW5jZSBwaWN0dXJlcyBhbGlnbiAuLi5cIikgYXMgdGhlIGZpcnN0IGxpbmU7IFN0dWRpbyBjb3JyZWN0cyBpdHMgbnVtYmVycy5cbi0gU2hvcnQgcmVxdWVzdHMgYXJlIGRpcmVjdGlvbnMsIG5vdCB0ZXh0IHRvIHBhc3RlOiBcIm1vcmUgZGV0YWlsXCIgbWVhbnMgcmljaGVyIGRlc2NyaXB0aW9uIG9mIHdoYXQgaXMgYWxyZWFkeSB0aGVyZTsgXCJ0aWdodGVyXCIgbWVhbnMgZmV3ZXIsIG1vcmUgY29uY3JldGUgd29yZHMgd2l0aCBub3RoaW5nIGltcG9ydGFudCBsb3N0OyBcImFkZCBkaWFsb2d1ZVwiIG1lYW5zIGxpbmVzIHRoYXQgZml0IHRoZSBzY2VuZSBhbmQgdGhlIDIuNeKAkzMgd29yZHMtcGVyLXNlY29uZCBidWRnZXQ7IFwibm8gbXVzaWNcIiBtZWFucyBub25fZGllZ2V0aWNfbXVzaWM6IE4vQSBhbmQgbm8gc2NvcmUgYW55d2hlcmUuXG4tIERvIG5vdCBzb2Z0ZW4sIGNlbnNvciBvciB0b25lIGRvd24gZXhpc3RpbmcgY29udGVudCB1bmxlc3MgYXNrZWQuIERvIG5vdCBhZGQgY29tbWVudGFyeS5cbi0gT3V0cHV0IG9ubHkgdGhlIGNvbXBsZXRlIHJldmlzZWQgcHJvbXB0LCBpbiB0aGUgc2FtZSBmb3JtYXQgYXMgQ1VSUkVOVCBQUk9NUFQsIHdpdGggbm8gTWFya2Rvd24gZmVuY2VzIG9yIG5vdGVzLlxuIn0=").decode("utf-8"))

if __name__ == "__main__":
    main()
