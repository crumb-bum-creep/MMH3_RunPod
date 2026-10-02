from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
PHONE_ROOT = ROOT / "services" / "phone-ui"
STATIC = PHONE_ROOT / "static"


def load_wrapper():
    sys.path.insert(0, str(PHONE_ROOT))
    try:
        sys.modules.pop("server_v2", None)
        return importlib.import_module("server_v2")
    finally:
        try:
            sys.path.remove(str(PHONE_ROOT))
        except ValueError:
            pass


class JsonRequest:
    def __init__(self, body=None, match_info=None):
        self._body = body or {}
        self.match_info = match_info or {}

    async def json(self):
        return self._body


def _json(response):
    return json.loads(response.text)


# ---------- LoRA install-on-demand ----------

def _lora_env(tmp_path, monkeypatch, entries):
    from mmh3 import loras

    comfy = tmp_path / "ComfyUI"
    data = tmp_path / "data"
    (comfy / "models" / "loras").mkdir(parents=True)
    data.mkdir()
    monkeypatch.setattr(loras, "COMFY_PERSIST", comfy)
    monkeypatch.setattr(loras, "DATA_ROOT", data)
    for name in ("MMH3_LORA_VERSION_IDS", "LORAS_IDS_TO_DOWNLOAD", "CIVITAI_LORAS"):
        monkeypatch.delenv(name, raising=False)
    cfg = tmp_path / "loras.yaml"
    cfg.write_text(yaml.safe_dump({"loras": entries}), encoding="utf-8")
    return loras, comfy / "models" / "loras", cfg


def test_catalog_loras_are_not_downloaded_at_startup(tmp_path, monkeypatch):
    loras, root, cfg = _lora_env(tmp_path, monkeypatch, [
        {"version_id": 1, "enabled": True, "nickname": "Installed", "filename": "a.safetensors"},
        {"version_id": 2, "enabled": True, "nickname": "Missing", "filename": "b.safetensors", "tags": ["Motion"]},
    ])
    (root / "a.safetensors").write_bytes(b"\0" * (1024 * 1024 + 1))

    def no_network(*args, **kwargs):
        raise AssertionError("startup sync must not contact CivitAI")

    monkeypatch.setattr(requests.Session, "get", no_network)
    catalog = loras.sync_loras(cfg)
    by_id = {x["version_id"]: x for x in catalog["managed"]}
    assert by_id[1]["status"] == "ready"
    assert by_id[2]["status"] == "available"
    assert by_id[2]["nickname"] == "Missing"
    assert by_id[2]["tags"] == ["Motion"]


def test_explicit_install_and_auto_download_reach_civitai(tmp_path, monkeypatch):
    loras, _, cfg = _lora_env(tmp_path, monkeypatch, [
        {"version_id": 3, "enabled": True, "nickname": "Requested", "filename": "c.safetensors"},
        {"version_id": 4, "enabled": True, "nickname": "Auto", "filename": "d.safetensors", "auto_download": True},
        {"version_id": 5, "enabled": True, "nickname": "Left alone", "filename": "e.safetensors"},
    ])
    called: list[str] = []

    def offline(self, url, *args, **kwargs):
        called.append(url)
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests.Session, "get", offline)
    catalog = loras.sync_loras(cfg, install_ids=[3])
    by_id = {x["version_id"]: x for x in catalog["managed"]}
    assert sorted(u.rsplit("/", 1)[-1] for u in called) == ["3", "4"]
    # A failed install keeps its catalog metadata so the UI can offer a retry.
    assert by_id[3]["status"] == "error"
    assert by_id[3]["nickname"] == "Requested"
    assert "ConnectionError" in by_id[3]["error"]
    assert by_id[5]["status"] == "available"


# ---------- system prompt upgrade ----------

def test_unedited_prompts_upgrade_and_custom_prompts_survive(tmp_path, monkeypatch):
    from mmh3 import bootstrap

    image = tmp_path / "image"
    config = tmp_path / "config"
    (image / "config").mkdir(parents=True)
    config.mkdir()
    (image / "config" / "system_prompts.yaml").write_text(
        yaml.safe_dump({"prompts": {"t2v_auto": "NEW T2V", "i2v_auto": "NEW I2V", "r2v_auto": "NEW R2V"}}),
        encoding="utf-8",
    )
    (config / "system_prompts.yaml").write_text(
        yaml.safe_dump({"prompts": {"t2v_auto": "old shipped t2v\n", "i2v_auto": "my own i2v"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bootstrap, "IMAGE_ROOT", image)
    monkeypatch.setattr(bootstrap, "CONFIG_ROOT", config)
    monkeypatch.setattr(bootstrap, "SHIPPED_PROMPT_SHA256", {
        "t2v_auto": {hashlib.sha256(b"old shipped t2v").hexdigest()},
        "i2v_auto": set(),
        "r2v_auto": set(),
    })

    upgraded = bootstrap.upgrade_default_prompts()
    prompts = yaml.safe_load((config / "system_prompts.yaml").read_text())["prompts"]
    assert sorted(upgraded) == ["r2v_auto", "t2v_auto"]
    assert prompts == {"t2v_auto": "NEW T2V", "i2v_auto": "my own i2v", "r2v_auto": "NEW R2V"}
    assert list(config.glob("system_prompts.yaml.bak-*"))
    assert bootstrap.upgrade_default_prompts() == []


def test_previous_shipped_prompts_are_fingerprinted():
    from mmh3 import bootstrap

    assert set(bootstrap.SHIPPED_PROMPT_SHA256) == {"t2v_auto", "i2v_auto", "r2v_auto"}
    current = yaml.safe_load((ROOT / "config" / "system_prompts.yaml").read_text())["prompts"]
    for key, text in current.items():
        # The current default must not be listed as "old", or edits would loop.
        assert bootstrap._prompt_sha(text) not in bootstrap.SHIPPED_PROMPT_SHA256[key]


def test_system_prompts_follow_minimax_guides():
    prompts = yaml.safe_load((ROOT / "config" / "system_prompts.yaml").read_text())["prompts"]
    t2v, i2v, r2v = prompts["t2v_auto"], prompts["i2v_auto"], prompts["r2v_auto"]
    for text in (t2v, i2v):
        for field in ("integrated_multimodal_description:", "overall_soundscape:", "non_diegetic_music:"):
            assert field in text
    assert "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced." in i2v
    assert "How the reference pictures align with the target video" in i2v
    for section in ("subject_definitions:", "summary:", "retention_analysis:", "detailed_description:"):
        assert section in r2v
    for marker in ("fully_preserved", "attribute_transfer", "partially_copy", "keyframe completion", "audio reuse"):
        assert marker in r2v
    for text in (t2v, i2v, r2v):
        assert "<d>" in text and "(S1)" in text


# ---------- server_v2 ----------

def test_seen_state_marks_new_videos_and_survives_library_put(tmp_path):
    wrapper = load_wrapper()
    wrapper.LIBRARY_FILE = tmp_path / "output_library.json"

    first = _json(asyncio.run(wrapper.api_output_library_get(JsonRequest())))
    assert first["seen_baseline"] > 0 and first["seen"] == {}

    asyncio.run(wrapper.api_output_library_seen(JsonRequest({"files": ["MMH3/a-audio.mp4", "../evil"]})))
    put = _json(asyncio.run(wrapper.api_output_library_put(JsonRequest({"groups": ["Keep"], "videos": {}}))))
    assert list(put["seen"]) == ["MMH3/a-audio.mp4"]
    assert put["groups"] == ["Keep"]

    cleared = _json(asyncio.run(wrapper.api_output_library_seen(JsonRequest({"all": True}))))
    assert cleared["seen"] == {} and cleared["seen_baseline"] >= first["seen_baseline"]


def test_fl2va_note_uses_frame_rounded_duration():
    wrapper = load_wrapper()
    assert f"{wrapper._effective_seconds(5):.2f}" == "5.17"
    assert f"{wrapper._effective_seconds(8):.2f}" == "8.00"


def test_prompt_edit_unwraps_fenced_reply():
    wrapper = load_wrapper()
    data = {"choices": [{"message": {"content": "```text\nintegrated_multimodal_description: x\n```"}}]}
    assert wrapper._openrouter_content(data) == "integrated_multimodal_description: x"


def test_v4_assets_are_served_and_wired():
    server = (PHONE_ROOT / "server_v2.py").read_text()
    js = (STATIC / "features-v4.js").read_text()
    lib = (STATIC / "library-v2.js").read_text()
    assert '("features-v4.js", 1)' in server and '("features-v4.css", 1)' in server
    assert 'app.router.add_post("/api/prompt/edit", api_prompt_edit)' in server
    assert 'app.router.add_post("/api/loras/install/{vid}", api_install_lora)' in server
    assert 'app.router.add_post("/api/output-library/seen", api_output_library_seen)' in server
    assert 'id="promptEditInput"' in js and "/api/loras/install/" in js
    assert "output-name-v2" in lib and "output-new-v2" in lib
    assert "stepDetail(-1)" in lib and "stepDetail(1)" in lib
    assert 'id="outputRemixV2"' in lib
