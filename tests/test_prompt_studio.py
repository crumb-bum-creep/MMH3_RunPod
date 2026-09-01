from __future__ import annotations

import copy

from mmh3 import prompt_studio


def test_new_project_has_editable_scene_and_flash_preview_default():
    project = prompt_studio.new_project(name="Elevator", mode="r2v", duration=15)
    assert project["name"] == "Elevator"
    assert project["mode"] == "r2v"
    assert project["duration"] == 15
    assert project["model"] == "google/gemini-3-flash-preview"
    assert project["scene"]["shots"][0]["start_seconds"] == 0
    assert project["scene"]["subjects"] == []


def test_opaque_picture_slot_stays_opaque_and_reference_binding_is_preserved():
    project = prompt_studio.new_project(mode="r2v")
    subject = prompt_studio.empty_subject(1)
    subject["description"] = "A woman as established by the supplied reference."
    subject["reference"] = {
        "mode": "picture_slot",
        "picture_number": 3,
        "asset_file": "",
        "analyze": False,
    }
    project["scene"]["subjects"] = [subject]
    project = prompt_studio.normalize_project(project)

    assert project["scene"]["subjects"][0]["reference"] == {
        "mode": "picture_slot",
        "picture_number": 3,
        "asset_file": "",
        "analyze": False,
    }
    contract = prompt_studio.reference_contract(project)
    assert "opaque <Picture 3>" in contract
    assert "must not be invented" in contract


def test_preserve_locks_prevents_broad_ai_edit_from_changing_locked_fields_or_reference():
    old = prompt_studio.new_project(mode="r2v")["scene"]
    subject = prompt_studio.empty_subject(1)
    subject["description"] = "Original appearance"
    subject["locks"] = ["description"]
    subject["reference"] = {
        "mode": "asset",
        "picture_number": 1,
        "asset_file": "person.png",
        "analyze": True,
    }
    old["subjects"] = [subject]

    shot = prompt_studio.empty_shot(1)
    shot["action"] = "Original action"
    shot["locks"] = ["action"]
    old["shots"] = [shot]
    old["environment"] = "Original elevator"
    old["locks"] = ["environment"]

    candidate = copy.deepcopy(old)
    candidate["environment"] = "Changed environment"
    candidate["subjects"][0]["description"] = "Changed appearance"
    candidate["subjects"][0]["reference"]["picture_number"] = 8
    candidate["shots"][0]["action"] = "Changed action"

    result = prompt_studio.preserve_locks(old, candidate)
    assert result["environment"] == "Original elevator"
    assert result["subjects"][0]["description"] == "Original appearance"
    assert result["subjects"][0]["reference"]["picture_number"] == 1
    assert result["shots"][0]["action"] == "Original action"


def test_r2v_validator_rejects_invented_picture_and_bad_timing():
    project = prompt_studio.new_project(mode="r2v", duration=10)
    subject = prompt_studio.empty_subject(1)
    subject["reference"] = {
        "mode": "picture_slot",
        "picture_number": 1,
        "asset_file": "",
        "analyze": False,
    }
    project["scene"]["subjects"] = [subject]

    bad = """subject_definitions:
<Subject 1> is established by <Picture 2>.
summary:
reference generation
retention_analysis:
<Subject 1>: fully_preserved
detailed_description:
[Shot 1] At 00:00.000, starts.
[Shot 2] At 00:10.000, cuts.
overall_soundscape:
room tone
non_diegetic_music:
N/A
"""
    validation = prompt_studio.validate_prompt(project, bad)
    assert validation["valid"] is False
    assert any("undefined picture" in x for x in validation["errors"])
    assert any("Shot 1" in x for x in validation["errors"])
    assert any("outside" in x for x in validation["errors"])


def test_r2v_validator_accepts_well_formed_compiled_prompt():
    project = prompt_studio.new_project(mode="r2v", duration=10)
    subject = prompt_studio.empty_subject(1)
    subject["reference"] = {
        "mode": "picture_slot",
        "picture_number": 1,
        "asset_file": "",
        "analyze": False,
    }
    project["scene"]["subjects"] = [subject]

    good = """subject_definitions:
<Subject 1> is established by <Picture 1>.
summary:
reference generation
retention_analysis:
<Subject 1>: fully_preserved
detailed_description:
[Shot 1] A static medium shot.
[Shot 2] At 00:05.000, the shot cuts closer.
overall_soundscape:
room tone
non_diegetic_music:
N/A
"""
    validation = prompt_studio.validate_prompt(project, good)
    assert validation == {"valid": True, "errors": [], "warnings": []}


def test_revision_snapshot_and_restore_round_trip():
    project = prompt_studio.new_project(name="Original")
    project["scene"]["concept"] = "Version one"
    revision = prompt_studio.snapshot_revision(project, "v1")
    project["scene"]["concept"] = "Version two"
    restored = prompt_studio.restore_revision(project, revision["id"])
    assert restored["scene"]["concept"] == "Version one"
    assert restored["revisions"][0]["id"] == revision["id"]


def test_blocking_is_normalized_and_preserved_across_ai_scene_edits():
    project = prompt_studio.new_project(mode="r2v", duration=10)
    subject = prompt_studio.empty_subject(1)
    project["scene"]["subjects"] = [subject]
    project["scene"]["shots"][0]["blocking"] = [{
        "id": "block_one",
        "subject_id": subject["id"],
        "kind": "subject",
        "label": "Hero",
        "x": 0.9,
        "y": 0.8,
        "width": 0.4,
        "height": 0.5,
        "facing": "left",
        "note": "foreground",
    }]
    project = prompt_studio.normalize_project(project)
    block = project["scene"]["shots"][0]["blocking"][0]
    assert block["x"] == 0.6
    assert block["y"] == 0.5
    assert block["facing"] == "left"

    candidate = copy.deepcopy(project["scene"])
    candidate["shots"][0]["blocking"] = []
    result = prompt_studio.preserve_locks(project["scene"], candidate)
    assert result["shots"][0]["blocking"][0]["id"] == "block_one"


def test_compiler_contract_treats_blocking_as_semantic_not_coordinate_output():
    prompt = prompt_studio.compiler_system_prompt("r2v")
    assert "Blocking is authoritative" in prompt
    assert "Do not mention coordinates" in prompt


def test_exact_shot_count_is_enforced_during_normalization():
    project = prompt_studio.new_project(duration=12)
    project["scene"]["shot_count_mode"] = "exact"
    project["scene"]["exact_shot_count"] = 4
    project["scene"]["shots"] = [prompt_studio.empty_shot(1)]
    project = prompt_studio.normalize_project(project)
    assert len(project["scene"]["shots"]) == 4
    assert [round(x["start_seconds"], 3) for x in project["scene"]["shots"]] == [0.0, 3.0, 6.0, 9.0]
    assert "shot_count_mode=exact" in prompt_studio.planner_system_prompt()


def test_blocking_sketch_strokes_are_normalized_as_ui_only_metadata():
    project = prompt_studio.new_project()
    shot = project["scene"]["shots"][0]
    shot["blocking_sketch"] = [{
        "id": "stroke_1",
        "block_id": "block_1",
        "points": [[-1, 0.25], [0.5, 2], ["bad", 0.4], [0.7, 0.8]],
    }]
    project = prompt_studio.normalize_project(project)
    stroke = project["scene"]["shots"][0]["blocking_sketch"][0]
    assert stroke["block_id"] == "block_1"
    assert stroke["points"] == [[0.0, 0.25], [0.5, 1.0], [0.7, 0.8]]
