from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_supervisor_prioritizes_phone_and_comfy_before_jupyter():
    source = (ROOT / "runtime" / "mmh3" / "supervisor.py").read_text()
    phone = source.index("phone_proc = start_phone()")
    comfy = source.index("comfy_proc = start_comfy()")
    provision = source.index("provision_proc = start_provisioner()")
    jupyter = source.index("jupyter_proc = start_jupyter()")
    assert phone < provision
    assert comfy < provision
    assert provision < jupyter
    assert 'warm_start = bool(bootstrap_state.get("core_ready"))' in source
    assert 'provisioner_start_mode="warm"' in source
    assert 'provisioner_start_mode="cold"' in source
    assert 'warm_start_grace_seconds' in source
    assert 'comfy.missing_ready_model_choices' in source
    assert 'comfy_model_visibility_repair=True' in source
    assert source.index("comfy.missing_ready_model_choices") < source.index('provisioner_start_mode="warm"')
    assert 'cfg.get("disable_dynamic_vram", False)' in source


def test_warm_lora_sync_avoids_network_metadata_roundtrip():
    source = (ROOT / "runtime" / "mmh3" / "loras.py").read_text()
    assert '"metadata_source": "persistent"' in source
    assert "existing_by_id" in source
    assert "dest.stat().st_size >= 1024 * 1024" in source


def test_lora_empty_user_metadata_is_authoritative():
    source = (ROOT / "runtime" / "mmh3" / "loras.py").read_text()
    assert 'override["trigger_words"] if "trigger_words" in override' in source
    assert 'override["notes"] if "notes" in override' in source
    assert 'override["tags"] if "tags" in override' in source


def test_commit_markers_replace_runtime_git_requirement():
    bootstrap = (ROOT / "runtime" / "mmh3" / "bootstrap.py").read_text()
    boot_check = (ROOT / "scripts" / "first_boot_check.sh").read_text()
    assert 'marker = path / ".mmh3_commit"' in bootstrap
    assert "/ComfyUI/.mmh3_commit" in boot_check


def test_production_image_drops_manager_and_git_metadata():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "FROM ubuntu:24.04 AS runtime" in dockerfile
    assert "rm -rf /ComfyUI/custom_nodes/comfyui-manager" in dockerfile
    assert "rm -rf /ComfyUI/.git /ComfyUI/custom_nodes/*/.git" in dockerfile
    assert "COPY scripts/split_runtime_layers.py /tmp/split_runtime_layers.py" in dockerfile
    assert "COPY --from=builder /opt/mmh3-layer/venv-1/ /" in dockerfile
    assert "COPY --from=builder /opt/mmh3-layer/venv-2/ /" in dockerfile
    assert "COPY --from=builder /opt/mmh3-layer/venv-3/ /" in dockerfile
    assert "COPY --from=builder /opt/mmh3-layer/nvidia-1/ /" in dockerfile
    assert "COPY --from=builder /opt/mmh3-layer/nvidia-2/ /" in dockerfile
    assert "COPY --from=builder /opt/mmh3-layer/nvidia-3/ /" in dockerfile


def test_ops_exposes_doctor_and_gpu_smoke():
    source = (ROOT / "scripts" / "mmh3").read_text()
    assert "  doctor)" in source
    assert "  gpu-smoke)" in source
    assert "GPU smoke: PASS" in source
    assert "Dynamic VRAM:" in source
    assert "Memory protection:" in source
    assert "Comfy model visibility:" in source
