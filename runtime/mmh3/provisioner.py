from __future__ import annotations

import json
import time
from pathlib import Path

from .common import CONFIG_ROOT, DATA_ROOT, IMAGE_ROOT, STATE_ROOT, dump_json, env_bool, load_yaml
from .models import sync_models
from .loras import sync_loras


def _state(**values):
    current = {}
    path = STATE_ROOT / "provisioning.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    current.update(values)
    current["updated_at"] = time.time()
    dump_json(path, current)


def run() -> None:
    runtime = load_yaml(CONFIG_ROOT / "runtime.yaml", {}) or {}
    cfg = runtime.get("provisioning") or {}
    do_models = env_bool("MMH3_AUTO_DOWNLOAD_MODELS", bool(cfg.get("download_models", True)))
    do_loras = env_bool("MMH3_AUTO_DOWNLOAD_LORAS", bool(cfg.get("download_loras", True)))

    _state(
        status="running",
        stage="starting",
        models_enabled=do_models,
        loras_enabled=do_loras,
        core_ready=not do_models,
        error=None,
    )

    model_results = []
    lora_results = {}
    try:
        if do_models:
            _state(stage="models", message="Checking/downloading MiniMax H3 core models")
            model_results = sync_models(IMAGE_ROOT / "config" / "models.yaml")
            failed = [x for x in model_results if x.get("status") == "error"]
            core_ready = not failed
            _state(
                stage="models_complete",
                models=model_results,
                core_ready=core_ready,
                message=("Core models ready" if core_ready else f"{len(failed)} core model download(s) failed"),
            )
        else:
            core_ready = True

        if do_loras:
            _state(stage="loras", message="Checking/downloading managed LoRAs")
            lora_results = sync_loras(CONFIG_ROOT / "loras.yaml")
            _state(stage="loras_complete", loras=lora_results)

        status = "ready" if core_ready else "degraded"
        _state(
            status=status,
            stage="complete",
            core_ready=core_ready,
            models=model_results,
            loras=lora_results,
            message=("Provisioning complete" if core_ready else "Provisioning completed with core model errors"),
        )
        dump_json(DATA_ROOT / "provisioning_report.json", {
            "status": status,
            "models": model_results,
            "loras": lora_results,
            "completed_at": time.time(),
        })
    except Exception as exc:
        _state(status="error", stage="failed", core_ready=False, error=repr(exc), message="Provisioning failed")
        print("[mmh3] provisioner failed:", repr(exc), flush=True)


if __name__ == "__main__":
    run()
