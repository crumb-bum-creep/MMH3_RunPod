from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PHONE_ROOT = ROOT / "services" / "phone-ui"


def load_wrapper():
    sys.path.insert(0, str(PHONE_ROOT))
    try:
        sys.modules.pop("server", None)
        return importlib.import_module("server")
    finally:
        try:
            sys.path.remove(str(PHONE_ROOT))
        except ValueError:
            pass


def test_output_naming_defaults_to_existing_scheme(monkeypatch):
    wrapper = load_wrapper()
    monkeypatch.setattr(
        wrapper,
        "load_controls",
        lambda: {"memory_protection": True, "output_naming_template": "MMH3/{mode}"},
    )
    prefix, template = wrapper._output_prefix(
        {},
        {"mode": "r2v", "prompt_mode": "auto", "seed": 123},
        "tuned",
        "stock_convrot_int8",
    )
    assert prefix == "MMH3/R2V"
    assert template == "MMH3/{mode}"


def test_output_naming_supports_tokens_and_sanitizes(monkeypatch):
    wrapper = load_wrapper()
    monkeypatch.setattr(
        wrapper,
        "load_controls",
        lambda: {
            "memory_protection": True,
            "output_naming_template": "Tests/{mode}/{profile}/{seed}",
        },
    )
    prefix, _ = wrapper._output_prefix(
        {},
        {"mode": "i2v", "prompt_mode": "custom", "seed": 44},
        "fast",
        "stock_convrot_int8",
    )
    assert prefix == "Tests/I2V/fast/44"


def test_reuse_exact_static_contract():
    app = (PHONE_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    library = (PHONE_ROOT / "static" / "library-v2.js").read_text(encoding="utf-8")
    assert "function reuseExactSnapshot" in app
    assert 'prompt_mode:"custom"' in app
    assert "actual_prompt||v.prompt||v.prompt_idea" in app
    assert "randomize_seed:false" in app
    assert 'generation_profile:v.generation_profile||(v.mode==="r2v"?"legacy_exact":"balanced8")' in app
    assert "historicalFallback=!v.generation_settings" in app
    assert "Reuse Exact" in library
    assert "reuseExactSnapshot(m)" in library
    features = (PHONE_ROOT / "static" / "features-v3.js").read_text(encoding="utf-8")
    assert "state.uiProfiles[profileKey()]=captureDraft()" in features
    assert "if(preferredProfile||preferredSettings)" in features


def test_memory_protection_runtime_control_is_wired():
    wrapper = (PHONE_ROOT / "server.py").read_text(encoding="utf-8")
    guard = (ROOT / "runtime" / "mmh3" / "memory_guard.py").read_text(encoding="utf-8")
    ui = (PHONE_ROOT / "static" / "features-v3.js").read_text(encoding="utf-8")
    assert 'app.router.add_get("/api/runtime-controls"' in wrapper
    assert 'app.router.add_put("/api/runtime-controls"' in wrapper
    assert "if memory_protection and active_families" in wrapper
    assert "memory_protection_enabled" in guard
    assert "memoryProtectionToggle" in ui
    assert "disableDynamicVramToggle" in ui
    assert 'app.router.add_get("/api/comfy-runtime"' in wrapper
    assert 'app.router.add_put("/api/comfy-runtime"' in wrapper


def test_phone_ui_has_one_canonical_entrypoint():
    phone = PHONE_ROOT
    entrypoint = (ROOT / "runtime" / "entrypoint.sh").read_text(encoding="utf-8")
    assert (phone / "server.py").is_file()
    assert (phone / "server_core.py").is_file()
    assert not (phone / "server_v2.py").exists()
    assert "server_legacy.py" not in entrypoint
    assert "server_v2.py" not in entrypoint
    server = (phone / "server.py").read_text(encoding="utf-8")
    assert "base.api_generate = api_generate" not in server
    assert "base.patch_workflow = patch_workflow_v3" not in server


def test_comfy_runtime_setting_requests_idle_restart():
    wrapper = (PHONE_ROOT / "server.py").read_text(encoding="utf-8")
    supervisor = (ROOT / "runtime" / "mmh3" / "supervisor.py").read_text(encoding="utf-8")
    assert 'STATE_ROOT / "comfy_restart.request"' in wrapper
    assert 'restart_request = STATE_ROOT / "comfy_restart.request"' in supervisor
    assert "pending_restart = rescan_request.exists() or restart_request.exists()" in supervisor
    assert "pending_restart and comfy.queue_idle()" in supervisor


class JsonRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def test_comfy_runtime_put_persists_setting_and_requests_restart(monkeypatch, tmp_path):
    wrapper = load_wrapper()
    config_root = tmp_path / "config"
    state_root = tmp_path / "state"
    config_root.mkdir()
    state_root.mkdir()
    runtime = config_root / "runtime.yaml"
    runtime.write_text(
        yaml.safe_dump({"version": 2, "comfy": {"disable_dynamic_vram": False, "use_sage_attention": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(wrapper.base, "CONFIG_ROOT", config_root)
    monkeypatch.setattr(wrapper.base, "STATE_ROOT", state_root)

    response = asyncio.run(
        wrapper.api_comfy_runtime_put(JsonRequest({"disable_dynamic_vram": True}))
    )
    body = json.loads(response.text)
    saved = yaml.safe_load(runtime.read_text())

    assert body["disable_dynamic_vram"] is True
    assert body["restart_requested"] is True
    assert saved["comfy"]["disable_dynamic_vram"] is True
    request = state_root / "comfy_restart.request"
    assert request.is_file()
    assert json.loads(request.read_text())["reason"] == "dynamic_vram_setting_changed"


def test_advanced_tuning_is_r2v_only():
    ui = (PHONE_ROOT / "static" / "features-v3.js").read_text(encoding="utf-8")
    server = (PHONE_ROOT / "server.py").read_text(encoding="utf-8")
    assert 'if(tuning)tuning.hidden=state.mode!=="r2v"' in ui
    assert 'v.generation_settings=state.mode==="r2v"?readGenerationSettings():null' in ui
    assert 'payload.get("generation_settings") if mode == "r2v" else None' in server
