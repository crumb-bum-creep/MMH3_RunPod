from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .common import COMFY_PERSIST, CONFIG_ROOT, DATA_ROOT, IMAGE_ROOT, STATE_ROOT, dump_json, env_bool, load_yaml
from .models import sync_models
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


def _initial_model_progress(config_path: Path) -> dict[str, dict]:
    cfg = load_yaml(config_path, {}) or {}
    entries = (cfg.get("models") or {})
    root = COMFY_PERSIST / "models"
    out: dict[str, dict] = {}
    for name, raw in entries.items():
        item = raw or {}
        dest_rel = str(item.get("destination") or "")
        dest = root / dest_rel
        minimum = int(float(item.get("min_size_mb", 1)) * 1024 * 1024)
        try:
            existing = dest.stat().st_size if dest.is_file() else 0
        except OSError:
            existing = 0
        ready = existing >= minimum
        out[str(name)] = {
            "label": str(item.get("label") or name),
            "destination": dest_rel,
            "phase": str(item.get("phase") or "core"),
            "show_in_ui": bool(item.get("show_in_ui", not dest_rel.startswith("loras/"))),
            "status": "ready" if ready else "waiting",
            "downloaded_bytes": existing if ready else 0,
            "total_bytes": existing if ready else None,
            "speed_bps": 0.0,
        }
    return out


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

    _state(
        status="running",
        stage="starting",
        models_enabled=do_models,
        loras_enabled=do_loras,
        core_ready=not do_models,
        addon_ready=False,
        error=None,
        model_progress=_initial_model_progress(manifest),
    )

    core_results = []
    addon_results = []
    lora_results = {}
    core_ready = not do_models
    try:
        if do_models:
            _state(stage="models", message="Checking/downloading stock MiniMax H3 core models")
            core_results = sync_models(manifest, phase="core", progress=_progress_callback)
            failed = [x for x in core_results if x.get("status") == "error"]
            core_ready = not failed
            _state(
                stage="core_ready" if core_ready else "models_complete",
                models=core_results,
                core_ready=core_ready,
                message=(
                    "Stock H3 core models ready; generation unlocked while optional provisioning continues"
                    if core_ready
                    else f"{len(failed)} stock core model download(s) failed"
                ),
            )
        else:
            core_ready = True

        if do_loras:
            _state(
                stage="loras",
                core_ready=core_ready,
                message=(
                    "Stock core ready; checking/downloading managed LoRAs"
                    if core_ready
                    else "Checking/downloading managed LoRAs"
                ),
            )
            lora_results = sync_loras(CONFIG_ROOT / "loras.yaml")
            _state(stage="loras_complete", loras=lora_results, core_ready=core_ready)

        if do_models:
            _state(
                stage="addons",
                core_ready=core_ready,
                message=(
                    "Stock core ready; downloading Eros Max Beta5 INT8 last"
                    if core_ready
                    else "Downloading optional checkpoint addons"
                ),
            )
            addon_results = sync_models(manifest, phase="addon", progress=_progress_callback)
            addon_failed = [x for x in addon_results if x.get("status") == "error"]
            addon_ready = not addon_failed and bool(addon_results)
            _state(
                stage="addons_complete",
                core_ready=core_ready,
                addon_ready=addon_ready,
                addon_models=addon_results,
                message=(
                    "Eros Max Beta5 INT8 ready"
                    if addon_ready
                    else (f"{len(addon_failed)} optional model download(s) failed" if addon_failed else "Optional model provisioning complete")
                ),
            )
        else:
            addon_ready = False

        all_models = core_results + addon_results
        addon_failed = [x for x in addon_results if x.get("status") == "error"]
        status = "ready" if core_ready and not addon_failed else "ready_with_addon_errors" if core_ready else "degraded"
        _state(
            status=status,
            stage="complete",
            core_ready=core_ready,
            addon_ready=addon_ready,
            models=all_models,
            loras=lora_results,
            message=(
                "Provisioning complete"
                if status == "ready"
                else "Stock core ready; one or more optional models failed"
                if core_ready
                else "Provisioning completed with core model errors"
            ),
        )
        dump_json(DATA_ROOT / "provisioning_report.json", {
            "status": status,
            "core_ready": core_ready,
            "addon_ready": addon_ready,
            "models": all_models,
            "loras": lora_results,
            "completed_at": time.time(),
        })
        return 0 if core_ready else 2
    except Exception as exc:
        _state(
            status="degraded" if core_ready else "error",
            stage="failed",
            core_ready=core_ready,
            error=repr(exc),
            message=(
                "Optional provisioning failed after stock core became ready"
                if core_ready
                else "Provisioning failed"
            ),
        )
        print("[mmh3] provisioner failed:", repr(exc), flush=True)
        return 0 if core_ready else 1


if __name__ == "__main__":
    raise SystemExit(run())
