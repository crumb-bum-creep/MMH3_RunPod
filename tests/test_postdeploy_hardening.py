from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_tini_runs_as_subreaper():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert 'ENTRYPOINT ["/usr/bin/tini", "-s", "--", "/opt/mmh3/runtime/entrypoint.sh"]' in dockerfile


def test_provisioner_module_executes_and_returns_status():
    source = (ROOT / "runtime" / "mmh3" / "provisioner.py").read_text()
    assert "def run() -> int:" in source
    assert "raise SystemExit(run())" in source
    assert "if not core_ready:" in source
    assert "return 2" in source
    assert "if optional_failed:" in source
    assert "return 3" in source
    assert "return 1" in source


def test_default_lora_seed_is_reconstructable():
    cfg = yaml.safe_load((ROOT / "config" / "loras.yaml").read_text()) or {}
    items = cfg.get("loras") or []
    assert len(items) == 18
    ids = [int(x["version_id"]) for x in items]
    assert len(ids) == len(set(ids))
    for item in items:
        assert item.get("enabled", True) is True
        assert str(item.get("filename") or "").endswith(".safetensors")
        assert item.get("nickname")
        assert "recommended_strength" in item


def test_output_poll_preserves_active_video_elements():
    source = (ROOT / "services" / "phone-ui" / "static" / "app.js").read_text()
    assert 'const playing=$$("video",host).some(v=>!v.paused&&!v.ended);' in source
    assert "if(!force && playing) return;" in source
    assert "if(signature===state.outputsSignature) return;" in source
    assert 'setInterval(()=>{if(state.tab==="outputs")refreshOutputs(false);},15000);' in source


def test_proxy_html_is_not_dumped_into_phone_ui():
    source = (ROOT / "services" / "phone-ui" / "static" / "app.js").read_text()
    assert 'contentType.includes("text/html")' in source
    assert "RunPod proxy could not reach the MMH3 service" in source


def test_recovery_helpers_are_exposed():
    source = (ROOT / "scripts" / "mmh3").read_text()
    assert "  provision)" in source
    assert "  jupyter-url)" in source
