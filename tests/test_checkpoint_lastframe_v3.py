from __future__ import annotations

import importlib
import sys
from pathlib import Path


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


def base_graph(mode="i2v", prompt_mode="auto"):
    graph = {
        "10": {"class_type": "UNETLoader", "inputs": {"unet_name": "old.safetensors"}},
        "20": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"first_frame": ["30", 0]}},
        "30": {"class_type": "LoadImage", "inputs": {"image": "start.png"}},
        "50": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": "old-turbo.safetensors", "strength_model": 0.5, "model": ["10", 0]},
            "_meta": {"title": "Generation Profile Turbo LoRA (Balanced default)"},
        },
        "51": {
            "class_type": "MiniMaxH3SigmaShift",
            "inputs": {"model": ["50", 0], "shift_video": 6.0, "shift_audio": 3.0},
        },
        "52": {
            "class_type": "BasicScheduler",
            "inputs": {"model": ["51", 0], "scheduler": "simple", "steps": 8, "denoise": 1.0},
        },
        "53": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "54": {"class_type": "BasicGuider", "inputs": {"model": ["51", 0]}},
        "55": {"class_type": "SamplerCustomAdvanced", "inputs": {"sigmas": ["52", 0]}},
    }
    if prompt_mode == "auto":
        graph["40"] = {"class_type": "OpenRouterNode", "inputs": {"image_1": ["30", 0]}}
    record = {"mode": mode, "prompt_mode": prompt_mode, "seed": 123, "model_family": "fl2v"}
    return graph, record


def test_eros_i2v_patches_unet_last_frame_and_openrouter(monkeypatch):
    wrapper = load_wrapper()
    monkeypatch.setattr(wrapper, "_base_patch_workflow", lambda payload: base_graph("i2v", "auto"))

    graph, record = wrapper.patch_workflow_v3({
        "mode": "i2v",
        "prompt_mode": "auto",
        "base_checkpoint": "eros_beta5_int8",
        "ending_image": "ending.png",
    })

    assert graph["10"]["inputs"]["unet_name"] == wrapper.EROS_BETA5_INT8
    last_ids = [nid for nid, node in graph.items() if node.get("_meta", {}).get("title") == "Load Last Frame"]
    assert len(last_ids) == 1
    last_id = last_ids[0]
    assert graph[last_id]["inputs"]["image"] == "ending.png"
    assert graph["20"]["inputs"]["last_frame"] == [last_id, 0]
    assert graph["40"]["inputs"]["image_2"] == [last_id, 0]
    assert record["base_checkpoint"] == wrapper.CHECKPOINT_EROS
    assert record["base_checkpoint_file"] == wrapper.EROS_BETA5_INT8
    assert record["generation_profile"] == "balanced8"
    expected = wrapper._profile_spec("i2v", "balanced8")["turbo_lora"]
    assert record["turbo_lora"] == expected
    assert graph["50"]["inputs"]["lora_name"] == expected
    assert graph["50"]["inputs"]["strength_model"] == 1.0
    assert graph["51"]["inputs"]["shift_video"] == 6.0
    assert graph["52"]["inputs"]["steps"] == 8
    assert graph["53"]["inputs"]["sampler_name"] == "euler"
    assert record["ending_image"] == "ending.png"


def test_stock_checkpoint_stays_mode_specific(monkeypatch):
    wrapper = load_wrapper()

    def fake_patch(payload):
        graph, record = base_graph(payload["mode"], payload.get("prompt_mode", "custom"))
        record["model_family"] = "ref2v" if payload["mode"] == "r2v" else "fl2v"
        return graph, record

    monkeypatch.setattr(wrapper, "_base_patch_workflow", fake_patch)

    graph, record = wrapper.patch_workflow_v3({"mode": "r2v", "prompt_mode": "custom"})
    assert graph["10"]["inputs"]["unet_name"] == wrapper.STOCK_REF2VA
    assert record["base_checkpoint"] == wrapper.CHECKPOINT_STOCK

    graph, record = wrapper.patch_workflow_v3({"mode": "t2v", "prompt_mode": "custom"})
    assert graph["10"]["inputs"]["unet_name"] == wrapper.STOCK_FL2VA
    assert record["base_checkpoint"] == wrapper.CHECKPOINT_STOCK


def test_end_frame_rejected_outside_i2v(monkeypatch):
    wrapper = load_wrapper()
    monkeypatch.setattr(wrapper, "_base_patch_workflow", lambda payload: base_graph("t2v", "custom"))
    try:
        wrapper.patch_workflow_v3({"mode": "t2v", "prompt_mode": "custom", "ending_image": "end.png"})
    except wrapper.web.HTTPBadRequest as exc:
        assert "only supported for I2V" in exc.text
    else:
        raise AssertionError("expected I2V-only ending-image validation")


def test_fast_fl2v_uses_v12_four_step_recipe(monkeypatch):
    wrapper = load_wrapper()
    monkeypatch.setattr(wrapper, "_base_patch_workflow", lambda payload: base_graph("t2v", "custom"))

    graph, record = wrapper.patch_workflow_v3({
        "mode": "t2v",
        "prompt_mode": "custom",
        "generation_profile": "fast",
    })

    assert record["generation_profile"] == "fast4"
    expected = wrapper._profile_spec("t2v", "fast4")["turbo_lora"]
    assert record["turbo_lora"] == expected
    assert graph["50"]["inputs"]["lora_name"] == expected
    assert graph["50"]["inputs"]["strength_model"] == 1.0
    assert graph["51"]["inputs"]["shift_video"] == 6.0
    assert graph["51"]["inputs"]["shift_audio"] == 3.0
    assert graph["52"]["inputs"]["scheduler"] == "simple"
    assert graph["52"]["inputs"]["steps"] == 4
    assert graph["53"]["inputs"]["sampler_name"] == "euler"


def test_fast_r2v_preserves_legacy_four_step_recipe(monkeypatch):
    wrapper = load_wrapper()
    monkeypatch.setattr(wrapper, "_base_patch_workflow", lambda payload: base_graph("r2v", "custom"))

    graph, record = wrapper.patch_workflow_v3({
        "mode": "r2v",
        "prompt_mode": "custom",
        "generation_profile": "fast",
    })

    assert record["generation_profile"] == "legacy_exact"
    expected = wrapper._profile_spec("r2v", "legacy_exact")["turbo_lora"]
    assert record["turbo_lora"] == expected
    assert graph["50"]["inputs"]["lora_name"] == expected
    assert graph["50"]["inputs"]["strength_model"] == 0.85
    assert graph["53"]["inputs"]["sampler_name"] == "seeds_2"

    beta = next((nid, n) for nid, n in graph.items() if n.get("class_type") == "BetaSamplingScheduler")
    extend = next((nid, n) for nid, n in graph.items() if n.get("class_type") == "ExtendIntermediateSigmas")
    assert beta[1]["inputs"]["steps"] == 4
    assert beta[1]["inputs"]["alpha"] == 0.6
    assert beta[1]["inputs"]["beta"] == 0.6
    assert extend[1]["inputs"]["steps"] == 2
    assert extend[1]["inputs"]["start_at_sigma"] == 0.8
    assert graph["54"]["inputs"]["model"] == ["50", 0]
    assert graph["55"]["inputs"]["sigmas"] == [extend[0], 0]


def test_r2v_default_is_tuned_euler_v01(monkeypatch):
    wrapper = load_wrapper()
    monkeypatch.setattr(wrapper, "_base_patch_workflow", lambda payload: base_graph("r2v", "custom"))

    graph, record = wrapper.patch_workflow_v3({
        "mode": "r2v",
        "prompt_mode": "custom",
    })

    assert record["generation_profile"] == "tuned"
    assert record["turbo_lora"] == wrapper._profile_spec("r2v", "tuned")["turbo_lora"]
    assert graph["53"]["inputs"]["sampler_name"] == "euler"
    assert record["generation_settings"]["schedule_type"] == "beta"


def test_generation_overrides_change_sampler_and_disable_extend(monkeypatch):
    wrapper = load_wrapper()
    monkeypatch.setattr(wrapper, "_base_patch_workflow", lambda payload: base_graph("r2v", "custom"))

    graph, record = wrapper.patch_workflow_v3({
        "mode": "r2v",
        "prompt_mode": "custom",
        "generation_profile": "tuned",
        "generation_settings": {
            "sampler": "euler",
            "schedule_type": "beta",
            "extend_enabled": False,
            "beta_alpha": 0.7,
        },
    })

    beta = next((nid, n) for nid, n in graph.items() if n.get("class_type") == "BetaSamplingScheduler")
    assert beta[1]["inputs"]["alpha"] == 0.7
    assert graph["55"]["inputs"]["sigmas"] == [beta[0], 0]
    assert record["generation_settings"]["extend_enabled"] is False


def test_i2v_ignores_advanced_generation_overrides(monkeypatch):
    wrapper = load_wrapper()
    monkeypatch.setattr(wrapper, "_base_patch_workflow", lambda payload: base_graph("i2v", "custom"))

    graph, record = wrapper.patch_workflow_v3({
        "mode": "i2v",
        "prompt_mode": "custom",
        "generation_profile": "balanced8",
        "generation_settings": {
            "sampler": "seeds_2",
            "schedule_type": "beta",
            "steps": 4,
            "strength": 0.5,
        },
    })

    assert record["generation_settings"]["sampler"] == "euler"
    assert record["generation_settings"]["schedule_type"] == "basic"
    assert record["generation_settings"]["steps"] == 8
    assert record["generation_settings"]["strength"] == 1.0
    assert graph["150"]["inputs"]["sampler_name"] == "euler"
