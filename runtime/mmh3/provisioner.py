from __future__ import annotations

import json
import threading
import time

from .common import CONFIG_ROOT, DATA_ROOT, IMAGE_ROOT, STATE_ROOT, dump_json, env_bool, load_yaml
from .models import local_model_progress, phase_ready, sync_models
from .loras import sync_loras

_STATE_LOCK = threading.Lock()


def _state(**values):
    with _STATE_LOCK:
        current = {}
        path = STATE_ROOT / "provisioning.json"
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        current.update(values)
        current["updated_at"] = time.time()
        dump_json(path, current)


def _progress_callback(name: str, values: dict) -> None:
    with _STATE_LOCK:
        path = STATE_ROOT / "provisioning.json"
        current = {}
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        progress = current.get("model_progress") if isinstance(current.get("model_progress"), dict) else {}
        row = progress.get(name) if isinstance(progress.get(name), dict) else {}
        row = {**row, **values, "updated_at": time.time()}
        progress[name] = row
        current["model_progress"] = progress
        current["updated_at"] = time.time()
        dump_json(path, current)


def run() -> int:
    runtime = load_yaml(CONFIG_ROOT / "runtime.yaml", {}) or {}
    cfg = runtime.get("provisioning") or {}
    do_models = env_bool("MMH3_AUTO_DOWNLOAD_MODELS", bool(cfg.get("download_models", True)))
    do_loras = env_bool("MMH3_AUTO_DOWNLOAD_LORAS", bool(cfg.get("download_loras", True)))
    manifest = IMAGE_ROOT / "config" / "models.yaml"

    local = local_model_progress(manifest)
    local_core_ready = phase_ready(local, "core")
    local_accelerator_ready = phase_ready(local, "accelerator")
    local_addon_ready = phase_ready(local, "addon")

    _state(
        status="running",
        stage="starting",
        models_enabled=do_models,
        loras_enabled=do_loras,
        core_ready=(local_core_ready if do_models else True),
        accelerator_ready=(local_accelerator_ready if do_models else True),
        addon_ready=(local_addon_ready if do_models else False),
        error=None,
        model_progress=local,
        message=(
            "Existing stock core detected; verifying persistent model files"
            if local_core_ready
            else "Checking persistent model files and provisioning missing core models"
        ),
    )

    core_results = []
    accelerator_results = []
    addon_results = []
    lora_results = {}
    core_ready = not do_models
    accelerator_ready = not do_models
    addon_ready = False

    try:
        if do_models:
            _state(stage="models", message="Checking/downloading stock MiniMax H3 core models")
            core_results = sync_models(manifest, phase="core", progress=_progress_callback)
            core_failed = [x for x in core_results if x.get("status") == "error"]
            core_ready = not core_failed
            _state(
                stage="core_ready" if core_ready else "models_complete",
                models=core_results,
                core_ready=core_ready,
                message=(
                    "Stock H3 core verified; generation unlocked while acceleration profiles finish provisioning"
                    if core_ready
                    else f"{len(core_failed)} stock core model check/download(s) failed"
                ),
            )

            _state(
                stage="accelerators",
                core_ready=core_ready,
                message=(
                    "Stock core ready; checking/downloading Fast and Balanced Turbo profiles"
                    if core_ready
                    else "Checking/downloading Turbo profile files"
                ),
            )
            accelerator_results = sync_models(manifest, phase="accelerator", progress=_progress_callback)
            accelerator_failed = [x for x in accelerator_results if x.get("status") == "error"]
            accelerator_ready = not accelerator_failed and bool(accelerator_results)
            _state(
                stage="accelerators_complete",
                core_ready=core_ready,
                accelerator_ready=accelerator_ready,
                accelerator_models=accelerator_results,
                message=(
                    "Fast and Balanced Turbo profiles ready"
                    if accelerator_ready
                    else f"{len(accelerator_failed)} Turbo profile file(s) still need attention"
                ),
            )
        else:
            core_ready = True
            accelerator_ready = True

        if do_loras:
            _state(
                stage="loras",
                core_ready=core_ready,
                accelerator_ready=accelerator_ready,
                message="Indexing user-managed LoRAs (only auto_download entries download at startup)",
            )
            lora_results = sync_loras(CONFIG_ROOT / "loras.yaml")
            _state(
                stage="loras_complete",
                loras=lora_results,
                core_ready=core_ready,
                accelerator_ready=accelerator_ready,
            )

        if do_models:
            _state(
                stage="addons",
                core_ready=core_ready,
                accelerator_ready=accelerator_ready,
                message="Downloading/checking Eros Max Beta5 INT8 last",
            )
            addon_results = sync_models(manifest, phase="addon", progress=_progress_callback)
            addon_failed = [x for x in addon_results if x.get("status") == "error"]
            addon_ready = not addon_failed and bool(addon_results)
            _state(
                stage="addons_complete",
                core_ready=core_ready,
                accelerator_ready=accelerator_ready,
                addon_ready=addon_ready,
                addon_models=addon_results,
                message=(
                    "Eros Max Beta5 INT8 ready"
                    if addon_ready
                    else (f"{len(addon_failed)} optional checkpoint download(s) failed" if addon_failed else "Optional model provisioning complete")
                ),
            )

        all_models = core_results + accelerator_results + addon_results
        core_failed = [x for x in core_results if x.get("status") == "error"]
        accelerator_failed = [x for x in accelerator_results if x.get("status") == "error"]
        addon_failed = [x for x in addon_results if x.get("status") == "error"]
        optional_failed = accelerator_failed + addon_failed

        if core_ready and not optional_failed:
            status = "ready"
        elif core_ready:
            status = "ready_with_optional_errors"
        else:
            status = "degraded"

        changed = any(x.get("status") == "downloaded" for x in all_models)
        _state(
            status=status,
            stage="complete",
            core_ready=core_ready,
            accelerator_ready=accelerator_ready,
            addon_ready=addon_ready,
            models=all_models,
            loras=lora_results,
            models_changed=changed,
            message=(
                "Provisioning complete"
                if status == "ready"
                else "Stock core ready; one or more profile/addon files will be retried"
                if core_ready
                else "Provisioning completed with core model errors"
            ),
        )
        dump_json(DATA_ROOT / "provisioning_report.json", {
            "status": status,
            "core_ready": core_ready,
            "accelerator_ready": accelerator_ready,
            "addon_ready": addon_ready,
            "models": all_models,
            "loras": lora_results,
            "models_changed": changed,
            "completed_at": time.time(),
        })

        if not core_ready:
            return 2
        if optional_failed:
            return 3
        return 0
    except Exception as exc:
        _state(
            status="degraded" if core_ready else "error",
            stage="failed",
            core_ready=core_ready,
            accelerator_ready=accelerator_ready,
            addon_ready=addon_ready,
            error=repr(exc),
            message=(
                "Provisioning hit a transient error after stock core became ready; supervisor will retry"
                if core_ready
                else "Provisioning failed; supervisor will retry"
            ),
        )
        print("[mmh3] provisioner failed:", repr(exc), flush=True)
        return 3 if core_ready else 1


if __name__ == "__main__":
    raise SystemExit(run())
