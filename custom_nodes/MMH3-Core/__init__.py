from __future__ import annotations

import json
from pathlib import Path

import folder_paths

NONE = "(none)"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}


def _files(exts: set[str]) -> list[str]:
    root = Path(folder_paths.get_input_directory())
    items = []
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in exts:
                items.append(path.relative_to(root).as_posix())
    return [NONE] + sorted(items, key=str.casefold)


class MMH3AssetReferenceSelector:
    """Build MiniMaxRefPack references_json from reusable ComfyUI/input assets."""

    @classmethod
    def INPUT_TYPES(cls):
        images = _files(IMAGE_EXT)
        videos = _files(VIDEO_EXT)
        audios = _files(AUDIO_EXT)
        optional = {}
        for i in range(1, 10):
            optional[f"picture_{i}"] = (images, {"default": NONE})
        for i in range(1, 4):
            optional[f"video_{i}"] = (videos, {"default": NONE})
            optional[f"video_{i}_soundtrack"] = ("BOOLEAN", {"default": True})
        for i in range(1, 4):
            optional[f"audio_{i}"] = (audios, {"default": NONE})
        return {"optional": optional}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("references_json",)
    FUNCTION = "build"
    CATEGORY = "MMH3/References"
    DESCRIPTION = (
        "Select persistent assets already present in ComfyUI/input. "
        "Wire references_json into MiniMax References Manager."
    )

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return json.dumps(kwargs, sort_keys=True, separators=(",", ":"))

    def build(self, **kwargs):
        refs = []
        for i in range(1, 10):
            value = str(kwargs.get(f"picture_{i}") or NONE)
            if value != NONE:
                refs.append({"kind": "image", "file": value})
        for i in range(1, 4):
            value = str(kwargs.get(f"video_{i}") or NONE)
            if value != NONE:
                refs.append({
                    "kind": "video",
                    "file": value,
                    "use_soundtrack": bool(kwargs.get(f"video_{i}_soundtrack", True)),
                })
        for i in range(1, 4):
            value = str(kwargs.get(f"audio_{i}") or NONE)
            if value != NONE:
                refs.append({"kind": "audio", "file": value})
        return (json.dumps({"references": refs}, separators=(",", ":")),)


NODE_CLASS_MAPPINGS = {
    "MMH3AssetReferenceSelector": MMH3AssetReferenceSelector,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MMH3AssetReferenceSelector": "MMH3 Asset Reference Selector",
}
