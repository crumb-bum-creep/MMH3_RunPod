from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "services" / "phone-ui" / "server.py"


def load_server_module():
    spec = importlib.util.spec_from_file_location("mmh3_phone_server_integration", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class JsonRequest:
    def __init__(self, body=None, *, query=None, match_info=None):
        self._body = body or {}
        self.query = query or {}
        self.match_info = match_info or {}

    async def json(self):
        return self._body


def response_json(response):
    return json.loads(response.text)


def test_ui_state_round_trip_persists_all_six_profiles(tmp_path):
    server = load_server_module()
    server.UI_STATE_FILE = tmp_path / "ui_state.json"

    profiles = {}
    for mode in ("t2v", "i2v", "r2v"):
        for prompt_mode in ("auto", "custom"):
            key = f"{mode}:{prompt_mode}"
            profiles[key] = {
                "prompt": f"{key} prompt",
                "duration": 10,
                "megapixels": 0.4,
                "refs": [{"kind": "image", "file": f"{key}.png"}] if mode == "r2v" else [],
                "loras": [{"filename": "example.safetensors", "strength": 0.75}],
            }

    put = asyncio.run(
        server.api_ui_state_put(
            JsonRequest(
                {
                    "active": {"mode": "r2v", "prompt_mode": "custom"},
                    "profiles": profiles,
                }
            )
        )
    )
    put_data = response_json(put)
    assert put_data["ok"] is True
    assert put_data["updated_at"] > 0
    assert server.UI_STATE_FILE.is_file()

    got = asyncio.run(server.api_ui_state_get(JsonRequest()))
    data = response_json(got)
    assert data["active"] == {"mode": "r2v", "prompt_mode": "custom"}
    assert set(data["profiles"]) == set(profiles)
    assert data["profiles"]["r2v:auto"]["prompt"] == "r2v:auto prompt"
    assert data["profiles"]["r2v:custom"]["prompt"] == "r2v:custom prompt"
    assert data["profiles"]["r2v:auto"]["prompt"] != data["profiles"]["r2v:custom"]["prompt"]


def test_asset_nickname_and_thumbnail_round_trip(tmp_path):
    server = load_server_module()
    server.INPUT_DIR = tmp_path / "input"
    server.INPUT_DIR.mkdir(parents=True)
    server.ASSET_META_FILE = tmp_path / "assets.json"
    server.THUMB_DIR = tmp_path / "thumbs"

    image_path = server.INPUT_DIR / "reference_abc123.jpg"
    Image.new("RGB", (640, 360), (32, 64, 96)).save(image_path, "JPEG")

    put = asyncio.run(
        server.api_asset_meta_put(
            JsonRequest({"file": image_path.name, "nickname": "Nicole Reference"})
        )
    )
    assert response_json(put)["asset"]["display_name"] == "Nicole Reference"

    listing = asyncio.run(
        server.api_inputs(JsonRequest(query={"kind": "image", "sort": "alpha"}))
    )
    item = response_json(listing)["items"][0]
    assert item["file"] == image_path.name
    assert item["nickname"] == "Nicole Reference"
    assert item["display_name"] == "Nicole Reference"
    assert item["kind"] == "image"
    assert item["thumb"].endswith(image_path.name)

    thumb_response = asyncio.run(
        server.serve_input_thumb(
            JsonRequest(match_info={"path": image_path.name})
        )
    )
    assert thumb_response.status == 200
    thumbs = list(server.THUMB_DIR.glob("*.jpg"))
    assert len(thumbs) == 1
    with Image.open(thumbs[0]) as thumb:
        assert thumb.width <= 180
        assert thumb.height <= 180

    # Clearing a nickname keeps the asset itself and falls back to the filename.
    asyncio.run(
        server.api_asset_meta_put(
            JsonRequest({"file": image_path.name, "nickname": ""})
        )
    )
    listing2 = asyncio.run(
        server.api_inputs(JsonRequest(query={"kind": "image", "sort": "alpha"}))
    )
    item2 = response_json(listing2)["items"][0]
    assert item2["nickname"] == ""
    assert item2["display_name"] == image_path.name


def test_ui_state_rejects_unknown_profile_keys(tmp_path):
    server = load_server_module()
    server.UI_STATE_FILE = tmp_path / "ui_state.json"

    put = asyncio.run(
        server.api_ui_state_put(
            JsonRequest(
                {
                    "active": {"mode": "t2v", "prompt_mode": "auto"},
                    "profiles": {
                        "t2v:auto": {"prompt": "keep"},
                        "bogus:profile": {"prompt": "drop"},
                    },
                }
            )
        )
    )
    data = response_json(put)
    assert "t2v:auto" in data["profiles"]
    assert "bogus:profile" not in data["profiles"]


def test_prompt_studio_planner_merge_preserves_ids_references_and_blocking():
    server = load_server_module()
    old = server.prompt_studio.new_project(mode="r2v", duration=12)["scene"]
    old["shot_count_mode"] = "exact"
    old["exact_shot_count"] = 2

    subject = server.prompt_studio.empty_subject(1)
    subject["id"] = "subject_stable"
    subject["reference"] = {
        "mode": "picture_slot",
        "picture_number": 2,
        "asset_file": "",
        "analyze": False,
    }
    old["subjects"] = [subject]

    first = server.prompt_studio.empty_shot(1)
    first["id"] = "shot_stable_1"
    first["blocking"] = [{
        "id": "block_stable",
        "subject_id": "subject_stable",
        "kind": "subject",
        "label": "Subject",
        "x": 0.1,
        "y": 0.2,
        "width": 0.2,
        "height": 0.6,
        "facing": "right",
        "note": "",
    }]
    second = server.prompt_studio.empty_shot(2)
    second["id"] = "shot_stable_2"
    old["shots"] = [first, second]

    planned = {
        **old,
        "subjects": [{**subject, "id": "model_changed_subject", "description": "enriched"}],
        "shots": [
            {**first, "id": "model_changed_shot_1", "action": "new action", "blocking": []},
            {**second, "id": "model_changed_shot_2", "action": "second action"},
            server.prompt_studio.empty_shot(3),
        ],
        "shot_count_mode": "auto",
        "exact_shot_count": 3,
    }

    merged = server._studio_merge_planner_scene(old, planned)
    assert merged["shot_count_mode"] == "exact"
    assert merged["exact_shot_count"] == 2
    assert len(merged["shots"]) == 2
    assert merged["subjects"][0]["id"] == "subject_stable"
    assert merged["subjects"][0]["reference"]["picture_number"] == 2
    assert merged["shots"][0]["id"] == "shot_stable_1"
    assert merged["shots"][0]["blocking"][0]["id"] == "block_stable"
    assert merged["shots"][0]["action"] == "new action"


def test_prompt_studio_llm_scene_strips_raw_sketch_but_keeps_semantic_blocking():
    server = load_server_module()
    scene = server.prompt_studio.new_project()["scene"]
    scene["shots"][0]["blocking"] = [{
        "id": "block_1",
        "subject_id": "",
        "kind": "note",
        "label": "foreground mark",
        "x": 0.1,
        "y": 0.2,
        "width": 0.3,
        "height": 0.4,
        "facing": "unspecified",
        "note": "",
    }]
    scene["shots"][0]["blocking_sketch"] = [{
        "id": "stroke_1",
        "block_id": "block_1",
        "points": [[0.1, 0.2], [0.3, 0.4]],
    }]

    llm_scene = server._studio_llm_scene(scene)
    assert "blocking_sketch" not in llm_scene["shots"][0]
    assert llm_scene["shots"][0]["blocking"][0]["id"] == "block_1"
    assert "blocking_sketch" in scene["shots"][0]


def test_prompt_studio_preflight_endpoint_uses_persistent_asset_inventory(tmp_path):
    server = load_server_module()
    server.PROMPT_PROJECT_FILE = tmp_path / "projects.json"
    server.INPUT_DIR = tmp_path / "input"
    server.INPUT_DIR.mkdir(parents=True)

    project = server.prompt_studio.new_project(mode="r2v")
    subject = server.prompt_studio.empty_subject(1)
    subject["reference"] = {
        "mode": "asset",
        "picture_number": 1,
        "asset_file": "hero.png",
        "analyze": True,
    }
    project["scene"]["subjects"] = [subject]
    server._save_json(server.PROMPT_PROJECT_FILE, {project["id"]: project})

    missing = asyncio.run(
        server.api_studio_preflight(
            JsonRequest(match_info={"project_id": project["id"]})
        )
    )
    assert response_json(missing)["preflight"]["valid"] is False

    Image.new("RGB", (64, 64), (1, 2, 3)).save(server.INPUT_DIR / "hero.png")
    present = asyncio.run(
        server.api_studio_preflight(
            JsonRequest(match_info={"project_id": project["id"]})
        )
    )
    assert response_json(present)["preflight"]["valid"] is True


def test_prompt_studio_model_check_uses_project_model_without_comfy(tmp_path):
    server = load_server_module()
    server.PROMPT_PROJECT_FILE = tmp_path / "projects.json"
    project = server.prompt_studio.new_project(model="google/gemini-3-flash-preview")
    server._save_json(server.PROMPT_PROJECT_FILE, {project["id"]: project})

    calls = []

    async def fake_openrouter(**kwargs):
        calls.append(kwargs)
        return "MMH3_STUDIO_OK"

    server._studio_openrouter = fake_openrouter
    response = asyncio.run(
        server.api_studio_model_check(
            JsonRequest(match_info={"project_id": project["id"]})
        )
    )
    data = response_json(response)
    assert data["ok"] is True
    assert data["model"] == "google/gemini-3-flash-preview"
    assert calls[0]["model"] == "google/gemini-3-flash-preview"
    assert "image_parts" not in calls[0]


def test_patch_workflow_sets_sampler_and_records_lora_weights(tmp_path):
    server = load_server_module()
    server.WORKFLOW_DIR = ROOT / "workflows" / "api"
    graph, record = server.patch_workflow({
        "mode": "t2v",
        "prompt_mode": "custom",
        "prompt": "test",
        "aspect_ratio": "9:16 (Portrait Widescreen)",
        "megapixels": 0.7,
        "duration": 5,
        "randomize_seed": False,
        "seed": 42,
        "sampler_name": "euler_ancestral",
        "loras": [
            {"filename": "character.safetensors", "nickname": "Character", "strength": 0.75}
        ],
    })
    sampler = server.find_nodes(graph, "KSamplerSelect")[0][1]
    assert sampler["inputs"]["sampler_name"] == "euler_ancestral"
    assert record["sampler_name"] == "euler_ancestral"
    assert record["loras"][0]["strength"] == 0.75


def test_phone_r2v_overrides_comfy_asset_selector_link_with_payload_refs():
    server = load_server_module()
    server.WORKFLOW_DIR = ROOT / "workflows" / "api"
    graph, record = server.patch_workflow({
        "mode": "r2v",
        "prompt_mode": "custom",
        "prompt": "test",
        "duration": 5,
        "randomize_seed": False,
        "seed": 1,
        "sampler_name": "seeds_2",
        "refs": [{"kind": "image", "file": "hero.png"}],
    })
    pack = server.find_nodes(graph, "MiniMaxH3ReferencePack")[0][1]
    assert isinstance(pack["inputs"]["references_json"], str)
    parsed = json.loads(pack["inputs"]["references_json"])
    assert parsed["references"][0]["file"] == "hero.png"
