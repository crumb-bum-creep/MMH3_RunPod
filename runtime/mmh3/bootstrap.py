from __future__ import annotations

import json
import shutil
import subprocess

import yaml

from .common import (
    CONFIG_ROOT,
    DATA_ROOT,
    IMAGE_ROOT,
    STATE_ROOT,
    ensure_dirs,
    load_yaml,
    dump_json,
    COMFY_DIR,
    COMFY_PERSIST,
)
from .hardware import detect, select_profile
from .comfy import configure_persistent_paths
from .models import local_model_progress, phase_ready


def _deep_merge(defaults, overrides):
    if not isinstance(defaults, dict) or not isinstance(overrides, dict):
        return overrides
    out = dict(defaults)
    for key, value in overrides.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _migrate_runtime_config(src, dst) -> None:
    defaults = load_yaml(src, {}) or {}
    current = load_yaml(dst, {}) or {} if dst.exists() else {}
    try:
        old_version = int(current.get("version") or 1)
    except (TypeError, ValueError):
        old_version = 1

    # v2 temporarily returned Comfy to dynamic-VRAM mode. That landed in the
    # same refactor wave as the Advanced sampler work and changed the execution
    # environment from the last known-good Fast/Balanced profile build.
    if old_version < 2:
        current.setdefault("comfy", {})["disable_dynamic_vram"] = False
        current["version"] = 2

    # v3 restores the last known-good generation environment for reproducibility.
    # Users can still turn Dynamic VRAM back on from System after migration if
    # host-RAM retention becomes more important than exact-repeat behavior.
    if old_version < 3:
        current.setdefault("comfy", {})["disable_dynamic_vram"] = True
        current["version"] = 3

    merged = _deep_merge(defaults, current)
    if not dst.exists() or merged != (load_yaml(dst, {}) or {}):
        if dst.exists() and old_version < 3:
            backup = dst.with_name(f"{dst.name}.v{old_version}.bak")
            if not backup.exists():
                shutil.copy2(dst, backup)
        dst.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")


def copy_default_configs() -> None:
    # Defaults evolve with the image; persistent user values override them.
    # runtime.yaml is schema-migrated instead of being frozen forever at the
    # version that happened to create the persistent volume.
    src = IMAGE_ROOT / "config"
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    runtime_src = src / "runtime.yaml"
    if runtime_src.exists():
        _migrate_runtime_config(runtime_src, CONFIG_ROOT / "runtime.yaml")

    for name in ("loras.yaml", "system_prompts.yaml"):
        p = src / name
        dst = CONFIG_ROOT / name
        if p.exists() and not dst.exists():
            shutil.copy2(p, dst)


def install_workflows() -> list[str]:
    src = IMAGE_ROOT / "workflows" / "api"
    dst = COMFY_PERSIST / "user" / "default" / "workflows" / "MMH3"
    dst.mkdir(parents=True, exist_ok=True)
    installed = []
    if src.exists():
        for p in src.glob("*.json"):
            shutil.copy2(p, dst / p.name)
            installed.append(p.name)
    return installed


def custom_node_report() -> list[dict]:
    cfg = load_yaml(IMAGE_ROOT / "config" / "custom_nodes.yaml", {}) or {}
    out = []
    for name, item in (cfg.get("custom_nodes") or {}).items():
        path = COMFY_DIR / "custom_nodes" / name
        expected = str((item or {}).get("commit", ""))
        try:
            marker = path / ".mmh3_commit"
            if marker.is_file():
                actual = marker.read_text(encoding="utf-8").strip()
            else:
                actual = subprocess.check_output(
                    ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
                ).strip()
            status = "ok" if actual == expected else "drift"
        except Exception:
            actual = ""
            status = "missing"
        out.append({"name": name, "expected": expected, "actual": actual, "status": status})
    return out


def main() -> int:
    ensure_dirs()
    copy_default_configs()
    configure_persistent_paths()

    hardware = detect()
    profile_name, profile = select_profile(
        hardware, IMAGE_ROOT / "config" / "hardware_profiles.yaml"
    )
    runtime = load_yaml(CONFIG_ROOT / "runtime.yaml", {}) or {}
    memory_cfg = dict(runtime.get("memory") or {})
    memory_cfg.update((profile.get("memory") or {}))

    manifest = IMAGE_ROOT / "config" / "models.yaml"
    model_progress = local_model_progress(manifest)
    core_ready = phase_ready(model_progress, "core")
    accelerator_ready = phase_ready(model_progress, "accelerator")
    addon_ready = phase_ready(model_progress, "addon")

    dump_json(STATE_ROOT / "hardware.json", {**hardware.to_dict(), "profile": profile_name})
    dump_json(STATE_ROOT / "effective_memory_policy.json", memory_cfg)
    dump_json(STATE_ROOT / "provisioning.json", {
        "status": "pending_verification",
        "stage": "local_core_ready" if core_ready else "waiting_for_background_provisioner",
        "core_ready": core_ready,
        "accelerator_ready": accelerator_ready,
        "addon_ready": addon_ready,
        "model_progress": model_progress,
        "message": (
            "Existing stock core detected; generation may start while background verification runs"
            if core_ready
            else "Core models are missing or incomplete; background provisioning will repair them"
        ),
    })

    results = {
        "hardware": {**hardware.to_dict(), "profile": profile_name},
        "custom_nodes": custom_node_report(),
        "workflows": install_workflows(),
        "provisioning": {
            "mode": "deferred_to_background",
            "local_core_ready": core_ready,
            "local_accelerator_ready": accelerator_ready,
            "local_addon_ready": addon_ready,
        },
    }
    dump_json(DATA_ROOT / "boot_report.json", results)

    print("=== MMH3 FAST BOOTSTRAP REPORT ===")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
