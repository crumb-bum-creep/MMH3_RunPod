from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time

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


# sha256 of every auto-prompt default this image has previously shipped
# (stripped text). A persistent prompt matching one of these was never edited
# by the user, so it is safe to replace with the current default.
SHIPPED_PROMPT_SHA256 = {
    "t2v_auto": {"4ff6ce0e92e98a340fee53f400900e8fd6e732900e51b0816cc99c9bde0e9cee"},
    "i2v_auto": {"2056c438d624c8685e91f3dd6b2d210f55aefc7fd8b01069b470c76d5f9a540c"},
    "r2v_auto": {"25808412e4d07d476e1f8b057ec1daac94ce1c568fbea6071a4ae05001d10708"},
}


def _prompt_sha(text: str) -> str:
    return hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()


def upgrade_default_prompts() -> list[str]:
    """Move unedited persistent auto prompts to the image's current defaults.

    Prompts the user customised in the UI are left untouched (the System tab
    offers a "Load defaults" button for those). The previous file is kept as a
    timestamped backup whenever something changes.
    """
    src = IMAGE_ROOT / "config" / "system_prompts.yaml"
    dst = CONFIG_ROOT / "system_prompts.yaml"
    if not src.exists() or not dst.exists():
        return []
    defaults = (load_yaml(src, {}) or {}).get("prompts") or {}
    current_cfg = load_yaml(dst, {}) or {}
    current = dict(current_cfg.get("prompts") or {})
    upgraded = []
    for key, text in defaults.items():
        old = current.get(key)
        if old is None or (
            _prompt_sha(old) != _prompt_sha(text) and _prompt_sha(old) in SHIPPED_PROMPT_SHA256.get(key, set())
        ):
            current[key] = text
            upgraded.append(key)
    if upgraded:
        shutil.copy2(dst, dst.with_name(f"system_prompts.yaml.bak-{int(time.time())}"))
        dst.write_text(
            yaml.safe_dump({**current_cfg, "prompts": current}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    return upgraded


def copy_default_configs() -> None:
    # User-editable configuration is initialized once and then survives image upgrades.
    src = IMAGE_ROOT / "config"
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    for name in ("runtime.yaml", "loras.yaml", "system_prompts.yaml"):
        p = src / name
        dst = CONFIG_ROOT / name
        if p.exists() and not dst.exists():
            shutil.copy2(p, dst)
    try:
        upgrade_default_prompts()
    except Exception:
        # A malformed user prompt file must never block boot.
        pass


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
