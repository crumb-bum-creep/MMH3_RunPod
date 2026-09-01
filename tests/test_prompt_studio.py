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
