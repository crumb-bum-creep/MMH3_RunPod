"""Auto-prompting: turn a short idea into MiniMax H3's structured prompt.

Runs in the Studio server, not inside a ComfyUI graph, so:
  - you can draft and edit a prompt while another video is rendering,
  - the API key never lands in a workflow or ComfyUI's /history,
  - the prompt that was actually used is saved with the output.

T2V / I2V call OpenRouter directly with the system prompts in
config/system_prompts.yaml (written from MiniMax's official prompt guides).
Image mode picks the guide's task from the frames given: I2VA (start),
FL2VA (start + end) or L2VA (end only), and Studio writes the first-line
alignment instruction itself so its shot number and seconds are always right.
R2V reuses MiniMaxRefPack's own prompt writer (it knows how to send whole
videos, soundtracks and the <Picture N> tag contract), in a short-lived
subprocess so the server never imports torch.

`refine` (Tweak in Create) edits an existing prompt from a short request,
for any mode, whether the prompt was drafted or typed.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import aiohttp

from . import graph, paths, util

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REFPACK_DIR = Path(os.environ.get("MMH3_REFPACK_DIR", str(paths.COMFY_CODE / "custom_nodes" / "ComfyUI-MiniMaxRefPack")))
SECRETS = paths.CONFIG / "secrets.json"


class PromptError(RuntimeError):
    pass


def api_key() -> str:
    """Pod secret first (OPENROUTER_API_KEY / LLM_KEY), then a key saved in System."""
    for name in ("OPENROUTER_API_KEY", "LLM_KEY"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    return str((util.read_json(SECRETS, {}) or {}).get("openrouter_api_key") or "").strip()


def key_source() -> str:
    for name in ("OPENROUTER_API_KEY", "LLM_KEY"):
        if os.environ.get(name, "").strip():
            return f"pod secret {name}"
    return "saved in Studio" if api_key() else ""


def save_key(value: str) -> None:
    data = util.read_json(SECRETS, {}) or {}
    data["openrouter_api_key"] = value.strip()
    util.write_json(SECRETS, data)
    os.chmod(SECRETS, 0o600)


PROMPT_NAMES = ("t2v_auto", "i2v_auto", "fl2v_auto", "l2v_auto", "r2v_auto", "refine")


def _prompt_files() -> tuple[dict[str, Any], dict[str, Any]]:
    return util.image_yaml("system_prompts.yaml"), util.read_yaml(paths.CONFIG / "system_prompts.yaml", {}) or {}


def system_prompts() -> dict[str, str]:
    """The image's prompts, overridden by edits saved against the current version.

    An edit saved against an older `version` (or before versions existed) no
    longer overrides: the defaults moved on underneath it. It stays in the
    file and is offered back by stale_prompts()."""
    base, user = _prompt_files()
    merged = dict(base.get("prompts") or {})
    current = int(base.get("version") or 1)
    based_on = user.get("based_on") or {}
    for k, v in (user.get("prompts") or {}).items():
        if isinstance(v, str) and v.strip() and int(based_on.get(k) or 0) >= current:
            merged[k] = v
    return merged


def stale_prompts() -> dict[str, str]:
    """Your edits that were made against an older version of the defaults."""
    base, user = _prompt_files()
    current = int(base.get("version") or 1)
    based_on = user.get("based_on") or {}
    return {k: v for k, v in (user.get("prompts") or {}).items()
            if isinstance(v, str) and v.strip() and int(based_on.get(k) or 0) < current
            and v.strip() != str((base.get("prompts") or {}).get(k) or "").strip()}


def save_system_prompt(name: str, text: str) -> None:
    path = paths.CONFIG / "system_prompts.yaml"
    data = util.read_yaml(path, {}) or {}
    data.setdefault("prompts", {})[name] = text
    data.setdefault("based_on", {})[name] = int(util.image_yaml("system_prompts.yaml").get("version") or 1)
    util.write_yaml(path, data)


def _image_part(path: Path, max_edge: int = 1536) -> dict[str, Any]:
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_edge, max_edge))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def clip_seconds(job: dict[str, Any]) -> float:
    """The real clip length: frames are snapped to the model's 17k+5 grid at 24 fps."""
    return graph.frame_count(float(job.get("duration", 5))) / graph.FPS


def keyframe_task(job: dict[str, Any]) -> str | None:
    """Image mode's task in MiniMax's guide: i2v (start), fl2v (start + end), l2v (end only)."""
    if job.get("mode") != "i2v":
        return None
    start, end = bool(job.get("start_image")), bool(job.get("end_image"))
    return "fl2v" if start and end else "l2v" if end else "i2v"


def _keyframe_images(job: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, input file) for the frames the node tokenizes, in its order: first, then last."""
    task = keyframe_task(job)
    secs = f"{clip_seconds(job):.2f}"
    if task == "i2v":
        return [("<Picture 1>, the first frame (0.00 s):", job["start_image"])]
    if task == "fl2v":
        return [("Picture 1, the first frame (0.00 s):", job["start_image"]),
                (f"Picture 2, the last frame ({secs} s):", job["end_image"])]
    if task == "l2v":
        return [(f"<Picture 1>, the last frame ({secs} s):", job["end_image"])]
    return []


def alignment_line(job: dict[str, Any], last_shot: int | str = 1) -> str:
    """MiniMax's first-line keyframe instruction for Image mode, '' otherwise."""
    task = keyframe_task(job)
    secs = f"{clip_seconds(job):.2f}"
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


_ALIGN = re.compile(r"\A\s*(?:For the target video, at 0\.00 seconds|How the reference pictures align)[^\n]*\n*", re.I)
_SHOT = re.compile(r"\[Shot (\d+)\]", re.I)


def align_keyframes(job: dict[str, Any], prompt: str) -> str:
    """Give an Image-mode prompt the right alignment line for its frames, shots and length.

    Applies to prompts in MiniMax's structured format (or that already carry an
    alignment line); a free-form prompt is left exactly as written."""
    task = keyframe_task(job)
    text = str(prompt or "")
    if not task:
        return text
    had_line = bool(_ALIGN.match(text))
    body = _ALIGN.sub("", text, count=1).lstrip()
    if not had_line and "integrated_multimodal_description:" not in body:
        return text
    shots = [int(n) for n in _SHOT.findall(body)]
    return f"{alignment_line(job, max(shots) if shots else 1)}\n\n{body}"


def format_block(job: dict[str, Any]) -> str:
    width, height = graph.resolution(job.get("aspect", "9:16"), float(job.get("megapixels", 0.7)))
    orient = "portrait" if height > width else "landscape" if width > height else "square"
    lines = [f"duration: {clip_seconds(job):.2f} seconds",
             f"frame: {width} x {height} ({job.get('aspect', '9:16')}, {orient})"]
    task = keyframe_task(job)
    if task == "i2v":
        lines.append("alignment line (your first line, verbatim): " + alignment_line(job))
    elif task:
        lines.append("alignment line (your first line; N = the number of your final shot): "
                     + alignment_line(job, "N"))
    return "TARGET FORMAT:\n" + "\n".join(lines)


async def _openrouter(messages: list[dict[str, Any]], cfg: dict[str, Any], key: str) -> str:
    payload: dict[str, Any] = {"model": cfg.get("model") or "google/gemini-3-flash-preview",
                               "messages": messages, "max_tokens": 8192}
    effort = cfg.get("reasoning_effort")
    if effort in ("none", "low", "medium", "high"):
        payload["reasoning"] = {"effort": effort}
    headers = {"Authorization": f"Bearer {key}", "X-Title": "MMH3 Studio"}
    timeout = aiohttp.ClientTimeout(total=float(cfg.get("timeout_seconds", 150)))
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(OPENROUTER_URL, json=payload, headers=headers) as r:
                raw = await r.text()
                status = r.status
    except asyncio.TimeoutError:
        raise PromptError("OpenRouter took too long to answer. Try again.") from None
    except aiohttp.ClientError as exc:
        raise PromptError(f"Can't reach OpenRouter from the pod ({type(exc).__name__}).") from None
    try:
        body = json.loads(raw)
    except ValueError:
        raise PromptError(f"OpenRouter answered HTTP {status} with no usable reply.") from None
    if status >= 400 or (isinstance(body, dict) and body.get("error")):
        err = body.get("error") if isinstance(body, dict) else None
        msg = err.get("message") if isinstance(err, dict) else err
        if status == 401:
            msg = "the OpenRouter key was rejected"
        raise PromptError(f"OpenRouter {status}: {msg or str(body)[:300]}")
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise PromptError(f"OpenRouter returned no text: {str(body)[:300]}") from None
    if isinstance(text, list):  # some providers return content parts
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    text = str(text or "").strip()
    if not text:
        raise PromptError("OpenRouter returned an empty prompt")
    return _strip_fences(text)


def _strip_fences(text: str) -> str:
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _require_key() -> str:
    key = api_key()
    if not key:
        raise PromptError("No OpenRouter key. Add OPENROUTER_API_KEY as a pod secret or save one in System.")
    return key


def _system_prompt_for(job: dict[str, Any], prompts: dict[str, str]) -> str:
    mode = job["mode"]
    if mode == "i2v":
        return prompts.get(f"{keyframe_task(job)}_auto") or prompts.get("i2v_auto", "")
    if mode in ("t2v", "r2v"):
        return prompts.get(f"{mode}_auto", "")
    raise PromptError(f"unknown mode {mode}")


async def write(job: dict[str, Any]) -> str:
    """Draft the full MiniMax prompt for a job from its `idea`."""
    key = _require_key()
    idea = str(job.get("idea") or "").strip()
    if not idea:
        raise PromptError("Write an idea first")
    cfg = util.settings().get("prompting") or {}
    prompts = system_prompts()
    mode = job["mode"]
    if mode in ("t2v", "i2v"):
        content: list[dict[str, Any]] = [{"type": "text", "text": f"USER DIRECTION:\n{idea}\n\n{format_block(job)}"}]
        for label, rel in _keyframe_images(job):
            content.append({"type": "text", "text": label})
            content.append(_image_part(paths.INPUT / rel))
        messages = [{"role": "system", "content": _system_prompt_for(job, prompts)},
                    {"role": "user", "content": content}]
        return align_keyframes(job, await _openrouter(messages, cfg, key))
    if mode == "r2v":
        return await _refpack_prompt(job, prompts.get("r2v_auto", ""), cfg, key)
    raise PromptError(f"unknown mode {mode}")


async def refine(job: dict[str, Any], prompt: str, request: str) -> str:
    """Edit an existing prompt to make one requested change (Tweak in Create)."""
    key = _require_key()
    prompt, request = str(prompt or "").strip(), str(request or "").strip()
    if not prompt:
        raise PromptError("There's no prompt to tweak yet")
    if not request:
        raise PromptError("Say what to change")
    cfg = util.settings().get("prompting") or {}
    prompts = system_prompts()
    rules = _system_prompt_for(job, prompts)
    system = prompts.get("refine", "") + ("\n\n# PROMPT RULES\n\n" + rules if rules else "")
    content: list[dict[str, Any]] = [{"type": "text", "text": (
        f"CHANGE REQUEST:\n{request}\n\n{format_block(job)}\n\nCURRENT PROMPT:\n{prompt}")}]
    if job["mode"] == "i2v":
        for label, rel in _keyframe_images(job):
            content.append({"type": "text", "text": label})
            content.append(_image_part(paths.INPUT / rel))
    elif job["mode"] == "r2v":
        refs = job.get("refs") or []
        for tag, ref in zip(graph.reference_tags(refs), refs):
            if ref.get("kind") == "image":
                content.append({"type": "text", "text": f"image_reference {tag} - the next image:"})
                content.append(_image_part(paths.INPUT / ref["file"]))
            else:
                content.append({"type": "text", "text": f"{tag}: a {ref.get('kind')} reference (not attached here)"})
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    return align_keyframes(job, await _openrouter(messages, cfg, key))


async def _refpack_prompt(job: dict[str, Any], system_prompt: str, cfg: dict[str, Any], key: str) -> str:
    width, height = graph.resolution(job.get("aspect", "9:16"), float(job.get("megapixels", 0.7)))
    request = {
        "references_json": graph.references_json(job.get("refs") or []),
        "input_dir": str(paths.INPUT),
        "direction": str(job.get("idea") or ""),
        "system_prompt": system_prompt,
        "model": cfg.get("model") or "google/gemini-3-flash-preview",
        "reasoning_effort": cfg.get("reasoning_effort") or "medium",
        "width": width, "height": height, "length_seconds": float(job.get("duration", 5)),
    }
    env = dict(os.environ, OPENROUTER_API_KEY=key, PYTHONPATH=f"{REFPACK_DIR}:{paths.IMAGE_ROOT}")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "studio.refprompt",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(request).encode()),
                                          timeout=float(cfg.get("timeout_seconds", 150)) + 60)
    except asyncio.TimeoutError:
        proc.kill()
        raise PromptError("the reference prompt writer timed out") from None
    try:
        result = json.loads(out.decode() or "{}")
    except ValueError:
        result = {}
    if result.get("prompt"):
        return _strip_fences(str(result["prompt"]))
    detail = result.get("error") or err.decode(errors="replace").strip().splitlines()[-1:] or ["unknown error"]
    raise PromptError(f"reference prompt writer failed: {detail if isinstance(detail, str) else detail[0]}")
