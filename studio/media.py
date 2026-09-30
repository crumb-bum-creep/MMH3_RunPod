"""ffmpeg / Pillow helpers: probing, thumbnails, last-frame extraction."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}


def kind(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "other"


def probe(path: Path) -> dict[str, Any]:
    """{duration, width, height, has_audio, has_video} (missing keys when unknown)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    info: dict[str, Any] = {"has_audio": False, "has_video": False}
    for s in data.get("streams") or []:
        if s.get("codec_type") == "audio":
            info["has_audio"] = True
        elif s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1:
            info["has_video"] = True
            info.setdefault("width", s.get("width"))
            info.setdefault("height", s.get("height"))
    try:
        info["duration"] = round(float((data.get("format") or {}).get("duration")), 2)
    except (TypeError, ValueError):
        pass
    return info


def image_thumb(src: Path, dst: Path, size: int = 360) -> bool:
    from PIL import Image, ImageOps

    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((size, size))
            dst.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst, "JPEG", quality=82)
        return True
    except Exception:
        return False


def video_thumb(src: Path, dst: Path, size: int = 360, at: float = 0.5) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(at), "-i", str(src), "-frames:v", "1",
           "-vf", f"scale='min({size},iw)':-2", "-q:v", "4", str(dst)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60, check=True)
        return dst.exists()
    except (OSError, subprocess.SubprocessError):
        # very short clips: seeking past the end yields nothing, retry at 0
        if at:
            return video_thumb(src, dst, size, 0)
        return False


def last_frame(src: Path, dst: Path) -> bool:
    """Write the final frame of a video as PNG (for 'continue from last frame')."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-sseof", "-0.5", "-i", str(src),
           "-update", "1", "-frames:v", "999", str(dst)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        return dst.exists() and dst.stat().st_size > 0
    except (OSError, subprocess.SubprocessError):
        return False
