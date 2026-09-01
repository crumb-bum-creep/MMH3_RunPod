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
