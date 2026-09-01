from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "services" / "phone-ui" / "static" / "app.js").read_text()
HTML = (ROOT / "services" / "phone-ui" / "static" / "index.html").read_text()
SERVER = (ROOT / "services" / "phone-ui" / "server.py").read_text()
CSS = (ROOT / "services" / "phone-ui" / "static" / "styles.css").read_text()


def test_six_generation_drafts_are_persistent_and_separate():
    assert 'return mode+":"+promptMode' in APP
    assert 'localStorage.setItem("mmh3.uiState.v2"' in APP
    assert 'api("/api/ui-state",{method:"PUT"' in APP
    assert 'web.get("/api/ui-state", api_ui_state_get)' in SERVER
    assert 'web.put("/api/ui-state", api_ui_state_put)' in SERVER
    for mode in ("t2v", "i2v", "r2v"):
        assert mode in SERVER
    for prompt_mode in ("auto", "custom"):
        assert prompt_mode in SERVER
    assert 'id="clearDraft"' in HTML


def test_generation_lora_picker_is_separate_from_manager():
    assert 'id="addGenerationLora"' in HTML
    assert 'id="goLoras"' in HTML
    assert 'class="gen-lora-select"' in APP
    assert 'class="pick"' not in APP
    assert 'id="loraSort"' in HTML
    assert 'value="alpha"' in HTML
    assert ".compact-lora" in CSS


def test_assets_have_persistent_nicknames_and_visual_picker():
    assert 'ASSET_META_FILE = DATA_ROOT / "assets.json"' in SERVER
    assert 'web.put("/api/assets/meta", api_asset_meta_put)' in SERVER
    assert 'web.get("/media/input-thumb/{path:.*}", serve_input_thumb)' in SERVER
    assert 'id="tab-assets"' in HTML
    assert 'id="assetPicker"' in HTML
    assert 'id="chooseStartAsset"' in HTML
    assert 'id="chooseExistingRef"' in HTML
    assert "assetThumb(file)" in APP
    assert ".asset-thumb" in CSS


def test_r2v_reference_labels_and_reordering_are_explicit():
    assert 'return "<"+letter+ordinal+">"' in APP
    assert "moveRefWithinKind" in APP
    assert "move-up" in APP
    assert "move-down" in APP
    assert 'class="ref-slot"' in APP


def test_queue_identifies_running_and_pending_positions():
    assert '"label": "RUNNING"' in SERVER
    assert 'f"NEXT #{position}"' in SERVER
    assert 'class="queue-badge' in APP
    assert '"Stop":"Cancel"' in APP


def test_prompt_studio_is_separate_from_generation_and_phone_friendly():
    assert 'data-tab="studio"' in HTML
    assert 'id="tab-studio"' in HTML
    assert 'id="studioProjectSelect"' in HTML
    assert 'id="studioSubjects"' in HTML
    assert 'id="studioShots"' in HTML
    assert 'id="studioUseCustom"' in HTML
    assert 'web.get("/api/prompt-studio/projects", api_studio_projects_get)' in SERVER
    assert 'web.post("/api/prompt-studio/projects/{project_id}/plan", api_studio_plan)' in SERVER
    assert 'web.post("/api/prompt-studio/projects/{project_id}/edit", api_studio_edit)' in SERVER
    assert 'web.post("/api/prompt-studio/projects/{project_id}/compile", api_studio_compile)' in SERVER
    assert 'google/gemini-3-flash-preview' in APP
    assert 'reference.mode=picture_slot' in (ROOT / "runtime" / "mmh3" / "prompt_studio.py").read_text()
    assert 'nav{grid-template-columns:repeat(4,1fr)}' in CSS


def test_prompt_studio_director_controls_and_collection_selectors():
    assert 'id="studioTimeline"' in HTML
    assert 'id="studioShotCount"' in HTML
    assert 'id="studioShotCountMode"' in HTML
    assert 'id="studioBlockingDialog"' in HTML
    assert 'class="studio-framing-grid"' in APP
    assert 'function renderStudioTimeline' in APP
    assert 'function renderStudioBlocking' in APP
    assert 'function framingIcon' in APP
    assert '$$(".studio-subject-card",host).forEach' in APP
    assert '$$(".studio-shot-card",host).forEach' in APP
    assert '$$(".studio-chip input",card).forEach' in APP
    assert '$$("[data-studio-scene-lock]").forEach' in APP
    assert '$$("[data-studio-edit-field]").forEach' in APP
    assert '.studio-blocking-frame' in CSS


def test_prompt_studio_freehand_sketch_is_subject_tagged_and_ui_only():
    assert 'id="studioBlockingDrawToggle"' in HTML
    assert 'id="studioBlockingUndoSketch"' in HTML
    assert 'id="studioBlockingClearSketch"' in HTML
    assert 'function renderStudioSketchCanvas' in APP
    assert 'function wireStudioSketchCanvas' in APP
    assert 'block_id:block.id' in APP
    assert 'fitStudioBlockToSketch' in APP
    assert '.studio-blocking-canvas.drawing' in CSS
    assert '_studio_llm_scene' in SERVER


def test_prompt_studio_preflight_is_visible_and_gates_openrouter_actions():
    assert 'id="studioPreflight"' in HTML
    assert 'id="studioPreflightBadge"' in HTML
    assert 'id="studioPreflightMsg"' in HTML
    assert 'function runStudioPreflight' in APP
    assert 'await runStudioPreflight(p,false)' in APP
    assert 'Fix the preflight issues above before calling the planner.' in APP
    assert 'Fix the preflight issues above before compiling.' in APP
    assert 'web.get("/api/prompt-studio/projects/{project_id}/preflight", api_studio_preflight)' in SERVER
    assert '.studio-preflight-error' in CSS


def test_prompt_studio_model_check_is_project_scoped_and_text_only():
    assert 'id="studioTestModel"' in HTML
    assert 'id="studioModelStatus"' in HTML
    assert 'async function testStudioModel' in APP
    assert '"/model-check"' in APP
    assert 'web.post("/api/prompt-studio/projects/{project_id}/model-check", api_studio_model_check)' in SERVER
    assert 'Connectivity check.' in SERVER
    assert '.studio-model-row' in CSS
