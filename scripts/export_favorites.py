"""Export every clip you starred in Studio as one zip: the videos, the prompt and
settings each was made with, and the reference images / videos / audio it used.

Upload this file to the workspace root in Jupyter (/workspace) and run it from a
Jupyter terminal:

    python export_favorites.py                  # all favorites
    python export_favorites.py --group "Keepers"  # only favorites in one group
    python export_favorites.py --list           # show what would be exported

or in a notebook cell:  %run export_favorites.py

The zip lands in /workspace/exports/; right-click it in Jupyter's file browser
and choose Download. Layout:

    manifest.json        every clip: prompt, settings, references, where each file is in the zip
    MANIFEST.md          the same, readable
    clips/<n> <name>/    the video, prompt.txt, settings.json (Studio's full record),
                         and first-frame.png (holds the ComfyUI graph, drag it into ComfyUI)
    references/          each reference / start / end file once, under its input path
    config/              your Studio settings and recipe overrides (no API keys)

Only uses the Python standard library and only reads from the workspace.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path


def find_workspace() -> Path:
    for cand in (os.environ.get("MMH3_WORKSPACE"), Path(__file__).resolve().parent, "/workspace"):
        if cand and (Path(cand) / "mmh3" / "data").is_dir():
            return Path(cand)
    sys.exit("Can't find the Studio workspace (expected /workspace/mmh3/data). Set MMH3_WORKSPACE.")


WS = find_workspace()
OUTPUT = WS / "ComfyUI" / "output"
INPUT = WS / "ComfyUI" / "input"
DATA = WS / "mmh3" / "data"
CONFIG = WS / "mmh3" / "config"
CONFIG_FILES = ("settings.yaml", "recipes.yaml", "system_prompts.yaml", "loras.yaml")  # never secrets.json
STORED = {".mp4", ".png", ".jpg", ".jpeg", ".webp", ".webm", ".mov", ".mp3", ".m4a", ".ogg", ".flac"}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def inside(root: Path, rel: str) -> Path | None:
    """root/rel, or None if rel is empty or points outside root."""
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    if not rel:
        return None
    p = (root / rel).resolve()
    return p if root.resolve() in p.parents else None


def record_for(rel: str, video: Path, legacy: dict) -> dict:
    rec = read_json(video.with_suffix(video.suffix + ".h3.json"), None)
    if not isinstance(rec, dict):
        rec = legacy.get(rel) if isinstance(legacy.get(rel), dict) else {}
    return rec


def summary(rec: dict) -> dict:
    """The fields that matter for remaking a clip, from a Studio record (new or older builds)."""
    if rec.get("studio"):
        return {
            "mode": rec.get("mode"), "prompt": rec.get("prompt") or "", "idea": rec.get("idea") or "",
            "prompt_mode": rec.get("prompt_mode"), "seed": rec.get("seed"),
            "aspect": rec.get("aspect"), "megapixels": rec.get("megapixels"),
            "width": rec.get("width"), "height": rec.get("height"),
            "duration": rec.get("duration"), "frames": rec.get("frames"),
            "checkpoint": rec.get("checkpoint"), "recipe_id": rec.get("recipe_id"),
            "recipe": rec.get("recipe") or {},
            "overrides": rec.get("overrides") or {},
            "loras": [{"file": x.get("file"), "strength": x.get("strength")} for x in rec.get("loras") or []],
            "start_image": rec.get("start_image"), "end_image": rec.get("end_image"),
            "refs": rec.get("refs") or [], "reference_tags": rec.get("reference_tags") or [],
            "created": rec.get("created"), "finished": rec.get("finished"),
        }
    return {  # records from the build before Studio
        "mode": rec.get("mode"), "prompt": rec.get("actual_prompt") or rec.get("prompt") or "",
        "idea": rec.get("prompt_idea") or "", "prompt_mode": rec.get("prompt_mode"), "seed": rec.get("seed"),
        "aspect": rec.get("aspect_ratio"), "megapixels": rec.get("megapixels"), "duration": rec.get("duration"),
        "checkpoint": rec.get("base_checkpoint"), "recipe_id": rec.get("generation_profile"),
        "loras": [{"file": x.get("filename") or x.get("lora") or x.get("file"), "strength": x.get("strength")}
                  for x in rec.get("loras") or [] if isinstance(x, dict)],
        "start_image": rec.get("starting_image"), "end_image": rec.get("ending_image"),
        "refs": rec.get("refs") or [],
    }


def first_frame(video: Path) -> Path | None:
    stem = re.sub(r"(?i)-audio$", "", video.stem)
    for cand in (video.with_name(stem + ".png"), video.with_name(video.stem + ".png")):
        if cand.exists():
            return cand
    return None


def clean(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._() +\-]+", "_", name).strip(" .")[:80] or "clip"


def fmt_time(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return ""


def prompt_text(rel: str, s: dict, tags: dict[str, str]) -> str:
    lines = [f"# {rel}", ""]
    if s.get("idea") and s.get("idea") != s.get("prompt"):
        lines += ["## Idea", s["idea"].strip(), ""]
    lines += ["## Prompt", (s.get("prompt") or "(not recorded)").strip(), ""]
    if tags:
        lines += ["## References (paths from the top of the zip)"] + [f"{t}  {f}" for f, t in tags.items()] + [""]
    return "\n".join(lines)


def markdown(clips: list[dict], made: str) -> str:
    out = [f"# Studio favorites, exported {made}", "", f"{len(clips)} clips.", ""]
    for c in clips:
        s = c["settings"]
        out += [f"## {c['folder']}", "", f"- Source: `{c['source']}`"]
        if c.get("missing"):
            out += ["- **Video file not found on the volume**", ""]
            continue
        rec = s.get("recipe") or {}
        bits = [s.get("mode", "").upper(), s.get("aspect"), f"{s['width']}x{s['height']}" if s.get("width") else None,
                f"{s['duration']}s" if s.get("duration") else None, f"seed {s.get('seed')}"]
        out.append("- " + " · ".join(str(b) for b in bits if b))
        out.append(f"- Model: {s.get('checkpoint') or '?'} · recipe {rec.get('label') or s.get('recipe_id') or '?'}"
                   + (f" ({rec.get('sampler')}/{rec.get('scheduler')}, {rec.get('steps')} steps)" if rec.get("steps") else ""))
        if s.get("overrides"):
            out.append(f"- Overrides: `{json.dumps(s['overrides'])}`")
        for lora in s.get("loras") or []:
            out.append(f"- LoRA: {lora.get('file')} @ {lora.get('strength')}")
        for a in c["assets"]:
            where = f"`{a['zip_path']}`" if a.get("zip_path") else "**missing**"
            out.append(f"- {a['role']}{(' ' + a['tag']) if a.get('tag') else ''}: {where}")
        out += ["", "```", (s.get("prompt") or "").strip(), "```", ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Zip your Studio favorites with prompts, settings and references.")
    ap.add_argument("--group", help="only favorites in this Library group")
    ap.add_argument("--out", type=Path, default=WS / "exports", help="folder for the zip (default /workspace/exports)")
    ap.add_argument("--list", action="store_true", help="list what would be exported and stop")
    args, _ = ap.parse_known_args()  # tolerate Jupyter's own argv under %run

    lib = read_json(DATA / "output_library.json", {}) or {}
    legacy = read_json(DATA / "output_meta.json", {}) or {}
    nicknames = read_json(DATA / "assets.json", {}) or {}
    favs = [(rel, v) for rel, v in (lib.get("videos") or {}).items()
            if isinstance(v, dict) and v.get("favorite") and (not args.group or v.get("group") == args.group)]
    if not favs:
        print("No favorites" + (f" in group {args.group!r}" if args.group else "") + ". Star clips in Studio's Library first.")
        return 1

    clips = []
    for rel, v in favs:
        video = inside(OUTPUT, rel)
        exists = bool(video and video.is_file())
        rec = record_for(rel, video, legacy) if exists else {}
        clips.append({"source": rel, "video": video if exists else None, "record": rec, "library": v,
                      "mtime": video.stat().st_mtime if exists else 0})
    clips.sort(key=lambda c: (c["video"] is None, c["mtime"]))  # oldest first; missing ones last

    if args.list:
        for c in clips:
            s = summary(c["record"])
            print(("MISSING  " if not c["video"] else "") + c["source"], "|", (s.get("prompt") or "")[:90].replace("\n", " "))
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    made = time.strftime("%Y-%m-%d %H:%M")
    dest = args.out / f"favorites-{time.strftime('%Y%m%d-%H%M%S')}{('-' + clean(args.group)) if args.group else ''}.zip"
    tmp = dest.with_suffix(".zip.part")
    added_refs: dict[str, str] = {}
    entries = []
    width = len(str(len(clips)))

    def put(zf: zipfile.ZipFile, src: Path, arc: str) -> None:
        comp = zipfile.ZIP_STORED if src.suffix.lower() in STORED else zipfile.ZIP_DEFLATED
        zf.write(src, arc, compress_type=comp)

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for n, c in enumerate(clips, 1):
            rel = c["source"]
            folder = f"clips/{n:0{width}d} {clean(Path(rel).stem)}"
            s = summary(c["record"])
            entry = {"folder": folder, "source": rel, "library": {k: c["library"].get(k) for k in ("group", "tags")},
                     "created": fmt_time(s.get("created") or c["mtime"]), "settings": s, "assets": []}
            if not c["video"]:
                entry["missing"] = True
                entries.append(entry)
                print(f"[{n}/{len(clips)}] missing on disk: {rel}")
                continue
            print(f"[{n}/{len(clips)}] {rel}")
            entry["video"] = f"{folder}/{c['video'].name}"
            put(zf, c["video"], entry["video"])
            png = first_frame(c["video"])
            if png:
                entry["first_frame"] = f"{folder}/first-frame.png"
                put(zf, png, entry["first_frame"])

            wanted = []  # (role, input rel, tag, ref details)
            if s.get("start_image"):
                wanted.append(("start image", s["start_image"], "", {}))
            if s.get("end_image"):
                wanted.append(("end image", s["end_image"], "", {}))
            tags = s.get("reference_tags") or []
            for i, ref in enumerate(s.get("refs") or []):
                if isinstance(ref, dict) and ref.get("file"):
                    extra = {k: ref[k] for k in ("use_soundtrack", "crop", "trim") if ref.get(k) is not None}
                    wanted.append((f"{ref.get('kind', 'reference')} reference", ref["file"],
                                   tags[i] if i < len(tags) else "", extra))
            tag_lines = {}
            for role, frel, tag, extra in wanted:
                asset = {"role": role, "input_path": frel, "tag": tag, **extra}
                nick = (nicknames.get(frel) or {}).get("nickname") if isinstance(nicknames.get(frel), dict) else None
                if nick:
                    asset["nickname"] = nick
                src = inside(INPUT, frel)
                if frel in added_refs:
                    asset["zip_path"] = added_refs[frel]
                elif src and src.is_file():
                    arc = "references/" + src.relative_to(INPUT.resolve()).as_posix()
                    put(zf, src, arc)
                    added_refs[frel] = asset["zip_path"] = arc
                else:
                    asset["missing"] = True
                entry["assets"].append(asset)
                tag_lines[asset.get("zip_path") or f"{frel} (missing)"] = tag or role
            zf.writestr(f"{folder}/prompt.txt", prompt_text(rel, s, tag_lines))
            zf.writestr(f"{folder}/settings.json", json.dumps(c["record"], indent=2, default=str))
            entries.append(entry)

        configs = []
        for name in CONFIG_FILES:
            p = CONFIG / name
            if p.is_file():
                zf.write(p, f"config/{name}")
                configs.append(f"config/{name}")
        manifest = {"exported": made, "workspace": str(WS), "group": args.group, "count": len(entries),
                    "config_files": configs, "clips": entries}
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        zf.writestr("MANIFEST.md", markdown(entries, made))
    os.replace(tmp, dest)

    missing = sum(1 for e in entries if e.get("missing")) + sum(1 for e in entries for a in e["assets"] if a.get("missing"))
    size = dest.stat().st_size / 1024 ** 2
    print(f"\nWrote {dest} ({size:.1f} MB): {len(entries)} clips, {len(added_refs)} reference files.")
    if missing:
        print(f"{missing} file(s) weren't on the volume any more; they're marked missing in the manifest.")
    print("Download it from Jupyter's file browser (right-click > Download).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
