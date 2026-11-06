"""Subjects (experimental): variants, reference composition, definition lines and hot-swapping."""
from __future__ import annotations

import pytest

from studio import graph, paths, prompting, subjects


def _file(name):
    p = paths.INPUT / "mmh3" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return f"mmh3/{name}"


@pytest.fixture
def cast(workspace):
    subjects.FILE.unlink(missing_ok=True)
    bob = subjects.save({"name": "Bob", "kind": "character",
                         "description": "a broad-shouldered man in his forties with a short grey beard",
                         "refs": [{"kind": "image", "file": _file("bob_face.png"), "role": "face"},
                                  {"kind": "image", "file": _file("bob_body.png"), "role": "full look"},
                                  {"kind": "audio", "file": _file("bob_voice.wav"), "role": "voice"}],
                         "variants": [{"name": "Tuxedo", "description": "wearing a black tuxedo",
                                       "refs": [{"kind": "image", "file": _file("tux.png"), "role": "outfit"}]},
                                      {"name": "Silent", "mute": True},
                                      {"name": "Simple", "description": "a bearded man", "description_mode": "replace",
                                       "inherit_refs": False,
                                       "refs": [{"kind": "image", "file": _file("bob_face.png"), "role": "face"}]}]})
    alice = subjects.save({"name": "Alice", "kind": "character", "description": "a slim woman with a red bob haircut",
                           "refs": [{"kind": "image", "file": _file("alice.png")}]})
    steve = subjects.save({"name": "Steve", "kind": "character", "description": "a lanky young man with curly blond hair",
                           "refs": [{"kind": "image", "file": _file("steve.png"), "role": "face"}]})
    suzy = subjects.save({"name": "Suzy", "kind": "character", "description": "a petite woman with long black braids",
                          "refs": [{"kind": "image", "file": _file("suzy.png")},
                                   {"kind": "audio", "file": _file("suzy.wav"), "role": "voice"}]})
    beach = subjects.save({"name": "Beach", "kind": "setting", "description": "a quiet beach at sunset",
                           "refs": [{"kind": "image", "file": _file("beach.png")}]})
    return {s["name"]: s for s in (bob, alice, steve, suzy, beach)}


def _variant(s, name):
    return next(v["id"] for v in s["variants"] if v["name"] == name)


def test_variants_add_replace_and_mute(cast):
    bob = cast["Bob"]
    tux = subjects.resolve(bob["id"], _variant(bob, "Tuxedo"))
    assert tux["label"] == "Bob · Tuxedo" and tux["description"].endswith("grey beard, wearing a black tuxedo")
    assert [r["role"] for r in tux["refs"]] == ["face", "full look", "voice", "outfit"]
    silent = subjects.resolve(bob["id"], _variant(bob, "Silent"))
    assert all(r["kind"] != "audio" for r in silent["refs"])
    simple = subjects.resolve(bob["id"], _variant(bob, "Simple"))
    assert simple["description"] == "a bearded man" and len(simple["refs"]) == 1


def test_compose_numbers_subject_references_first_then_loose_ones(cast):
    loose = [{"kind": "image", "file": _file("storyboard.png")}]
    c = subjects.compose([{"n": 2, "id": cast["Alice"]["id"]}, {"n": 1, "id": cast["Bob"]["id"]}], loose)
    assert [r.get("subject") for r in c["refs"]] == [1, 1, 1, 2, None]
    assert c["tags"] == ["<Picture 1>", "<Picture 2>", "<Audio 1>", "<Picture 3>", "<Picture 4>"]
    graph.references_json(c["refs"])  # extra keys (subject, role) are fine for ComfyUI


def test_definition_lines_name_tags_roles_and_voice(cast):
    slots = [{"n": 1, "id": cast["Bob"]["id"]}, {"n": 2, "id": cast["Beach"]["id"]}]
    c = subjects.compose(slots)
    lines = subjects.all_lines(slots, c["refs"])
    assert lines == [
        "<Subject 1> is a broad-shouldered man in his forties with a short grey beard, as shown in "
        "<Picture 1> (face) and <Picture 2> (full look).",
        "<Audio 1> is the voice-timbre reference for <Subject 1> (S1).",
        "<Subject 2> is a quiet beach at sunset, as shown in <Picture 3>.",
    ]
    block = subjects.direction_block(slots, c["refs"])
    assert "verbatim" in block and "from <Subject 3> on" in block and "<Subject 1> speaks as (S1)" in block


PROMPT = """subject_definitions:
<Subject 1> is a broad-shouldered man in his forties with a short grey beard, as shown in <Picture 1> (face) and <Picture 2> (full look).
<Audio 1> is the voice-timbre reference for <Subject 1> (S1).
<Subject 2> is a slim woman with a red bob haircut, as shown in <Picture 3>.
<Picture 4> is the first frame of [Shot 1].

summary:
[reference generation + keyframe completion + audio reference] <Subject 1> and <Subject 2> share dinner on a beach, starting from <Picture 4>.

retention_analysis:
<Subject 1> (all shots): fully_preserved - his look follows the references.
<Audio 1>: reference - <Subject 1> follows <Audio 1>'s timbre.
<Subject 2> (all shots): fully_preserved - her look follows the references.
<Picture 4> ([Shot 1] first frame): fully_preserved - composition.

detailed_description:
The shot begins from <Picture 4>. <Subject 1> (S1) raises his glass, his grey beard catching the light, and says <d>[English] To us.</d> <Subject 2> laughs.

overall_soundscape:
Waves.

non_diegetic_music:
N/A"""


def test_swapping_both_subjects_rewrites_only_what_belongs_to_them(cast):
    loose = [{"kind": "image", "file": _file("storyboard.png")}]
    before_slots = [{"n": 1, "id": cast["Bob"]["id"]}, {"n": 2, "id": cast["Alice"]["id"]}]
    after_slots = [{"n": 1, "id": cast["Steve"]["id"]}, {"n": 2, "id": cast["Suzy"]["id"]}]
    before = {"slots": before_slots, "refs": subjects.compose(before_slots, loose)["refs"]}
    after = {"slots": after_slots, "refs": subjects.compose(after_slots, loose)["refs"]}
    assert graph.reference_tags(after["refs"]) == ["<Picture 1>", "<Picture 2>", "<Audio 1>", "<Picture 3>"]

    r = subjects.rewrite(PROMPT, before, after)
    out = r["prompt"]
    sd = out.split("summary:")[0]
    assert "<Subject 1> is a lanky young man with curly blond hair, as shown in <Picture 1> (face)." in sd
    assert "<Subject 2> is a petite woman with long black braids, as shown in <Picture 2>." in sd
    assert "<Audio 1> is the voice-timbre reference for <Subject 2> (S2)." in sd
    assert "for <Subject 1> (S1)" not in out  # Steve has no voice reference
    assert "grey beard, as shown" not in out and "red bob" not in out
    # the loose storyboard moved from <Picture 4> to <Picture 3> everywhere
    assert "<Picture 3> is the first frame of [Shot 1]." in sd
    assert "starting from <Picture 3>" in out and "The shot begins from <Picture 3>" in out
    assert "<Picture 4>" not in out
    # retention: Bob's voice line went, Suzy's came
    rt = out.split("retention_analysis:")[1].split("detailed_description:")[0]
    assert "<Subject 1> follows <Audio 1>" not in rt and "<Audio 1>: reference - <Subject 2>" in rt
    # the body is untouched apart from tags, and the leftover is reported for a Tidy
    assert "his grey beard catching the light" in out
    assert r["leftovers"] == {1: ["beard", "grey"]}
    assert "Steve" in r["tidy_request"] and "Bob" in r["tidy_request"]


def test_insert_lines_into_a_free_form_prompt(cast):
    slots = [{"n": 1, "id": cast["Alice"]["id"]}]
    refs = subjects.compose(slots)["refs"]
    r = subjects.rewrite("<Subject 1> walks along the shore.", {"slots": slots, "refs": refs},
                         {"slots": slots, "refs": refs})
    assert r["prompt"].startswith("subject_definitions:\n<Subject 1> is a slim woman")
    assert r["prompt"].endswith("<Subject 1> walks along the shore.")


def test_auto_direction_carries_the_subject_block(cast, monkeypatch):
    seen = {}

    async def fake_refpack(job, system_prompt, cfg, key):
        seen["idea"] = job["idea"]
        return "ok"

    monkeypatch.setattr(prompting, "_refpack_prompt", fake_refpack)
    monkeypatch.setattr(prompting, "api_key", lambda: "k")
    slots = [{"n": 1, "id": cast["Bob"]["id"]}]
    refs = subjects.compose(slots)["refs"]
    import asyncio
    asyncio.run(prompting.write({"mode": "r2v", "idea": "Bob toasts", "refs": refs, "subjects": slots}))
    assert seen["idea"].startswith("Bob toasts\n\nSUBJECTS")
    assert "<Subject 1> is a broad-shouldered man" in seen["idea"]


def test_a_loose_file_a_subject_brings_is_not_doubled_and_its_tag_follows(cast):
    alice_file = cast["Alice"]["refs"][0]["file"]
    loose = [{"kind": "image", "file": alice_file}, {"kind": "image", "file": _file("storyboard.png")}]
    slots = [{"n": 1, "id": cast["Alice"]["id"]}]
    after = subjects.compose(slots, loose)
    assert [r["file"] for r in after["refs"]].count(alice_file) == 1
    r = subjects.rewrite("<Picture 1> shows her. <Picture 2> is the first frame.",
                         {"slots": [], "refs": loose}, {"slots": slots, "refs": after["refs"]})
    assert r["prompt"].endswith("<Picture 1> shows her. <Picture 2> is the first frame.")
