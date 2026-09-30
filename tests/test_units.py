"""Recipes, LoRA file selection, CivitAI file classification, library migration."""
from __future__ import annotations

import json

import pytest

from studio import civitai, library, loras, paths, recipes, util


def test_every_recipe_resolves_and_names_real_models():
    models = util.image_yaml("models.yaml")["models"]
    for fam, spec in recipes.catalog().items():
        assert spec["default"] in spec["recipes"]
        assert set(spec["compare"]) <= set(spec["recipes"])
        mode = "t2v" if fam == "fl2v" else "r2v"
        for rid, r in spec["recipes"].items():
            resolved = recipes.resolve(mode, rid)
            assert r["lora"] in models, rid
            assert fam in models[r["lora"]]["used_by"], f"{rid} uses a LoRA for the other family"
            assert resolved["steps"] >= 1


def test_known_good_defaults_are_preserved():
    """The defaults are the settings the previous build ran (9d134f3)."""
    fl = recipes.resolve("t2v")
    assert (fl["steps"], fl["strength"], fl["shift"], fl["lora"]) == (8, 1.0, [6.0, 3.0], "fl2v_turbo_8step")
    r2 = recipes.resolve("r2v")
    assert (r2["steps"], r2["strength"], r2["shift"], r2["lora"]) == (8, 1.0, [12.0, 3.0], "ref2v_turbo_8step")
    assert r2["sampler"] == "euler"  # never seeds_2: that recipe drifted the camera


def test_no_4step_r2v_recipe():
    for rid, r in recipes.catalog()["ref2v"]["recipes"].items():
        assert r["steps"] >= 8, f"{rid}: the only 4-step Ref2V LoRA is the v0.1 preview that drifted"


@pytest.mark.parametrize("bad", [{"steps": 0}, {"strength": 5}, {"shift": [1]}, {"ref_image_size": "huge"}])
def test_bad_overrides_rejected(bad):
    with pytest.raises(recipes.RecipeError):
        recipes.resolve("r2v", "balanced", bad)


@pytest.mark.parametrize("names,expected", [
    (["MysticXXX_MMH3-V4.safetensors", "MysticXXX_MMH3-V4-ref2va.safetensors"], [("fl2v", "main"), ("ref2v", "main")]),
    (["minimax-h3-digicam.safetensors"], [("any", "main")]),
    (["style_visual.safetensors", "style_motion_helper.safetensors"], [("any", "main"), ("any", "helper")]),
    (["preference_lora.safetensors", "refined.safetensors"], [("any", "main"), ("any", "main")]),
    (["vagassist_e40.safetensors"], [("any", "main")]),
    (["H3_T2V_style.safetensors", "H3_Ref2V_style.safetensors"], [("fl2v", "main"), ("ref2v", "main")]),
])
def test_classify_files(names, expected):
    got = civitai.classify_files([{"name": n} for n in names])
    assert [(f["family"], f["role"]) for f in got] == expected


def _catalog(workspace, entries):
    paths.ensure_dirs()
    util.write_yaml(loras.CATALOG, {"loras": entries})


def _touch_lora(name):
    p = loras.LORA_DIR / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_multi_file_lora_picks_file_per_family(workspace):
    _catalog(workspace, [{
        "version_id": 3266628, "nickname": "Mystic v4", "recommended_strength": 0.6,
        "files": [
            {"id": 1, "name": "MysticXXX_MMH3-V4.safetensors", "family": "fl2v", "role": "main", "install": True},
            {"id": 2, "name": "MysticXXX_MMH3-V4-ref2va.safetensors", "family": "ref2v", "role": "main", "install": True},
            {"id": 3, "name": "Mystic_motion.safetensors", "family": "any", "role": "helper", "install": True, "scale": 0.5},
        ]}])
    for n in ("MysticXXX_MMH3-V4.safetensors", "MysticXXX_MMH3-V4-ref2va.safetensors", "Mystic_motion.safetensors"):
        _touch_lora(n)
    fl, warn = loras.resolve_selection([{"key": "3266628", "strength": 0.6}], "fl2v")
    assert warn == []
    assert [(f["file"], f["strength"]) for f in fl] == [("MysticXXX_MMH3-V4.safetensors", 0.6), ("Mystic_motion.safetensors", 0.3)]
    r2, _ = loras.resolve_selection([{"key": "3266628", "strength": 0.6}], "ref2v")
    assert [f["file"] for f in r2] == ["MysticXXX_MMH3-V4-ref2va.safetensors", "Mystic_motion.safetensors"]


def test_legacy_single_file_entry_and_untracked(workspace):
    _catalog(workspace, [{"version_id": 3224980, "nickname": "Digicam", "filename": "minimax-h3-digicam.safetensors"},
                         {"version_id": 9, "nickname": "R2V only", "files": [
                             {"id": 1, "name": "only_ref2va.safetensors", "family": "ref2v", "role": "main"}]}])
    _touch_lora("minimax-h3-digicam.safetensors")
    _touch_lora("only_ref2va.safetensors")
    _touch_lora("loose_file.safetensors")
    out, warn = loras.resolve_selection([{"key": "3224980", "strength": 1.2}, {"key": "9", "strength": 1},
                                         {"key": "loose_file.safetensors", "file": "loose_file.safetensors", "strength": 0.5}], "fl2v")
    assert [o["file"] for o in out] == ["minimax-h3-digicam.safetensors", "loose_file.safetensors"]
    assert warn and "R2V only" in warn[0]
    listing = {i["key"]: i for i in loras.listing()["items"]}
    assert listing["3224980"]["families"] == ["fl2v", "ref2v"]
    assert listing["9"]["families"] == ["ref2v"]
    assert listing["loose_file.safetensors"]["untracked"]


def test_merge_files_keeps_user_edits():
    old = [{"name": "a.safetensors", "family": "ref2v", "role": "helper", "install": False}]
    new = [{"id": 1, "name": "a.safetensors", "family": "any", "role": "main", "install": True},
           {"id": 2, "name": "b.safetensors", "family": "any", "role": "main", "install": True}]
    kept = loras._merge_files(old, new, keep_install=True)
    assert kept[0] == {"id": 1, "name": "a.safetensors", "family": "ref2v", "role": "helper", "install": False}
    assert kept[1]["install"] is True
    explicit = loras._merge_files(old, new)
    assert explicit[0]["install"] is True and explicit[0]["family"] == "ref2v"


def test_checkpoints_include_builtins_and_user(workspace):
    util.write_json(loras.CHECKPOINTS, {"items": [{"id": "civ1", "label": "Mine", "version_id": 1,
                                                   "files": {"fl2v": "diffusion_models/mine.safetensors"}}]})
    ids = {c["id"]: c for c in loras.checkpoints()}
    assert {"stock", "eros", "civ1"} <= set(ids)
    assert loras.checkpoint_file("stock", "ref2v").endswith("ref2va_pruned_int8_convrot.safetensors")
    assert loras.checkpoint_file("civ1", "ref2v") is None


def test_legacy_record_normalization():
    legacy = {"mode": "r2v", "prompt_mode": "auto", "prompt_idea": "idea", "actual_prompt": "full prompt",
              "aspect_ratio": "2:3 (Portrait Photo)", "megapixels": 0.8, "duration": 7, "seed": 5,
              "refs": [{"kind": "image", "file": "a.png"}], "loras": [{"filename": "x.safetensors", "strength": 0.4}],
              "base_checkpoint": "eros_beta5_int8", "generation_profile": "fast"}
    n = library.normalize_record(legacy)
    assert n["aspect"] == "2:3" and n["prompt"] == "full prompt" and n["idea"] == "idea"
    assert n["checkpoint"] == "eros" and n["loras"][0]["file"] == "x.safetensors"
    assert n["recipe_id"] == "balanced"  # the old fast R2V recipe maps to balanced, never back to the drift


def test_library_reads_legacy_files(workspace):
    out = paths.OUTPUT / "MMH3"
    out.mkdir(parents=True, exist_ok=True)
    for name in ("T2V_00001.mp4", "T2V_00001-audio.mp4", "T2V_00001.png", "R2V_00002-audio.mp4"):
        (out / name).write_bytes(b"x")
    util.write_json(library.LEGACY_META, {"MMH3/T2V_00001-audio.mp4": {"mode": "t2v", "prompt": "p"}})
    util.write_json(library.LIBRARY_FILE, {"groups": ["Keepers"], "videos": {"MMH3/T2V_00001-audio.mp4": {"favorite": True, "group": "Keepers"}}})
    library.refresh_index()
    items = {i["file"]: i for i in library.outputs()["items"]}
    assert "MMH3/T2V_00001.mp4" not in items  # silent twin hidden
    assert items["MMH3/T2V_00001-audio.mp4"]["favorite"] and items["MMH3/T2V_00001-audio.mp4"]["preview"] == "MMH3/T2V_00001.png"
    assert items["MMH3/R2V_00002-audio.mp4"]["mode"] == "r2v"
    library.update_output("MMH3/R2V_00002-audio.mp4", favorite=True, group="New")
    lib = json.loads(library.LIBRARY_FILE.read_text())
    assert lib["groups"] == ["Keepers", "New"]


def test_paths_cannot_escape(workspace):
    for bad in ("../x", "../../etc/passwd", "MMH3/../../x"):
        with pytest.raises(library.LibraryError):
            library.safe_rel(paths.OUTPUT, bad)
    # an absolute path is treated as relative to the root, never as the real /etc
    assert library.safe_rel(paths.OUTPUT, "/etc/passwd") == (paths.OUTPUT / "etc/passwd").resolve()


def test_alternate_copies_are_never_stacked(workspace):
    """fp16 + fp32 of the same LoRA: only one main file loads per mode."""
    files = civitai.classify_files([{"name": "thing_fp16.safetensors", "primary": True},
                                    {"name": "thing_fp32.safetensors", "primary": False},
                                    {"name": "thing_ref2va.safetensors", "primary": False}])
    assert [f["suggested"] for f in files] == [True, False, True]
    _catalog(workspace, [{"version_id": 77, "nickname": "Thing", "files": [
        {"id": 1, "name": "thing_fp16.safetensors", "family": "fl2v", "role": "main", "primary": True},
        {"id": 2, "name": "thing_fp32.safetensors", "family": "fl2v", "role": "main"},
        {"id": 3, "name": "thing_ref2va.safetensors", "family": "ref2v", "role": "main"},
        {"id": 4, "name": "thing_motion.safetensors", "family": "any", "role": "helper"}]}])
    for n in ("thing_fp16.safetensors", "thing_fp32.safetensors", "thing_ref2va.safetensors", "thing_motion.safetensors"):
        _touch_lora(n)
    fl, _ = loras.resolve_selection([{"key": "77", "strength": 1}], "fl2v")
    assert [f["file"] for f in fl] == ["thing_fp16.safetensors", "thing_motion.safetensors"]
    r2, _ = loras.resolve_selection([{"key": "77", "strength": 1}], "ref2v")
    assert [f["file"] for f in r2] == ["thing_ref2va.safetensors", "thing_motion.safetensors"]


def test_memory_reads_tightest_cgroup_limit(tmp_path, monkeypatch):
    from studio import memory

    outer, inner = tmp_path / "outer", tmp_path / "outer/inner"
    inner.mkdir(parents=True)
    (outer / "memory.max").write_text(str(100 * 1024 ** 3))
    (outer / "memory.current").write_text(str(10 * 1024 ** 3))
    (outer / "memory.stat").write_text("inactive_file 0\n")
    (inner / "memory.max").write_text(str(20 * 1024 ** 3))
    (inner / "memory.current").write_text(str(9 * 1024 ** 3))
    (inner / "memory.stat").write_text(f"anon 1\ninactive_file {4 * 1024 ** 3}\n")
    monkeypatch.setattr(memory, "_cgroup_dirs", lambda: [str(inner), str(outer)])
    monkeypatch.setattr(memory.os, "sysconf", lambda k: {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 2 ** 30}[k])
    m = memory.read()
    assert m.limited and round(m.limit / 1024 ** 3) == 20 and round(m.used / 1024 ** 3) == 5
    assert memory.cache_headroom_gb("auto") == 12.0 and memory.cache_headroom_gb(24) == 24.0


def test_prompt_writer_payload_and_parsing(workspace, monkeypatch):
    """T2V/I2V drafting: system prompt, idea + format block, image attached, fences stripped."""
    import asyncio

    from aiohttp import web

    from studio import prompting

    seen = {}

    async def handler(request):
        seen.update(await request.json())
        seen["auth"] = request.headers.get("Authorization")
        return web.json_response({"choices": [{"message": {"content": "```\nintegrated_multimodal_description: ok\n```"}}]})

    async def run():
        app = web.Application()
        app.router.add_post("/chat", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(prompting, "OPENROUTER_URL", f"http://127.0.0.1:{port}/chat")
        try:
            from PIL import Image

            img = paths.INPUT / "mmh3/start.png"
            img.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (32, 32)).save(img)
            return await prompting.write({"mode": "i2v", "idea": "she smiles", "aspect": "9:16", "megapixels": 0.7,
                                          "duration": 5, "start_image": "mmh3/start.png"})
        finally:
            await runner.cleanup()

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    text = asyncio.run(run())
    assert text == "integrated_multimodal_description: ok"
    assert seen["auth"] == "Bearer sk-or-test"
    assert seen["messages"][0]["role"] == "system" and "Image-to-Video" in seen["messages"][0]["content"]
    user = seen["messages"][1]["content"]
    assert user[0]["text"].startswith("she smiles") and "Width: 640\nHeight: 1152" in user[0]["text"]
    assert user[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
