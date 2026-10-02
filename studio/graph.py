"""Build ComfyUI API prompts for MiniMax H3 from a job description.

The graph is built in code, top to bottom, so its shape is readable here and
testable, instead of being a 30-node exported JSON that gets searched and
rewired at queue time. Structure is the same for every job of a mode; recipes
only change values, plus two optional stages (sigma shift, extend passes).

  UNET ─ user LoRAs ─ turbo LoRA ─ [sigma shift] ─ live preview ─┬─ guider
                                                                 └─ scheduler ─ [extend]
  CLIP + VAEs ─ ImageToVideo / ReferenceToVideo ─ conditioning + empty AV latent
  noise + guider + sampler + sigmas + latent ─ SamplerCustomAdvanced
  ─ VAEDecode + VAEDecodeAudio ─ VideoCombine (mp4 with audio)
"""
from __future__ import annotations

import json
import math
from typing import Any

FPS = 24

# UI key -> (w, h) ratio, and the exact ComfyUI ResolutionSelector label.
ASPECTS: dict[str, tuple[int, int]] = {
    "9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "2:3": (2, 3), "3:2": (3, 2),
    "3:4": (3, 4), "4:3": (4, 3), "21:9": (21, 9),
}
MAX_REFS = {"image": 9, "video": 3, "audio": 3}


class GraphError(ValueError):
    pass


def resolution(aspect: str, megapixels: float, multiple: int = 32) -> tuple[int, int]:
    """Identical to ComfyUI's ResolutionSelector (multiple=32, as the workflows used)."""
    if aspect not in ASPECTS:
        raise GraphError(f"unknown aspect ratio: {aspect}")
    w_ratio, h_ratio = ASPECTS[aspect]
    scale = math.sqrt(megapixels * 1024 * 1024 / (w_ratio * h_ratio))
    return (round(w_ratio * scale / multiple) * multiple, round(h_ratio * scale / multiple) * multiple)


def frame_count(seconds: float) -> int:
    """H3 needs 17n + 5 frames; same snapping as the upstream math node."""
    n = max(5, round(seconds * FPS))
    return n + (5 - n % 17) % 17


class _Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self._next = 1

    def add(self, class_type: str, title: str, **inputs: Any) -> str:
        nid = str(self._next)
        self._next += 1
        self.nodes[nid] = {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}
        return nid


def _name(entry_file: str) -> str:
    """Manifest paths are 'folder/name'; ComfyUI loaders want just the name."""
    return entry_file.split("/", 1)[1] if "/" in entry_file else entry_file


def build(job: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
    """Return a ComfyUI API prompt.

    job:   mode, prompt, aspect, megapixels, duration, seed, recipe (resolved),
           loras [{file, strength}], start_image, end_image, refs, output_prefix, crf
    files: model file names for unet, text_encoder, video_vae, audio_vae, turbo,
           and preview_vae ('' when TAEH3 is not on disk yet)
    """
    mode = job["mode"]
    recipe = job["recipe"]
    prompt = str(job.get("prompt") or "").strip()
    if not prompt:
        raise GraphError("prompt is empty")
    width, height = resolution(job.get("aspect", "9:16"), float(job.get("megapixels", 0.7)))
    length = frame_count(float(job.get("duration", 5)))

    g = _Graph()
    unet = g.add("UNETLoader", "Diffusion model", unet_name=_name(files["unet"]), weight_dtype="default")
    clip = g.add("CLIPLoader", "Text encoder", clip_name=_name(files["text_encoder"]), type="minimax", device="default")
    vae_v = g.add("VAELoader", "Video VAE", vae_name=_name(files["video_vae"]))
    vae_a = g.add("VAELoader", "Audio VAE", vae_name=_name(files["audio_vae"]))

    model = [unet, 0]
    for i, lora in enumerate(job.get("loras") or [], start=1):
        strength = float(lora.get("strength", 1.0))
        if strength == 0:
            continue
        nid = g.add("LoraLoaderModelOnly", f"LoRA {i}: {lora['file']}",
                    model=model, lora_name=lora["file"], strength_model=strength)
        model = [nid, 0]
    if recipe.get("lora") and files.get("turbo"):  # a recipe may run the base model without a turbo LoRA
        turbo = g.add("LoraLoaderModelOnly", f"Turbo LoRA ({recipe['id']})",
                      model=model, lora_name=_name(files["turbo"]), strength_model=recipe["strength"])
        model = [turbo, 0]
    if recipe.get("shift"):
        sv, sa = recipe["shift"]
        shift = g.add("MiniMaxH3SigmaShift", "Sigma shift", model=model, shift_video=sv, shift_audio=sa)
        model = [shift, 0]
    preview = g.add("ModelPreviewOverrideKJ", "Live preview", model=model,
                    max_resolution=640, jpeg_quality=80, suppress_default_preview=True,
                    preview_frames=1, preview_fps=5,
                    tiny_vae=_name(files["preview_vae"]) if files.get("preview_vae") else "none")
    model = [preview, 0]

    if mode in ("t2v", "i2v"):
        cond_inputs: dict[str, Any] = dict(clip=[clip, 0], vae=[vae_v, 0], prompt=prompt,
                                           width=width, height=height, length=length)
        if mode == "i2v":
            if not (job.get("start_image") or job.get("end_image")):
                raise GraphError("image-to-video needs a start frame, an end frame, or both")
            if job.get("start_image"):
                start = g.add("LoadImage", "Start frame", image=job["start_image"])
                cond_inputs["first_frame"] = [start, 0]
            if job.get("end_image"):
                end = g.add("LoadImage", "End frame", image=job["end_image"])
                cond_inputs["last_frame"] = [end, 0]
        cond = g.add("MiniMaxH3ImageToVideo", "Image to video" if mode == "i2v" else "Text to video", **cond_inputs)
    elif mode == "r2v":
        refs = job.get("refs") or []
        if not refs:
            raise GraphError("reference-to-video needs at least one reference")
        pack = g.add("MiniMaxH3ReferencePack", "References",
                     direction=prompt, references_json=references_json(refs),
                     prompt_provider="none", job_type="standard",
                     width=width, height=height, length_seconds=float(job.get("duration", 5)),
                     max_reference_edge=2048)
        ref_inputs: dict[str, Any] = dict(clip=[clip, 0], vae=[vae_v, 0], audio_vae=[vae_a, 0], prompt=prompt,
                                          width=width, height=height, length=length,
                                          ref_image_size=recipe.get("ref_image_size", "max"))
        # RefPack's 18 sockets, in its fixed order; unused ones carry nothing.
        for i in range(9):
            ref_inputs[f"ref_images.ref_image_{i}"] = [pack, i]
        for i in range(3):
            ref_inputs[f"ref_videos.ref_video_{i}"] = [pack, 9 + i]
            ref_inputs[f"ref_video_audios.ref_video_audio_{i}"] = [pack, 12 + i]
            ref_inputs[f"ref_audios.ref_audio_{i}"] = [pack, 15 + i]
        cond = g.add("MiniMaxH3ReferenceToVideo", "Reference to video", **ref_inputs)
    else:
        raise GraphError(f"unknown mode: {mode}")

    noise = g.add("RandomNoise", "Seed", noise_seed=int(job["seed"]))
    sampler = g.add("KSamplerSelect", "Sampler", sampler_name=recipe["sampler"])
    sigmas_node = g.add("BasicScheduler", "Scheduler", model=model, scheduler=recipe["scheduler"],
                        steps=recipe["steps"], denoise=1.0)
    sigmas = [sigmas_node, 0]
    if recipe.get("extend"):
        e = recipe["extend"]
        ext = g.add("ExtendIntermediateSigmas", "Extra low-noise passes", sigmas=sigmas, steps=e["steps"],
                    start_at_sigma=e["start"], end_at_sigma=e["end"], spacing=e["spacing"])
        sigmas = [ext, 0]
    guider = g.add("BasicGuider", "Guider", model=model, conditioning=[cond, 0])
    sample = g.add("SamplerCustomAdvanced", "Sample", noise=[noise, 0], guider=[guider, 0],
                   sampler=[sampler, 0], sigmas=sigmas, latent_image=[cond, 1])
    dec_v = g.add("VAEDecode", "Decode video", samples=[sample, 0], vae=[vae_v, 0])
    dec_a = g.add("VAEDecodeAudio", "Decode audio", samples=[sample, 0], vae=[vae_a, 0])
    g.add("VHS_VideoCombine", "Save", images=[dec_v, 0], audio=[dec_a, 0], frame_rate=FPS, loop_count=0,
          filename_prefix=job.get("output_prefix") or f"MMH3/{mode.upper()}", format="video/h264-mp4",
          pix_fmt="yuv420p", crf=int(job.get("crf", 12)), save_metadata=False, trim_to_audio=False,
          pingpong=False, save_output=True)
    return g.nodes


def references_json(refs: list[dict[str, Any]]) -> str:
    """RefPack's reference list. Order within a kind decides <Picture N> etc."""
    counts = {k: 0 for k in MAX_REFS}
    out = []
    for ref in refs:
        kind = ref.get("kind")
        if kind not in MAX_REFS or not ref.get("file"):
            raise GraphError(f"bad reference: {ref}")
        counts[kind] += 1
        if counts[kind] > MAX_REFS[kind]:
            raise GraphError(f"at most {MAX_REFS[kind]} {kind} references")
        item: dict[str, Any] = {"kind": kind, "file": ref["file"]}
        if kind == "video":
            item["use_soundtrack"] = bool(ref.get("use_soundtrack", True))
        for key in ("crop", "trim"):
            if ref.get(key) is not None:
                item[key] = ref[key]
        out.append(item)
    return json.dumps({"references": out}, separators=(",", ":"))


def reference_tags(refs: list[dict[str, Any]]) -> list[str]:
    """The tag(s) MiniMax will give each reference, aligned with `refs`.

    Mirrors RefPack's ReferenceSet.assign_tags: pictures and videos are numbered
    within their kind; <Audio N> runs across video soundtracks first (in video
    order), then standalone audio. A video with its soundtrack gets both tags.
    """
    tags = [""] * len(refs)
    pic = vid = 0
    audio_n = 0
    for i, ref in enumerate(refs):
        if ref.get("kind") == "image":
            pic += 1
            tags[i] = f"<Picture {pic}>"
    for i, ref in enumerate(refs):
        if ref.get("kind") == "video":
            vid += 1
            tags[i] = f"<Video {vid}>"
            if ref.get("use_soundtrack", True):
                audio_n += 1
                tags[i] += f" <Audio {audio_n}>"
    for i, ref in enumerate(refs):
        if ref.get("kind") == "audio":
            audio_n += 1
            tags[i] = f"<Audio {audio_n}>"
    return tags
