from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "services" / "phone-ui" / "server_core.py"


def load_server_module():
    phone_root = str(SERVER_PATH.parent)
    sys.path.insert(0, phone_root)
    try:
        spec = importlib.util.spec_from_file_location("mmh3_phone_server_integration", SERVER_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        try:
            sys.path.remove(phone_root)
        except ValueError:
            pass


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
