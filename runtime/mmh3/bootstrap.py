from __future__ import annotations

import json
import shutil
import subprocess

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


def copy_default_configs() -> None:
    # User-editable configuration is initialized once and then survives image upgrades.
    src = IMAGE_ROOT / "config"
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    for name in ("runtime.yaml", "loras.yaml", "system_prompts.yaml"):
        p = src / name
        dst = CONFIG_ROOT / name
        if p.exists() and not dst.exists():
            shutil.copy2(p, dst)


def install_workflows() -> list[str]:
    """Prepare the persistent workflow folder without installing API prompt JSON.

    API-format graphs are queue payloads, not drawable LiteGraph workflows.
    Real ComfyUI workflows are generated after Comfy exposes /object_info.
    """
    dst = COMFY_PERSIST / "user" / "default" / "workflows" / "MMH3"
    dst.mkdir(parents=True, exist_ok=True)
    legacy = (
        "t2v_auto.json", "t2v_custom.json",
        "i2v_auto.json", "i2v_custom.json",
        "r2v_auto.json", "r2v_custom.json",
    )
    removed = []
    for name in legacy:
        path = dst / name
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                value = None
            if isinstance(value, dict) and "nodes" not in value:
                path.unlink()
                removed.append(name)
    return [f"drawable workflows generated after Comfy startup; removed legacy={len(removed)}"]

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

    dump_json(STATE_ROOT / "hardware.json", {**hardware.to_dict(), "profile": profile_name})
    dump_json(STATE_ROOT / "effective_memory_policy.json", memory_cfg)
    dump_json(STATE_ROOT / "provisioning.json", {
        "status": "pending",
        "stage": "waiting_for_background_provisioner",
        "core_ready": False,
    })

    results = {
        "hardware": {**hardware.to_dict(), "profile": profile_name},
        "custom_nodes": custom_node_report(),
        "workflows": install_workflows(),
        "provisioning": "deferred_to_background",
    }
    dump_json(DATA_ROOT / "boot_report.json", results)

    print("=== MMH3 FAST BOOTSTRAP REPORT ===")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
