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
