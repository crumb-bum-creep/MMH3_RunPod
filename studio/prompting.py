"""Auto-prompting: turn a short idea into MiniMax H3's structured prompt.

Runs in the Studio server, not inside a ComfyUI graph, so:
  - you can draft and edit a prompt while another video is rendering,
  - the API key never lands in a workflow or ComfyUI's /history,
  - the prompt that was actually used is saved with the output.

T2V / I2V call OpenRouter directly with your system prompts (the same ones
the previous build used). R2V reuses MiniMaxRefPack's own prompt writer (it
knows how to send whole videos, soundtracks and the <Picture N> tag contract),
in a short-lived subprocess so the server never imports torch.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
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


def system_prompts() -> dict[str, str]:
    """User copy on the volume if present, else the image's canonical prompts."""
    user = util.read_yaml(paths.CONFIG / "system_prompts.yaml", {}) or {}
    base = util.image_yaml("system_prompts.yaml")
    merged = dict((base.get("prompts") or {}))
    merged.update({k: v for k, v in (user.get("prompts") or {}).items() if isinstance(v, str) and v.strip()})
    return merged


def save_system_prompt(name: str, text: str) -> None:
    path = paths.CONFIG / "system_prompts.yaml"
    data = util.read_yaml(path, {}) or {}
    data.setdefault("prompts", {})[name] = text
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


def format_block(job: dict[str, Any]) -> str:
    width, height = graph.resolution(job.get("aspect", "9:16"), float(job.get("megapixels", 0.7)))
    frames = graph.frame_count(float(job.get("duration", 5)))
    seconds = round(frames / graph.FPS, 2)
    return f"Duration: {seconds} seconds\nWidth: {width}\nHeight: {height}"


async def _openrouter(messages: list[dict[str, Any]], cfg: dict[str, Any], key: str) -> str:
    payload: dict[str, Any] = {"model": cfg.get("model") or "google/gemini-3-flash-preview",
                               "messages": messages, "max_tokens": 4096}
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


async def write(job: dict[str, Any]) -> str:
    """Draft the full MiniMax prompt for a job from its `idea`."""
    key = api_key()
    if not key:
        raise PromptError("No OpenRouter key. Add OPENROUTER_API_KEY as a pod secret or save one in System.")
    idea = str(job.get("idea") or "").strip()
    if not idea:
        raise PromptError("Write an idea first")
    cfg = util.settings().get("prompting") or {}
    prompts = system_prompts()
    mode = job["mode"]
    if mode in ("t2v", "i2v"):
        content: list[dict[str, Any]] = [{"type": "text", "text": f"{idea}\n\n{format_block(job)}"}]
        if mode == "i2v":
            for key_name in ("start_image", "end_image"):
                if job.get(key_name):
                    content.append(_image_part(paths.INPUT / job[key_name]))
        messages = [{"role": "system", "content": prompts.get(f"{mode}_auto", "")},
                    {"role": "user", "content": content}]
        return await _openrouter(messages, cfg, key)
    if mode == "r2v":
        return await _refpack_prompt(job, prompts.get("r2v_auto", ""), cfg, key)
    raise PromptError(f"unknown mode {mode}")


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
