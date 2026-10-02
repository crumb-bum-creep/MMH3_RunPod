"""Graph builder: shape, recipe values, references, and parity with ComfyUI helpers."""
from __future__ import annotations

import json
import math

import pytest

from studio import graph, recipes

FILES = {
    "unet": "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "text_encoder": "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
    "video_vae": "vae/minimax_h3_video_vae_fp16.safetensors",
    "audio_vae": "vae/minimax_h3_audio_vae_fp32.safetensors",
    "turbo": "loras/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",
    "preview_vae": "vae_approx/taeh3.safetensors",
}
PROMPT = "integrated_multimodal_description: [Shot 1] x.\n\noverall_soundscape: y.\n\nnon_diegetic_music: N/A"


def job(mode="t2v", recipe_id=None, **kw):
    base = dict(mode=mode, prompt=PROMPT, aspect="9:16", megapixels=0.7, duration=5, seed=42,
                recipe=recipes.resolve(mode, recipe_id), loras=[])
    base.update(kw)
    return base


def by_class(g, ct):
    return [(k, n) for k, n in g.items() if n["class_type"] == ct]


def one(g, ct):
    found = by_class(g, ct)
    assert len(found) == 1, f"{ct}: {len(found)}"
    return found[0]


def resolution_selector(aspect, mp, multiple=32):
    """ComfyUI's ResolutionSelector.execute, copied verbatim (Python round)."""
    w_ratio, h_ratio = graph.ASPECTS[aspect]
    total = mp * 1024 * 1024
    scale = math.sqrt(total / (w_ratio * h_ratio))
    return round(w_ratio * scale / multiple) * multiple, round(h_ratio * scale / multiple) * multiple


@pytest.mark.parametrize("aspect", list(graph.ASPECTS))
@pytest.mark.parametrize("mp", [0.25, 0.5, 0.7, 0.8, 0.9, 1.2])
def test_resolution_matches_comfy(aspect, mp):
    assert graph.resolution(aspect, mp) == resolution_selector(aspect, mp)


@pytest.mark.parametrize("sec,frames", [(1, 39), (4, 107), (5, 124), (10, 243), (15, 362)])
def test_frame_count_is_17n_plus_5(sec, frames):
    n = graph.frame_count(sec)
    assert n == frames and (n - 5) % 17 == 0


def test_balanced_t2v_graph():
    g = graph.build(job(), FILES)
    _, unet = one(g, "UNETLoader")
    assert unet["inputs"]["unet_name"] == "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    _, turbo = one(g, "LoraLoaderModelOnly")
    assert turbo["inputs"]["strength_model"] == 1.0
    _, shift = one(g, "MiniMaxH3SigmaShift")
    assert (shift["inputs"]["shift_video"], shift["inputs"]["shift_audio"]) == (6.0, 3.0)
    assert not by_class(g, "ExtendIntermediateSigmas")
    _, sched = one(g, "BasicScheduler")
    assert sched["inputs"]["steps"] == 8 and sched["inputs"]["scheduler"] == "simple"
    _, cond = one(g, "MiniMaxH3ImageToVideo")
    assert (cond["inputs"]["width"], cond["inputs"]["height"], cond["inputs"]["length"]) == (640, 1152, 124)
    assert "first_frame" not in cond["inputs"]
    _, save = one(g, "VHS_VideoCombine")
    assert save["inputs"]["audio"][0] == one(g, "VAEDecodeAudio")[0]


def test_model_chain_order():
    """UNET -> user LoRAs -> turbo -> shift -> preview; guider and scheduler read the end of the chain."""
    g = graph.build(job(loras=[{"file": "a.safetensors", "strength": 0.6}, {"file": "b.safetensors", "strength": 0}]), FILES)
    loras = by_class(g, "LoraLoaderModelOnly")
    assert [n["inputs"]["lora_name"] for _, n in loras] == ["a.safetensors", "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"]
    unet_id = one(g, "UNETLoader")[0]
    assert loras[0][1]["inputs"]["model"] == [unet_id, 0]
    assert loras[1][1]["inputs"]["model"] == [loras[0][0], 0]
    shift_id, shift = one(g, "MiniMaxH3SigmaShift")
    assert shift["inputs"]["model"] == [loras[1][0], 0]
    prev_id, prev = one(g, "ModelPreviewOverrideKJ")
    assert prev["inputs"]["model"] == [shift_id, 0]
    assert one(g, "BasicGuider")[1]["inputs"]["model"] == [prev_id, 0]
    assert one(g, "BasicScheduler")[1]["inputs"]["model"] == [prev_id, 0]


def test_upstream_recipe_has_no_shift_and_extends():
    g = graph.build(job(recipe_id="upstream"), FILES)
    assert not by_class(g, "MiniMaxH3SigmaShift")
    ext_id, ext = one(g, "ExtendIntermediateSigmas")
    assert ext["inputs"]["start_at_sigma"] == 0.8 and ext["inputs"]["steps"] == 2
    assert one(g, "SamplerCustomAdvanced")[1]["inputs"]["sigmas"] == [ext_id, 0]
    assert one(g, "LoraLoaderModelOnly")[1]["inputs"]["strength_model"] == 0.8


def test_overrides_change_values_only():
    r = recipes.resolve("t2v", "balanced", {"steps": 10, "strength": 0.9, "shift": [8, 3], "sampler": "res_multistep"})
    g = graph.build(job(recipe=r), FILES)
    assert one(g, "BasicScheduler")[1]["inputs"]["steps"] == 10
    assert one(g, "KSamplerSelect")[1]["inputs"]["sampler_name"] == "res_multistep"
    assert one(g, "MiniMaxH3SigmaShift")[1]["inputs"]["shift_video"] == 8.0
    assert r["overridden"] == ["sampler", "shift", "steps", "strength"]
    off = recipes.resolve("t2v", "balanced", {"shift": "off"})
    assert not by_class(graph.build(job(recipe=off), FILES), "MiniMaxH3SigmaShift")


def test_i2v_frames():
    g = graph.build(job("i2v", start_image="mmh3/a.png", end_image="mmh3/b.png"), FILES)
    loads = {n["inputs"]["image"]: nid for nid, n in by_class(g, "LoadImage")}
    cond = one(g, "MiniMaxH3ImageToVideo")[1]["inputs"]
    assert cond["first_frame"] == [loads["mmh3/a.png"], 0] and cond["last_frame"] == [loads["mmh3/b.png"], 0]
    with pytest.raises(graph.GraphError):
        graph.build(job("i2v"), FILES)


def test_i2v_end_frame_only():
    """L2V: the node accepts last_frame without first_frame."""
    g = graph.build(job("i2v", end_image="mmh3/b.png"), FILES)
    (load_id, load), = by_class(g, "LoadImage")
    cond = one(g, "MiniMaxH3ImageToVideo")[1]["inputs"]
    assert load["inputs"]["image"] == "mmh3/b.png" and load["_meta"]["title"] == "End frame"
    assert cond["last_frame"] == [load_id, 0] and "first_frame" not in cond


def test_r2v_wiring():
    refs = [{"kind": "image", "file": "a.png"}, {"kind": "video", "file": "v.mp4"}, {"kind": "audio", "file": "s.wav"}]
    g = graph.build(job("r2v", refs=refs), {**FILES, "unet": "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"})
    pack_id, pack = one(g, "MiniMaxH3ReferencePack")
    assert pack["inputs"]["prompt_provider"] == "none" and "openrouter_api_key" not in pack["inputs"]
    assert json.loads(pack["inputs"]["references_json"])["references"][1] == {"kind": "video", "file": "v.mp4", "use_soundtrack": True}
    ref = one(g, "MiniMaxH3ReferenceToVideo")[1]["inputs"]
    assert ref["prompt"] == PROMPT and ref["ref_image_size"] == "max"
    assert ref["ref_images.ref_image_0"] == [pack_id, 0]
    assert ref["ref_videos.ref_video_2"] == [pack_id, 11]
    assert ref["ref_video_audios.ref_video_audio_0"] == [pack_id, 12]
    assert ref["ref_audios.ref_audio_2"] == [pack_id, 17]
    assert one(g, "MiniMaxH3SigmaShift")[1]["inputs"]["shift_video"] == 12.0


def test_no_secrets_in_graphs():
    for mode in ("t2v", "i2v", "r2v"):
        g = graph.build(job(mode, start_image="a.png", refs=[{"kind": "image", "file": "a.png"}]), FILES)
        text = json.dumps(g).lower()
        assert "api_key" not in text and "sk-or" not in text


def test_reference_limits():
    with pytest.raises(graph.GraphError):
        graph.references_json([{"kind": "video", "file": f"{i}.mp4"} for i in range(4)])


@pytest.mark.parametrize("refs,expected", [
    ([{"kind": "image", "file": "a"}, {"kind": "image", "file": "b"}], ["<Picture 1>", "<Picture 2>"]),
    ([{"kind": "audio", "file": "s"}, {"kind": "video", "file": "v"}, {"kind": "image", "file": "i"}],
     ["<Audio 2>", "<Video 1> <Audio 1>", "<Picture 1>"]),
    ([{"kind": "video", "file": "v1", "use_soundtrack": False}, {"kind": "video", "file": "v2"}, {"kind": "audio", "file": "s"}],
     ["<Video 1>", "<Video 2> <Audio 1>", "<Audio 2>"]),
])
def test_reference_tags(refs, expected):
    assert graph.reference_tags(refs) == expected


def test_reference_tags_match_refpack():
    """Same numbering as MiniMaxRefPack's own assign_tags, when its source is available."""
    import os
    import sys

    root = os.environ.get("MMH3_REFPACK_DIR")
    if not root or not os.path.isdir(root):
        pytest.skip("MiniMaxRefPack source not available")
    sys.path.insert(0, root)
    from minimax_refpack import refs as rr

    refs = [{"kind": "audio", "file": "s.wav"}, {"kind": "video", "file": "v1.mp4"}, {"kind": "image", "file": "i.png"},
            {"kind": "video", "file": "v2.mp4", "use_soundtrack": False}, {"kind": "image", "file": "j.png"}]
    tagged = rr.ReferenceSet.from_json(graph.references_json(refs)).assign_tags()
    theirs = {t.file: (t.tag if t.audio_tag is None else f"{t.tag} {t.audio_tag}") for t in tagged}
    ours = dict(zip([r["file"] for r in refs], graph.reference_tags(refs)))
    assert ours == theirs
