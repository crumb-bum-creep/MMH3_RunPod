from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PHONE_ROOT = ROOT / "services" / "phone-ui"


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


def base_graph(mode="i2v", prompt_mode="auto"):
    graph = {
        "10": {"class_type": "UNETLoader", "inputs": {"unet_name": "old.safetensors"}},
        "20": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"first_frame": ["30", 0]}},
        "30": {"class_type": "LoadImage", "inputs": {"image": "start.png"}},
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
