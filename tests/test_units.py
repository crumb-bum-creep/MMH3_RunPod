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
            assert models[r["lora"]].get("role") == "turbo", rid
            assert resolved["steps"] >= 1
        assert spec["av_recipe"] is None or spec["av_recipe"] in spec["recipes"]


def test_known_good_defaults_are_preserved():
    """The defaults are the settings the previous build ran (9d134f3)."""
    fl = recipes.resolve("t2v")
    assert (fl["steps"], fl["strength"], fl["shift"], fl["lora"]) == (8, 1.0, [6.0, 3.0], "fl2v_turbo_8step")
    r2 = recipes.resolve("r2v")
    assert (r2["steps"], r2["strength"], r2["shift"], r2["lora"]) == (8, 1.0, [12.0, 3.0], "ref2v_turbo_8step")
    assert r2["sampler"] == "euler"  # never seeds_2: that recipe drifted the camera


def test_retired_original_ref_size_falls_back_to_max():
    assert recipes.resolve("r2v", "balanced", {"ref_image_size": "original"})["ref_image_size"] == "max"
    assert set(recipes.REF_IMAGE_SIZES) == {"max", "match"}


def test_legacy_euler_is_the_low_drift_baseline():
    """v0.1 @ 0.85, 4 steps, Euler + Beta, extend 2 steps 0.8 -> 0 linear, model-default shift.
    Opt-in (and the audio/video recipe), never the R2V default."""
    r = recipes.resolve("r2v", "legacy_euler")
    assert (r["lora"], r["strength"], r["steps"], r["sampler"], r["scheduler"]) == (
        "ref2v_turbo_4step_v01", 0.85, 4, "euler", "beta")
    assert r["extend"] == {"steps": 2, "start": 0.8, "end": 0.0, "spacing": "linear"} and "shift" not in r
    cat = recipes.catalog()["ref2v"]
    assert cat["default"] == "balanced" and cat["av_recipe"] == "legacy_euler"


def test_your_recipes_and_choices(workspace):
    recipes.USER_FILE.unlink(missing_ok=True)
    try:
        mine = recipes.save_recipe("ref2v", {"label": "Base 20", "lora": "none", "steps": 20, "sampler": "euler",
                                             "scheduler": "simple", "shift": "default", "extend": "off",
                                             "ref_image_size": "match"})
        assert mine["id"] == "my_base_20" and mine["lora"] is None
        r = recipes.resolve("r2v", "my_base_20")
        assert r["steps"] == 20 and r["lora"] is None and "shift" not in r and "extend" not in r
        again = recipes.save_recipe("ref2v", {"label": "Base 20", "steps": 20, "lora": "ref2v_turbo_8step"})
        assert again["id"] == "my_base_20_2"  # a new recipe never overwrites another
        with pytest.raises(recipes.RecipeError):
            recipes.save_recipe("ref2v", {"label": "x", "lora": "fl2v_turbo_8step"})  # other family's LoRA
        with pytest.raises(recipes.RecipeError):
            recipes.save_recipe("ref2v", {"label": "x", "lora": "none"}, "balanced")  # built-ins are read-only
        out = recipes.save_choices("ref2v", {"compare": ["balanced", "legacy_euler"], "hidden": ["upstream", "nope"],
                                             "default": "my_base_20", "av_recipe": None})
        assert out["compare"] == ["balanced", "legacy_euler"] and out["hidden"] == ["upstream"]
        assert out["default"] == "my_base_20" and out["av_recipe"] is None
        with pytest.raises(recipes.RecipeError):
            recipes.save_choices("ref2v", {"hidden": ["my_base_20"]})  # the default stays visible
        assert recipes.resolve("r2v", "upstream")["id"] == "upstream"  # hidden still resolves (old outputs, queue)
        recipes.delete_recipe("ref2v", "my_base_20")
        cat = recipes.catalog()["ref2v"]
        assert "my_base_20" not in cat["recipes"] and cat["default"] == "balanced"
        with pytest.raises(recipes.RecipeError):
            recipes.delete_recipe("ref2v", "balanced")
    finally:
        recipes.USER_FILE.unlink(missing_ok=True)


def test_a_family_waits_only_for_its_recipes_turbo(workspace, monkeypatch):
    from studio import provision

    monkeypatch.setattr(provision, "is_present", lambda e: "turbo_4step_v0.1" not in e["file"])
    _, missing = provision.family_ready("ref2v", turbo="ref2v_turbo_8step")
    assert "ref2v_turbo_4step_v01" not in missing  # still downloading, but this recipe doesn't use it
    ready, missing = provision.family_ready("ref2v", turbo="ref2v_turbo_4step_v01")
    assert not ready and "ref2v_turbo_4step_v01" in missing


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


def test_per_file_strengths_are_independent(workspace):
    _catalog(workspace, [{
        "version_id": 5, "nickname": "Visual+helper",
        "files": [
            {"id": 1, "name": "vis.safetensors", "family": "any", "role": "main", "install": True},
            {"id": 2, "name": "help.safetensors", "family": "any", "role": "helper", "install": True, "scale": 0.5},
        ]}])
    _touch_lora("vis.safetensors")
    _touch_lora("help.safetensors")
    out, _ = loras.resolve_selection([{"key": "5", "strength": 1.0,
                                       "parts": {"vis.safetensors": 0.9, "help.safetensors": 0.5}}], "fl2v")
    assert {f["file"]: f["strength"] for f in out} == {"vis.safetensors": 0.9, "help.safetensors": 0.5}
    item = next(i for i in loras.listing()["items"] if i["key"] == "5")
    assert [a["name"] for a in item["active"]["fl2v"]] == ["vis.safetensors", "help.safetensors"]


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
    # the I2VA alignment line is Studio's, even when the model leaves it out
    assert text == ("For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully "
                    "referenced.\n\nintegrated_multimodal_description: ok")
    assert seen["auth"] == "Bearer sk-or-test"
    assert seen["messages"][0]["role"] == "system" and "Image-to-Video" in seen["messages"][0]["content"]
    user = seen["messages"][1]["content"]
    assert user[0]["text"].startswith("USER DIRECTION:\nshe smiles")
    assert "frame: 640 x 1152" in user[0]["text"] and "duration: 5.17 seconds" in user[0]["text"]
    assert user[1]["text"].startswith("<Picture 1>, the first frame")
    assert user[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def _i2v(**kw):
    return {"mode": "i2v", "aspect": "9:16", "megapixels": 0.7, "duration": 5, **kw}


def test_keyframe_task_picks_the_guides_prompt():
    from studio import prompting

    prompts = prompting.system_prompts()
    for job, key in ((_i2v(start_image="a.png"), "i2v_auto"), (_i2v(start_image="a.png", end_image="b.png"), "fl2v_auto"),
                     (_i2v(end_image="b.png"), "l2v_auto"), ({"mode": "t2v"}, "t2v_auto"), ({"mode": "r2v"}, "r2v_auto")):
        assert prompting._system_prompt_for(job, prompts) == prompts[key]
    assert {"t2v_auto", "i2v_auto", "fl2v_auto", "l2v_auto", "r2v_auto", "refine"} <= set(prompts)


def test_alignment_line_tracks_frames_shots_and_length():
    from studio import prompting

    body = "integrated_multimodal_description: [Shot 1] a. [Shot 2] At 00:03.000, b.\n\noverall_soundscape: x\n\nnon_diegetic_music: N/A"
    fl = prompting.align_keyframes(_i2v(start_image="a.png", end_image="b.png", duration=8), body)
    assert fl.startswith("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the "
                         "0.00-second mark of the target video; Picture 2 (from Shot 2) aligns with the 8.00-second mark")
    l2 = prompting.align_keyframes(_i2v(end_image="b.png", duration=6), body)
    assert l2.startswith("How the reference pictures align with the target video — <Picture 1> (from [Shot 2]) aligns with the 6.58-second mark")
    # an existing (stale) line is replaced, not stacked; idempotent
    again = prompting.align_keyframes(_i2v(end_image="b.png", duration=10), l2)
    assert again.count("How the reference pictures") == 1 and "10.12-second mark" in again
    assert prompting.align_keyframes(_i2v(end_image="b.png", duration=10), again) == again
    # free-form prompts and other modes are left alone
    assert prompting.align_keyframes(_i2v(start_image="a.png"), "she waves") == "she waves"
    assert prompting.align_keyframes({"mode": "t2v", "duration": 5}, body) == body


def test_edited_prompts_go_stale_when_defaults_move_on(workspace):
    from studio import prompting

    user = paths.CONFIG / "system_prompts.yaml"
    util.write_yaml(user, {"prompts": {"t2v_auto": "my old t2v prompt"}})  # saved before versions existed
    try:
        assert prompting.system_prompts()["t2v_auto"] != "my old t2v prompt"
        assert prompting.stale_prompts() == {"t2v_auto": "my old t2v prompt"}
        prompting.save_system_prompt("t2v_auto", "my new t2v prompt")
        assert prompting.system_prompts()["t2v_auto"] == "my new t2v prompt"
        assert prompting.stale_prompts() == {}
    finally:
        user.unlink(missing_ok=True)


def test_refine_sends_prompt_request_rules_and_frames(workspace, monkeypatch):
    import asyncio

    from studio import prompting

    sent = {}

    async def fake(messages, cfg, key):
        sent["messages"] = messages
        return "integrated_multimodal_description: [Shot 1] at night.\n\noverall_soundscape: x\n\nnon_diegetic_music: N/A"

    from PIL import Image

    img = paths.INPUT / "mmh3/end.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32)).save(img)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(prompting, "_openrouter", fake)
    out = asyncio.run(prompting.refine(_i2v(end_image="mmh3/end.png"), "integrated_multimodal_description: [Shot 1] by day.",
                                       "make it night"))
    assert out.startswith("How the reference pictures align with the target video — <Picture 1> (from [Shot 1])")
    system, user = sent["messages"][0]["content"], sent["messages"][1]["content"]
    prompts = prompting.system_prompts()
    assert system.startswith(prompts["refine"]) and prompts["l2v_auto"] in system
    assert "CHANGE REQUEST:\nmake it night" in user[0]["text"] and "CURRENT PROMPT:\nintegrated_multimodal_description: [Shot 1] by day." in user[0]["text"]
    assert user[1]["text"].startswith("<Picture 1>, the last frame") and user[2]["type"] == "image_url"
    with pytest.raises(prompting.PromptError):
        asyncio.run(prompting.refine({"mode": "t2v"}, "", "make it night"))


def test_new_outputs_until_seen(workspace):
    import time

    out = paths.OUTPUT / "MMH3"
    out.mkdir(parents=True, exist_ok=True)
    library.SEEN_FILE.unlink(missing_ok=True)
    old = out / "T2V_09001-audio.mp4"
    old.write_bytes(b"x")
    library._seen()  # first look sets the baseline: what's already there isn't "new"
    time.sleep(0.02)
    fresh = out / "T2V_09002-audio.mp4"
    fresh.write_bytes(b"x")
    library.refresh_index()
    items = {i["file"]: i for i in library.outputs()["items"]}
    assert items["MMH3/T2V_09001-audio.mp4"]["new"] is False and items["MMH3/T2V_09002-audio.mp4"]["new"] is True
    library.mark_seen(["MMH3/T2V_09002-audio.mp4"])
    assert not any(i["new"] for i in library.outputs()["items"])
    (out / "T2V_09003-audio.mp4").write_bytes(b"x")
    library.refresh_index()
    assert library.outputs()["new"] == 1
    library.mark_seen([], everything=True)
    assert library.outputs()["new"] == 0
    with pytest.raises(library.LibraryError):
        library.mark_seen(["../../etc/passwd"])


def test_quick_install_loras_skip_boot_sync(workspace, monkeypatch):
    queued = []
    monkeypatch.setattr(loras, "_enqueue", lambda item: queued.append(item))
    files = [{"id": 1, "name": "q.safetensors", "family": "any", "role": "main", "install": True}]
    _catalog(workspace, [{"version_id": 11, "nickname": "Quick", "auto_install": False, "files": files},
                         {"version_id": 12, "nickname": "Auto", "files": [{**files[0], "id": 2, "name": "a.safetensors"}]}])
    loras.sync()
    assert [j["file"]["name"] for kind, j in queued if kind == "lora"] == ["a.safetensors"]
    item = next(i for i in loras.listing()["items"] if i["key"] == "11")
    assert item["auto_install"] is False and item["files"][0]["state"] == "available"
    queued.clear()
    loras.install("11")
    assert [j["file"]["name"] for kind, j in queued] == ["q.safetensors"]
    # uninstall deletes the files and keeps it under quick install
    _touch_lora("a.safetensors")
    loras.uninstall("12")
    assert not (loras.LORA_DIR / "a.safetensors").exists()
    assert next(e for e in loras.catalog() if e["version_id"] == 12)["auto_install"] is False


def test_existing_catalog_takes_seed_auto_install_once(workspace):
    _catalog(workspace, [{"version_id": 3260276, "nickname": "Mystic XXX v3", "filename": "MysticXXX_MMH3-V3.safetensors"},
                         {"version_id": 3224980, "nickname": "Digicam", "filename": "minimax-h3-digicam.safetensors"},
                         {"version_id": 777, "nickname": "Mine"}])
    loras.ensure_seeded()
    by = {e["version_id"]: e for e in loras.catalog()}
    assert by[3260276]["auto_install"] is False and by[3224980]["auto_install"] is False
    assert "auto_install" not in by[777]  # not in the seed: untouched, downloads as before
    loras.upsert({"version_id": 3260276, "auto_install": True})  # your choice sticks
    loras.ensure_seeded()
    assert next(e for e in loras.catalog() if e["version_id"] == 3260276)["auto_install"] is True


def _vhs_next(folder, prefix):
    """VideoHelperSuite's VideoCombine counter, copied: highest <prefix>_<n>... file + 1."""
    import os
    import re

    m = re.compile(f"{re.escape(prefix)}_(\\d+)\\D*\\..+", re.IGNORECASE)
    return max([int(x.group(1)) for f in os.listdir(folder) for x in [m.fullmatch(f)] if x], default=0) + 1


def test_deleting_the_newest_clip_never_frees_its_name(workspace):
    folder = paths.OUTPUT / "NUMS"
    folder.mkdir(parents=True, exist_ok=True)
    for n in (1, 2, 3):
        for name in (f"T2V_{n:05d}-audio.mp4", f"T2V_{n:05d}.png", f"T2V_{n:05d}-audio.mp4.h3.json"):
            (folder / name).write_bytes(b"x")
    library.delete_output("NUMS/T2V_00003-audio.mp4")
    library.delete_output("NUMS/T2V_00002-audio.mp4")
    assert _vhs_next(folder, "T2V") == 4, "a new render must not reuse a deleted clip's name"
    markers = [f for f in __import__("os").listdir(folder) if f.endswith(library.RESERVED_SUFFIX)]
    assert markers == ["T2V_00003-number-reserved.txt"]  # one marker per prefix, at the highest number
    assert all(i["file"].endswith(".mp4") for i in library.outputs()["items"])  # markers never show in the Library
    (folder / "T2V_00004-audio.mp4").write_bytes(b"x")  # the next render lands; deleting an older clip
    library.delete_output("NUMS/T2V_00001-audio.mp4")
    assert not [f for f in __import__("os").listdir(folder) if f.endswith(library.RESERVED_SUFFIX)]  # 4 holds the count
    assert _vhs_next(folder, "T2V") == 5


def test_job_finishing_during_the_history_poll_is_not_left_running(workspace, monkeypatch):
    """The websocket can report success while the 10 s history fallback is mid-request. That must
    not raise (InvalidStateError) and strand the job as "running" while the queue moves on."""
    import asyncio

    from studio import jobs

    class FakeComfy:
        def on_event(self, cb):
            pass

        async def history(self, pid):
            await runner._on_event("execution_success", {"prompt_id": pid})  # lands mid-request
            return {"status": {"completed": True, "status_str": "success"}}

    monkeypatch.setattr(jobs, "POLL_SECONDS", 0.01)
    runner = jobs.Runner(FakeComfy())
    finalized = []

    async def fake_finalize(job):
        finalized.append(job["id"])
        runner._touch(job, status="done")

    async def nothing():
        return None

    monkeypatch.setattr(runner, "_finalize", fake_finalize)
    monkeypatch.setattr(runner, "_after_job", nothing)
    job = {"id": "j1", "status": "running", "prompt_id": "p1", "created": 1.0}
    runner.jobs[job["id"]] = job
    asyncio.run(runner._await_completion(job))
    assert job["status"] == "done" and finalized == ["j1"]


def test_a_crash_while_running_a_job_never_leaves_it_running(workspace, monkeypatch):
    import asyncio

    from studio import jobs

    class FakeComfy:
        def on_event(self, cb):
            pass

    runner = jobs.Runner(FakeComfy())
    job = {"id": "j2", "status": "queued", "created": 1.0, "prompt": "x"}
    runner.jobs[job["id"]] = job

    async def boom(j):
        runner._touch(j, status="running", prompt_id="p2")
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(runner, "_run", boom)
    asyncio.run(runner._run_one(job))
    assert job["status"] == "failed" and "something unexpected" in job["error"]


def test_workflow_check_finds_missing_nodes_and_models(workspace):
    from studio import workflows

    vae = paths.MODELS / "vae" / "minimax_h3_video_vae_fp16.safetensors"
    vae.parent.mkdir(parents=True, exist_ok=True)
    vae.write_bytes(b"x")
    wf = {"nodes": [
        {"type": "VAELoader", "widgets_values": ["minimax_h3_t1_image_vae_step1597.safetensors"]},
        {"type": "VAELoader", "widgets_values": ["minimax_h3_video_vae_fp16.safetensors"]},
        {"type": "SomeCustomNode", "widgets_values": [1, "text"]},
        {"type": "Note", "widgets_values": ["just a note.safetensors? no"]},
        {"type": "1f2e-subgraph-id"}],
        "definitions": {"subgraphs": [{"id": "1f2e-subgraph-id", "nodes": [
            {"type": "UNETLoader", "widgets_values": ["models/diffusion_models/other_h3.safetensors", "default"]}]}]}}
    out = workflows.check(wf, {"VAELoader", "UNETLoader"})
    assert out["missing_nodes"] == ["SomeCustomNode"]
    assert out["missing_models"] == ["minimax_h3_t1_image_vae_step1597.safetensors", "other_h3.safetensors"]
    assert workflows.check(wf, None)["missing_nodes"] == []  # ComfyUI not up yet: nodes unchecked


def test_workflows_install_from_civitai_and_keep_your_edits(workspace, monkeypatch):
    import json

    from studio import civitai, workflows

    monkeypatch.delenv("CIVITAI_TOKEN", raising=False)
    workflows.fetch()
    assert all(v["state"] == "needs_token" for v in util.read_json(workflows.STATUS, {}).values())

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"nodes": [{"type": "VAELoader", "widgets_values": ["x.safetensors"]}]}

    calls = []
    monkeypatch.setenv("CIVITAI_TOKEN", "tok")
    monkeypatch.setattr(workflows.requests, "get", lambda url, **kw: calls.append(url) or Resp())
    workflows.fetch()
    names = {w["filename"] for w in workflows.manifest()["workflows"]}
    assert {p.name for p in workflows.folder().iterdir()} == names
    assert all("token=tok" in u and "fileId=" in u for u in calls)
    edited = workflows.folder() / sorted(names)[0]
    edited.write_text(json.dumps({"nodes": [], "mine": True}))
    calls.clear()
    workflows.fetch()  # present: left alone
    assert not calls and json.loads(edited.read_text())["mine"]
    rows = {r["id"]: r for r in workflows.listing({"VAELoader"})}
    assert all(r["installed"] and r["path"].startswith("Studio extras/") for r in rows.values())
    assert civitai.token() == "tok"


def test_workflow_adapt_maps_h3_names_and_mutes_optional_nodes():
    """Shaped like the v1 image-edit workflow: an nvfp4 text encoder and an RMBG compare node
    feeding its own SaveImage, next to the SaveImage that holds the actual edit."""
    from studio import workflows

    wf = {"nodes": [
        {"id": 130, "type": "CLIPLoader", "widgets_values": ["qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "minimax", "default"]},
        {"id": 149, "type": "ResizeImageMaskNode", "inputs": [], "widgets_values": []},
        {"id": 154, "type": "AILab_ImageCompare", "inputs": [{"name": "image1", "link": 278}, {"name": "image2", "link": 279}]},
        {"id": 165, "type": "SaveImage", "inputs": [{"name": "images", "link": 290}], "widgets_values": ["Edit"]},
        {"id": 166, "type": "SaveImage", "inputs": [{"name": "images", "link": 291}], "widgets_values": ["Compare"]},
        {"id": 114, "type": "LoadImage", "inputs": [], "widgets_values": ["x.png", "image"]}],
        "links": [[278, 114, 0, 154, 0, "IMAGE"], [279, 149, 0, 154, 1, "IMAGE"],
                  [290, 149, 0, 165, 0, "IMAGE"], [291, 154, 0, 166, 0, "IMAGE"]]}
    out = workflows.adapt(wf, {"mute": ["AILab_ImageCompare"]})
    by = {n["id"]: n for n in out["nodes"]}
    assert by[130]["widgets_values"][0] == "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
    assert by[154]["mode"] == workflows.MUTED and by[166]["mode"] == workflows.MUTED  # compare + its save
    assert "mode" not in by[165] and "mode" not in by[114]  # the edit is still saved
    known = {"CLIPLoader", "ResizeImageMaskNode", "SaveImage", "LoadImage"}
    assert workflows.check(out, known)["missing_nodes"] == []  # muted nodes aren't reported


def test_workflow_splice_rewires_an_empty_lora_loader_and_keeps_a_used_one():
    """Shaped like the v2 image-edit workflow: an rgthree LoRA loader with no LoRAs
    between the UNET loader and the guider/scheduler."""
    from studio import workflows

    def wf(lora_rows):
        return {"nodes": [
            {"id": 127, "type": "UNETLoader", "inputs": [], "outputs": [{"type": "MODEL", "links": [334]}]},
            {"id": 196, "type": "Power Lora Loader (rgthree)", "widgets_values": [{}, {"type": "PowerLoraLoaderHeaderWidget"}, *lora_rows, ""],
             "inputs": [{"type": "MODEL", "link": 334}, {"type": "CLIP", "link": None}],
             "outputs": [{"type": "MODEL", "links": [332, 333]}, {"type": "CLIP", "links": [335]}]},
            {"id": 126, "type": "BasicGuider", "inputs": [{"type": "MODEL", "link": 332}], "outputs": []},
            {"id": 124, "type": "BasicScheduler", "inputs": [{"type": "MODEL", "link": 333}], "outputs": []},
            {"id": 200, "type": "CLIPTextEncode", "inputs": [{"type": "CLIP", "link": 335}], "outputs": []}],
            "links": [[332, 196, 0, 126, 0, "MODEL"], [333, 196, 0, 124, 0, "MODEL"],
                      [334, 127, 0, 196, 0, "MODEL"], [335, 196, 1, 200, 0, "CLIP"]]}

    out = workflows.adapt(wf([]), {"splice": ["Power Lora Loader (rgthree)"]})
    by = {n["id"]: n for n in out["nodes"]}
    assert 196 not in by
    assert sorted(out["links"]) == [[332, 127, 0, 126, 0, "MODEL"], [333, 127, 0, 124, 0, "MODEL"]]
    assert by[127]["outputs"][0]["links"] == [332, 333]
    assert by[200]["inputs"][0]["link"] is None  # nothing fed the loader's CLIP
    assert workflows.check(out, {"UNETLoader", "BasicGuider", "BasicScheduler", "CLIPTextEncode"})["missing_nodes"] == []

    used = workflows.adapt(wf([{"on": True, "lora": "style.safetensors", "strength": 1}]),
                           {"splice": ["Power Lora Loader (rgthree)"]})
    assert any(n["id"] == 196 for n in used["nodes"])  # a loaded LoRA is never dropped silently
