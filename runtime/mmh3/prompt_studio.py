from __future__ import annotations

import copy
import re
import time
import uuid
from typing import Any


DEFAULT_MODEL = "google/gemini-3-flash-preview"

SUBJECT_TYPES = [
    "person",
    "animal",
    "object",
    "environment",
    "wardrobe",
    "visual_style",
    "vehicle",
    "other",
]

CAMERA_MOTIONS = [
    "Static Shot",
    "Zoom In",
    "Zoom Out",
    "Push In",
    "Pull Out",
    "Pan Left",
    "Pan Right",
    "Truck Left",
    "Truck Right",
    "Tilt Up",
    "Tilt Down",
    "Pedestal Up",
    "Pedestal Down",
    "Arc Shot",
    "Tracking Shot",
    "Shake Slightly",
    "Shake Strongly",
    "POV",
    "Roll Clockwise",
    "Roll Counterclockwise",
    "Custom",
]

FRAMING_PRESETS = [
    "Extreme Wide",
    "Wide",
    "Full",
    "Medium Full",
    "Medium",
    "Medium Close-Up",
    "Close-Up",
    "Extreme Close-Up",
    "Two-Shot",
    "Over-the-Shoulder",
    "POV",
    "Custom",
]


def _now() -> float:
    return time.time()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def subject_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "label": {"type": "string"},
            "type": {"type": "string", "enum": SUBJECT_TYPES},
            "description": {"type": "string"},
            "performance_notes": {"type": "string"},
            "reference": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["none", "asset", "picture_slot"]},
                    "picture_number": {"type": "integer", "minimum": 1, "maximum": 9},
                    "asset_file": {"type": "string"},
                    "analyze": {"type": "boolean"},
                },
                "required": ["mode", "picture_number", "asset_file", "analyze"],
                "additionalProperties": False,
            },
            "locks": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "id",
            "label",
            "type",
            "description",
            "performance_notes",
            "reference",
            "locks",
        ],
        "additionalProperties": False,
    }


def blocking_item_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "subject_id": {"type": "string"},
            "kind": {"type": "string", "enum": ["subject", "object", "environment", "note"]},
            "label": {"type": "string"},
            "x": {"type": "number", "minimum": 0, "maximum": 1},
            "y": {"type": "number", "minimum": 0, "maximum": 1},
            "width": {"type": "number", "minimum": 0.02, "maximum": 1},
            "height": {"type": "number", "minimum": 0.02, "maximum": 1},
            "facing": {"type": "string", "enum": ["unspecified", "left", "right", "camera", "away"]},
            "note": {"type": "string"},
        },
        "required": [
            "id",
            "subject_id",
            "kind",
            "label",
            "x",
            "y",
            "width",
            "height",
            "facing",
            "note",
        ],
        "additionalProperties": False,
    }


def shot_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "start_seconds": {"type": "number", "minimum": 0},
            "framing": {"type": "string"},
            "camera_motion": {"type": "string"},
            "camera_custom": {"type": "string"},
            "amplitude": {"type": "string"},
            "speed": {"type": "string"},
            "subjects": {"type": "array", "items": {"type": "string"}},
            "action": {"type": "string"},
            "dialogue": {"type": "string"},
            "sound": {"type": "string"},
            "blocking": {"type": "array", "items": blocking_item_schema()},
            "locks": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "id",
            "start_seconds",
            "framing",
            "camera_motion",
            "camera_custom",
            "amplitude",
            "speed",
            "subjects",
            "action",
            "dialogue",
            "sound",
            "blocking",
            "locks",
        ],
        "additionalProperties": False,
    }


def scene_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "concept": {"type": "string"},
            "environment": {"type": "string"},
            "visual_style": {"type": "string"},
            "soundscape": {"type": "string"},
            "music": {"type": "string"},
            "shot_count_mode": {"type": "string", "enum": ["auto", "exact"]},
            "exact_shot_count": {"type": "integer", "minimum": 1, "maximum": 12},
            "subjects": {"type": "array", "items": subject_schema()},
            "shots": {"type": "array", "items": shot_schema()},
            "locks": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "concept",
            "environment",
            "visual_style",
            "soundscape",
            "music",
            "shot_count_mode",
            "exact_shot_count",
            "subjects",
            "shots",
            "locks",
        ],
        "additionalProperties": False,
    }


def structured_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


def empty_subject(index: int = 1) -> dict[str, Any]:
    return {
        "id": _id("subject"),
        "label": f"Subject {index}",
        "type": "person",
        "description": "",
        "performance_notes": "",
        "reference": {
            "mode": "none",
            "picture_number": min(max(index, 1), 9),
            "asset_file": "",
            "analyze": False,
        },
        "locks": [],
    }


def empty_shot(index: int = 1) -> dict[str, Any]:
    return {
        "id": _id("shot"),
        "start_seconds": 0.0 if index == 1 else float((index - 1) * 3),
        "framing": "Medium",
        "camera_motion": "Static Shot",
        "camera_custom": "",
        "amplitude": "",
        "speed": "",
        "subjects": [],
        "action": "",
        "dialogue": "",
        "sound": "",
        "blocking": [],
        "blocking_sketch": [],
        "locks": [],
    }


def new_project(
    *,
    name: str = "Untitled Prompt",
    mode: str = "r2v",
    duration: float = 10,
    aspect_ratio: str = "9:16 (Portrait Widescreen)",
    concept: str = "",
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    mode = mode if mode in {"t2v", "i2v", "r2v"} else "r2v"
    now = _now()
    return {
        "id": _id("prompt"),
        "name": name.strip() or "Untitled Prompt",
        "mode": mode,
        "duration": max(1.0, float(duration or 10)),
        "aspect_ratio": aspect_ratio or "9:16 (Portrait Widescreen)",
        "model": model.strip() or DEFAULT_MODEL,
        "starting_image": {"asset_file": "", "analyze": True},
        "scene": {
            "concept": concept,
            "environment": "",
            "visual_style": "",
            "soundscape": "",
            "music": "",
            "shot_count_mode": "auto",
            "exact_shot_count": 1,
            "subjects": [],
            "shots": [empty_shot(1)],
            "locks": [],
        },
        "final_prompt": "",
        "validation": {"valid": False, "errors": [], "warnings": []},
        "revisions": [],
        "created_at": now,
        "updated_at": now,
    }


def _clean_subject(value: Any, index: int) -> dict[str, Any]:
    base = empty_subject(index)
    if isinstance(value, dict):
        for key in ("id", "label", "type", "description", "performance_notes", "locks"):
            if key in value:
                base[key] = copy.deepcopy(value[key])
        ref = value.get("reference")
        if isinstance(ref, dict):
            base["reference"].update({k: copy.deepcopy(v) for k, v in ref.items() if k in base["reference"]})
    if base["type"] not in SUBJECT_TYPES:
        base["type"] = "other"
    base["id"] = str(base["id"] or _id("subject"))
    base["label"] = str(base["label"] or f"Subject {index}")
    base["description"] = str(base["description"] or "")
    base["performance_notes"] = str(base["performance_notes"] or "")
    base["locks"] = [str(x) for x in (base.get("locks") or [])]
    ref = base["reference"]
    if ref["mode"] not in {"none", "asset", "picture_slot"}:
        ref["mode"] = "none"
    try:
        ref["picture_number"] = min(9, max(1, int(ref.get("picture_number") or index)))
    except (TypeError, ValueError):
        ref["picture_number"] = min(9, max(1, index))
    ref["asset_file"] = str(ref.get("asset_file") or "")
    ref["analyze"] = bool(ref.get("analyze", False))
    if ref["mode"] == "picture_slot":
        ref["asset_file"] = ""
        ref["analyze"] = False
    if ref["mode"] == "none":
        ref["asset_file"] = ""
        ref["analyze"] = False
    return base


def _clean_shot(value: Any, index: int, duration: float) -> dict[str, Any]:
    base = empty_shot(index)
    if isinstance(value, dict):
        for key in base:
            if key in value:
                base[key] = copy.deepcopy(value[key])
    base["id"] = str(base["id"] or _id("shot"))
    try:
        base["start_seconds"] = float(base["start_seconds"])
    except (TypeError, ValueError):
        base["start_seconds"] = 0.0
    base["start_seconds"] = min(max(0.0, base["start_seconds"]), max(0.0, duration - 0.001))
    for key in ("framing", "camera_motion", "camera_custom", "amplitude", "speed", "action", "dialogue", "sound"):
        base[key] = str(base.get(key) or "")
    base["subjects"] = [str(x) for x in (base.get("subjects") or [])]
    blocking = []
    for item in base.get("blocking") or []:
        if not isinstance(item, dict):
            continue
        try:
            x = min(1.0, max(0.0, float(item.get("x", 0.4))))
            y = min(1.0, max(0.0, float(item.get("y", 0.25))))
            width = min(1.0, max(0.02, float(item.get("width", 0.2))))
            height = min(1.0, max(0.02, float(item.get("height", 0.5))))
        except (TypeError, ValueError):
            x, y, width, height = 0.4, 0.25, 0.2, 0.5
        if x + width > 1:
            x = max(0.0, 1 - width)
        if y + height > 1:
            y = max(0.0, 1 - height)
        facing = str(item.get("facing") or "unspecified")
        if facing not in {"unspecified", "left", "right", "camera", "away"}:
            facing = "unspecified"
        kind = str(item.get("kind") or "subject")
        if kind not in {"subject", "object", "environment", "note"}:
            kind = "note"
        blocking.append({
            "id": str(item.get("id") or _id("block")),
            "subject_id": str(item.get("subject_id") or ""),
            "kind": kind,
            "label": str(item.get("label") or ""),
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "facing": facing,
            "note": str(item.get("note") or ""),
        })
    base["blocking"] = blocking
    sketch = []
    for stroke in base.get("blocking_sketch") or []:
        if not isinstance(stroke, dict):
            continue
        points = []
        for point in stroke.get("points") or []:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            try:
                px = min(1.0, max(0.0, float(point[0])))
                py = min(1.0, max(0.0, float(point[1])))
            except (TypeError, ValueError):
                continue
            points.append([px, py])
        if len(points) < 2:
            continue
        sketch.append({
            "id": str(stroke.get("id") or _id("stroke")),
            "block_id": str(stroke.get("block_id") or ""),
            "points": points[:400],
        })
    base["blocking_sketch"] = sketch[:200]
    base["locks"] = [str(x) for x in (base.get("locks") or [])]
    return base


def normalize_project(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return new_project()
    base = new_project(
        name=str(value.get("name") or "Untitled Prompt"),
        mode=str(value.get("mode") or "r2v"),
        duration=float(value.get("duration") or 10),
        aspect_ratio=str(value.get("aspect_ratio") or "9:16 (Portrait Widescreen)"),
        concept=str((value.get("scene") or {}).get("concept") or ""),
        model=str(value.get("model") or DEFAULT_MODEL),
    )
    for key in ("id", "created_at", "final_prompt", "validation", "revisions"):
        if key in value:
            base[key] = copy.deepcopy(value[key])
    start = value.get("starting_image")
    if isinstance(start, dict):
        base["starting_image"] = {
            "asset_file": str(start.get("asset_file") or ""),
            "analyze": bool(start.get("analyze", True)),
        }
    scene_in = value.get("scene") if isinstance(value.get("scene"), dict) else {}
    scene = base["scene"]
    for key in ("concept", "environment", "visual_style", "soundscape", "music"):
        scene[key] = str(scene_in.get(key) or "")
    shot_count_mode = str(scene_in.get("shot_count_mode") or "auto")
    scene["shot_count_mode"] = shot_count_mode if shot_count_mode in {"auto", "exact"} else "auto"
    try:
        scene["exact_shot_count"] = min(12, max(1, int(scene_in.get("exact_shot_count") or 1)))
    except (TypeError, ValueError):
        scene["exact_shot_count"] = 1
    scene["locks"] = [str(x) for x in (scene_in.get("locks") or [])]
    scene["subjects"] = [
        _clean_subject(x, i)
        for i, x in enumerate(scene_in.get("subjects") or [], start=1)
    ]
    scene["shots"] = [
        _clean_shot(x, i, base["duration"])
        for i, x in enumerate(scene_in.get("shots") or [], start=1)
    ] or [empty_shot(1)]
    scene["shots"].sort(key=lambda x: x["start_seconds"])
    if scene["shot_count_mode"] == "exact":
        desired = scene["exact_shot_count"]
        scene["shots"] = scene["shots"][:desired]
        while len(scene["shots"]) < desired:
            index = len(scene["shots"]) + 1
            shot = empty_shot(index)
            shot["start_seconds"] = base["duration"] * (index - 1) / desired
            scene["shots"].append(shot)
    scene["shots"][0]["start_seconds"] = 0.0
    base["updated_at"] = _now()
    return base


def snapshot_revision(project: dict[str, Any], label: str) -> dict[str, Any]:
    revision = {
        "id": _id("revision"),
        "created_at": _now(),
        "label": label.strip() or "Saved revision",
        "project": {
            k: copy.deepcopy(v)
            for k, v in project.items()
            if k not in {"revisions", "created_at", "updated_at"}
        },
    }
    project.setdefault("revisions", []).insert(0, revision)
    project["revisions"] = project["revisions"][:50]
    project["updated_at"] = _now()
    return revision


def restore_revision(project: dict[str, Any], revision_id: str) -> dict[str, Any]:
    for revision in project.get("revisions") or []:
        if str(revision.get("id")) == revision_id:
            restored = copy.deepcopy(project)
            for key, value in (revision.get("project") or {}).items():
                restored[key] = copy.deepcopy(value)
            restored["revisions"] = copy.deepcopy(project.get("revisions") or [])
            restored["id"] = project["id"]
            restored["created_at"] = project.get("created_at", _now())
            restored["updated_at"] = _now()
            return normalize_project(restored)
    raise KeyError(revision_id)


def _restore_locked_fields(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(new)
    for key in old.get("locks") or []:
        if key in old:
            out[key] = copy.deepcopy(old[key])
    out["locks"] = copy.deepcopy(old.get("locks") or [])
    return out


def preserve_locks(old_scene: dict[str, Any], new_scene: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(new_scene)
    for key in old_scene.get("locks") or []:
        if key in old_scene:
            out[key] = copy.deepcopy(old_scene[key])
    out["locks"] = copy.deepcopy(old_scene.get("locks") or [])

    old_subjects = {str(x.get("id")): x for x in old_scene.get("subjects") or []}
    subjects = []
    for subject in out.get("subjects") or []:
        old = old_subjects.get(str(subject.get("id")))
        if old:
            subject = _restore_locked_fields(old, subject)
            subject["reference"] = copy.deepcopy(old.get("reference") or subject.get("reference"))
        subjects.append(subject)
    out["subjects"] = subjects

    old_shots = {str(x.get("id")): x for x in old_scene.get("shots") or []}
    shots = []
    for shot in out.get("shots") or []:
        old = old_shots.get(str(shot.get("id")))
        if old:
            shot = _restore_locked_fields(old, shot)
            # Blocking is user-authored director metadata. AI edits may use it
            # as context but never silently replace or erase the board.
            shot["blocking"] = copy.deepcopy(old.get("blocking") or [])
            shot["blocking_sketch"] = copy.deepcopy(old.get("blocking_sketch") or [])
        shots.append(shot)
    out["shots"] = shots
    return out


def planner_system_prompt() -> str:
    return """You are the planning engine for MMH3 Prompt Studio. Build a precise editable scene specification, not a final video-model prompt.

The user's concept is authoritative. Make the plan coherent for the requested duration and aspect ratio. Use the supplied subject IDs and shot IDs when they already exist. Preserve all supplied reference bindings exactly.

Reference rules:
- reference.mode=asset means an actual H3 reference picture will be supplied later. If analyze=true and an attachment is present, you may describe visible appearance. Still preserve the picture_number.
- reference.mode=picture_slot is intentionally opaque. You do not know what the picture looks like. Never invent identity, appearance, wardrobe, age, ethnicity, hair, body, or other visual traits for it. Describe it generically as established by that <Picture N> unless the user explicitly supplied text overrides.
- reference.mode=none is text-only/pure generation.
- Existing text descriptions are user-authored constraints, not suggestions.

Shot rules:
- If shot_count_mode=exact, return exactly exact_shot_count shots. Do not add or remove shots. Preserve existing shot IDs by order whenever possible.
- If shot_count_mode=auto, choose the shot count that best serves the action and duration.
- Shot 1 starts at 0.
- Later shot start times must strictly increase and stay inside the duration.
- Prefer continuity over excessive cuts.
- Camera motion should use the supplied MiniMax-style vocabulary when it fits; use Custom plus camera_custom when it does not.
- Blocking entries are semantic composition constraints using normalized frame coordinates. Treat them as authoritative placement/facing guidance and preserve them when present.
- Dialogue is verbatim user-authored wording when quoted in the concept.
- Keep spatial relationships, object states, wardrobe, and continuity consistent.

Return only the structured scene object required by the schema."""


def edit_system_prompt(scope: str) -> str:
    return f"""You are a surgical editor inside MMH3 Prompt Studio.
Change only the requested {scope} component. The user's edit instruction is authoritative.
Do not rewrite unrelated details for style. Preserve IDs and reference bindings. Preserve locked fields.
For opaque picture-slot references, never invent unseen visual attributes.
Return only the replacement component required by the schema."""


def compiler_system_prompt(mode: str) -> str:
    common = """You are the final compiler for MMH3 Prompt Studio. Convert the supplied structured scene plan into one production-ready MiniMax H3 prompt. The scene plan is authoritative. Do not add plot beats, dialogue, characters, references, brands, or physical traits that are absent from it. Opaque picture-slot references must remain opaque. Return only the final prompt text with no Markdown fence or commentary.

Use playback order. Shot 1 has no timestamp. Later cuts use [Shot N] At MM:SS.mmm and strictly increasing times inside the clip duration. Use camera vocabulary naturally rather than as a tag dump. Synchronize dialogue and event sounds with visible events.

When a shot includes semantic blocking, translate normalized placement into natural composition language such as left/right/center, foreground/midground/background, relative size, and facing. Blocking is authoritative over generic composition guesses. Do not mention coordinates, percentages, UI boards, or "blocking metadata" in the final prompt."""
    if mode == "r2v":
        return common + """

Output exactly these six sections in this order:
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:

Assign project subjects to <Subject 1>, <Subject 2>, etc. in the supplied mapping. Preserve every <Picture N> number in the supplied reference contract and never invent a picture number.
A picture used only to establish a subject belongs inside that subject definition, not as a separate frame anchor.
In retention_analysis include exactly one line for every referenced subject definition, same order. Visual markers are: fully_preserved, partially_preserved, attribute_transfer, weak_reference.
summary begins with an applicable task type such as reference generation or pure generation.
Dialogue is placed in detailed_description. Referenced speakers use <Subject N> (S1), (S2), etc. in order of first vocal event. Spoken words use <d>[English] verbatim words</d>.
overall_soundscape contains ambience/diegetic physical sound without repeating dialogue.
non_diegetic_music contains audience-only score or N/A."""
    if mode == "i2v":
        return common + """

Output exactly these three fields in this order:
integrated_multimodal_description:
overall_soundscape:
non_diegetic_music:

<Picture 1> is the actual video frame at 0.00 seconds. Treat it as authoritative initial state when a starting image is configured. Describe forward temporal progression rather than reinventing the opening frame. Preserve the starting frame's composition and continuity unless the scene plan explicitly changes it."""
    return common + """

Output exactly these three fields in this order:
integrated_multimodal_description:
overall_soundscape:
non_diegetic_music:

Put the audiovisual scene and shot-by-shot progression in integrated_multimodal_description. Put ambience and diegetic physical sound in overall_soundscape. Put audience-only score in non_diegetic_music, or N/A when none is intended."""


def reference_contract(project: dict[str, Any]) -> str:
    lines: list[str] = []
    if project.get("mode") == "i2v" and (project.get("starting_image") or {}).get("asset_file"):
        lines.append("<Picture 1>: configured I2V starting frame at 0.00 seconds")
    for index, subject in enumerate((project.get("scene") or {}).get("subjects") or [], start=1):
        ref = subject.get("reference") or {}
        lines.append(f"{subject.get('id')} => <Subject {index}>")
        if ref.get("mode") in {"asset", "picture_slot"}:
            number = int(ref.get("picture_number") or index)
            if ref.get("mode") == "asset":
                lines.append(
                    f"<Subject {index}> visual identity/reference is established by <Picture {number}>"
                )
            else:
                lines.append(
                    f"<Subject {index}> uses opaque <Picture {number}>; its unseen attributes must not be invented"
                )
    return "\n".join(lines) or "No external reference contract."


def validate_prompt(project: dict[str, Any], prompt: str) -> dict[str, Any]:
    mode = project.get("mode")
    errors: list[str] = []
    warnings: list[str] = []
    text = str(prompt or "").strip()
    if not text:
        return {"valid": False, "errors": ["Compiler returned an empty prompt."], "warnings": []}

    if mode == "r2v":
        required = [
            "subject_definitions:",
            "summary:",
            "retention_analysis:",
            "detailed_description:",
            "overall_soundscape:",
            "non_diegetic_music:",
        ]
    else:
        required = [
            "integrated_multimodal_description:",
            "overall_soundscape:",
            "non_diegetic_music:",
        ]
    positions = [text.find(x) for x in required]
    for label, pos in zip(required, positions):
        if pos < 0:
            errors.append(f"Missing required section: {label}")
    if all(x >= 0 for x in positions) and positions != sorted(positions):
        errors.append("Required sections are out of order.")

    duration = float(project.get("duration") or 0)
    stamps = []
    for mm, ss, ms in re.findall(r"At\s+(\d{2}):(\d{2})\.(\d{3})", text):
        value = int(mm) * 60 + int(ss) + int(ms) / 1000
        stamps.append(value)
    if stamps != sorted(stamps) or len(stamps) != len(set(stamps)):
        errors.append("Shot timestamps must strictly increase.")
    if any(x >= duration for x in stamps):
        errors.append("A shot timestamp is outside the configured duration.")

    if re.search(r"\[Shot\s*1\]\s*At\s+\d", text, flags=re.I):
        errors.append("Shot 1 must not have a timestamp.")

    if mode == "r2v":
        allowed_pictures = {
            int((s.get("reference") or {}).get("picture_number"))
            for s in (project.get("scene") or {}).get("subjects") or []
            if (s.get("reference") or {}).get("mode") in {"asset", "picture_slot"}
        }
        used_pictures = {int(x) for x in re.findall(r"<Picture\s+(\d+)>", text)}
        invented = sorted(used_pictures - allowed_pictures)
        if invented:
            errors.append("Prompt invented undefined picture reference(s): " + ", ".join(map(str, invented)))

        subject_count = len((project.get("scene") or {}).get("subjects") or [])
        used_subjects = {int(x) for x in re.findall(r"<Subject\s+(\d+)>", text)}
        invented_subjects = sorted(x for x in used_subjects if x < 1 or x > subject_count)
        if invented_subjects:
            errors.append("Prompt invented undefined subject reference(s): " + ", ".join(map(str, invented_subjects)))

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def planner_request_schema() -> dict[str, Any]:
    return structured_format("mmh3_prompt_studio_scene", scene_schema())


def edit_request_schema(scope: str) -> dict[str, Any]:
    if scope == "subject":
        value_schema = subject_schema()
    elif scope == "shot":
        value_schema = shot_schema()
    elif scope == "whole":
        value_schema = scene_schema()
    else:
        value_schema = {"type": "string"}
    return structured_format(
        "mmh3_prompt_studio_edit",
        {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "value": value_schema,
            },
            "required": ["summary", "value"],
            "additionalProperties": False,
        },
    )
