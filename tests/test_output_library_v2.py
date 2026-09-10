from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PHONE = ROOT / "services" / "phone-ui"


def _load_indexer(monkeypatch, tmp_path):
    monkeypatch.setenv("MMH3_COMFY_PERSIST", str(tmp_path / "ComfyUI"))
    monkeypatch.setenv("MMH3_DATA_ROOT", str(tmp_path / "mmh3" / "data"))
    spec = importlib.util.spec_from_file_location("mmh3_test_output_indexer", PHONE / "output_indexer.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_output_indexer_only_indexes_final_audio_and_finds_preview(tmp_path, monkeypatch):
    indexer = _load_indexer(monkeypatch, tmp_path)
    out = tmp_path / "ComfyUI" / "output" / "MMH3" / "T2V"
    out.mkdir(parents=True)
    (out / "T2V_00001.mp4").write_bytes(b"raw")
    (out / "T2V_00001-audio.mp4").write_bytes(b"muxed")
    (out / "T2V_00001.png").write_bytes(b"preview")
    (out / "T2V_00001-audio.mp4.h3.json").write_text('{"mode":"t2v"}', encoding="utf-8")
    director = tmp_path / "ComfyUI" / "output" / "MMH3Director"
    director.mkdir(parents=True)
    (director / "ignored-audio.mp4").write_bytes(b"ignored")

    result = indexer.build_index()
    assert result["count"] == 1
    item = result["items"][0]
    assert item["file"] == "MMH3/T2V/T2V_00001-audio.mp4"
    assert item["preview_file"] == "MMH3/T2V/T2V_00001.png"
    assert item["metadata"]["mode"] == "t2v"


def test_library_v2_static_contracts():
    js = (PHONE / "static" / "library-v2.js").read_text(encoding="utf-8")
    css = (PHONE / "static" / "library-v2.css").read_text(encoding="utf-8")
    wrapper = (PHONE / "server_v2.py").read_text(encoding="utf-8")
    entrypoint = (ROOT / "runtime" / "entrypoint.sh").read_text(encoding="utf-8")

    for token in ("outputFavoritesV2", "outputGroupFilterV2", "outputTagFilterV2", "globalProgressHud", "clearCacheOnly"):
        assert token in js
    assert "legacyHost.replaceWith(sink)" in js
    assert "refreshOutputs=async(force=false)=>load(force)" in js
    assert "?since=" in js
    assert "grid-template-columns:repeat(2" in css
    assert "base.api_outputs = api_outputs" in wrapper
    assert 'app.router.add_put("/api/output-library"' in wrapper
    assert '"unchanged": True' in wrapper
    assert "server_legacy.py" in entrypoint and "server_v2.py" in entrypoint
