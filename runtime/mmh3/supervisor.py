from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time

from . import comfy
from .common import CONFIG_ROOT, IMAGE_ROOT, LOG_ROOT, STATE_ROOT, COMFY_DIR, dump_json, load_yaml
from .hardware import detect, select_profile
from .memory_guard import run as run_memory_guard

STOP = threading.Event()

try:
    START_EPOCH = float(os.environ.get("MMH3_CONTAINER_START_EPOCH") or time.time())
except ValueError:
    START_EPOCH = time.time()


def startup_mark(**values) -> None:
    path = STATE_ROOT / "startup.json"
    current = {}
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    current.update(values)
    current["elapsed_seconds"] = round(time.time() - START_EPOCH, 3)
    current["updated_at"] = time.time()
    dump_json(path, current)

def log_file(name: str):
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    return (LOG_ROOT / name).open("ab", buffering=0)

def start_comfy() -> subprocess.Popen:
    # Re-assert persistent paths immediately before every Comfy launch. This
    # prevents migration/warm-start races where Comfy indexes the image-local
    # model tree before /workspace paths are finalized.
    comfy.configure_persistent_paths()
    runtime = load_yaml(CONFIG_ROOT / "runtime.yaml", {}) or {}
    hw = detect()
    _, profile = select_profile(hw, IMAGE_ROOT / "config" / "hardware_profiles.yaml")
    cfg = runtime.get("comfy") or {}
    args = [
        sys.executable,
        str(COMFY_DIR / "main.py"),
        "--listen", "0.0.0.0",
        "--port", os.environ.get("MMH3_COMFY_PORT", "8188"),
        "--extra-model-paths-config", str(COMFY_DIR / "extra_model_paths.yaml"),
    ]
    if cfg.get("cors"):
        args += ["--enable-cors-header", str(cfg["cors"])]
    if cfg.get("use_sage_attention", True):
        args.append("--use-sage-attention")
    if cfg.get("disable_dynamic_vram", True):
        args.append("--disable-dynamic-vram")

    reserve = float(((profile.get("comfy") or {}).get("reserve_vram_gb")) or 0)
    if reserve > 0:
        args += ["--reserve-vram", str(reserve)]

    env = os.environ.copy()
    tcmalloc = subprocess.run(
        "ldconfig -p | grep -Po 'libtcmalloc.so.\\d' | head -n1",
        shell=True, text=True, capture_output=True
    ).stdout.strip()
    if tcmalloc:
        env["LD_PRELOAD"] = tcmalloc
    return subprocess.Popen(
        args, cwd=COMFY_DIR, env=env,
        stdout=log_file("comfyui.log"), stderr=subprocess.STDOUT
    )

def start_jupyter() -> subprocess.Popen | None:
    runtime = load_yaml(CONFIG_ROOT / "runtime.yaml", {}) or {}
    if not (runtime.get("services") or {}).get("start_jupyter", True):
        return None
    args = [
        "jupyter-lab", "--ip=0.0.0.0", "--allow-root", "--no-browser",
        "--ServerApp.allow_origin=*",
        "--ServerApp.allow_credentials=True",
        "--notebook-dir=/workspace",
        "--ServerApp.port=" + os.environ.get("MMH3_JUPYTER_PORT", "8888"),
    ]
    token = os.environ.get("JUPYTER_TOKEN", "").strip()
    if token:
        args += ["--IdentityProvider.token=" + token]
    else:
        args += ["--IdentityProvider.token=", "--ServerApp.password="]
        print("[mmh3] WARNING: JUPYTER_TOKEN is not set; Jupyter will be unauthenticated.", flush=True)
    return subprocess.Popen(args, stdout=log_file("jupyter.log"), stderr=subprocess.STDOUT)

def start_provisioner() -> subprocess.Popen:
    env = os.environ.copy()
    return subprocess.Popen(
        [sys.executable, "-m", "mmh3.provisioner"],
        cwd=IMAGE_ROOT / "runtime",
        env=env,
        stdout=log_file("provisioning.log"),
        stderr=subprocess.STDOUT,
    )

def start_phone() -> subprocess.Popen | None:
    runtime = load_yaml(CONFIG_ROOT / "runtime.yaml", {}) or {}
    if not (runtime.get("services") or {}).get("start_phone_ui", True):
        return None
    root = IMAGE_ROOT / "services" / "phone-ui"
    server = root / "server.py"
    if not server.exists():
        print("[mmh3] Phone UI not bundled yet; continuing without 7860 service.", flush=True)
        return None
    args = [
        sys.executable, str(server),
        "--port", os.environ.get("MMH3_PHONE_UI_PORT", "7860"),
        "--comfy", comfy.base_url(),
    ]
    return subprocess.Popen(args, cwd=root, stdout=log_file("phone-ui.log"), stderr=subprocess.STDOUT)

def terminate(proc: subprocess.Popen | None) -> None:
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

def main() -> int:
    startup_mark(supervisor_started=True)
    runtime = load_yaml(CONFIG_ROOT / "runtime.yaml", {}) or {}
    hw = detect()
    _, profile = select_profile(hw, IMAGE_ROOT / "config" / "hardware_profiles.yaml")
    mem_cfg = dict(runtime.get("memory") or {})
    mem_cfg.update(profile.get("memory") or {})
    threading.Thread(target=run_memory_guard, args=(mem_cfg,), daemon=True).start()

    # Optimize for two separate milestones:
    #   * interactive shell/UI availability as fast as possible;
    #   * first-generation readiness on an empty volume.
    #
    # Phone UI and Comfy start first. Give them a very short uncontested grace
    # period, then start large model downloads while Comfy finishes importing.
    # Jupyter is intentionally deferred until Comfy is healthy so notebook
    # startup cannot steal CPU/I/O from the latency-critical service.
    phone_proc = start_phone()
    comfy_proc = start_comfy()
    startup_mark(phone_spawned=True, comfy_spawned=True)

    STOP.wait(3)
    provision_proc = start_provisioner()
    provision_attempt = 1
    provision_retry_at = 0.0
    provision_cfg = runtime.get("provisioning") or {}
    try:
        provision_max_attempts = max(1, int(provision_cfg.get("retry_attempts", 3)))
    except (TypeError, ValueError):
        provision_max_attempts = 3
    try:
        provision_backoff = max(2.0, float(provision_cfg.get("retry_backoff_seconds", 8)))
    except (TypeError, ValueError):
        provision_backoff = 8.0
    startup_mark(provisioner_started=True, provisioner_attempt=provision_attempt)

    comfy_ready = comfy.wait_ready(180)
    startup_mark(comfy_ready=comfy_ready, comfy_ready_seconds=round(time.time() - START_EPOCH, 3))
    if not comfy_ready:
        print("[mmh3] ComfyUI did not become healthy within 180s; supervisor remains alive.", flush=True)

    jupyter_proc = start_jupyter()
    startup_mark(jupyter_spawned=bool(jupyter_proc))

    def _signal(_sig, _frame):
        STOP.set()
    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)

    crash_times: list[float] = []
    rescan_request = STATE_ROOT / "comfy_model_rescan.request"
    while not STOP.is_set():
        # The Phone UI writes this request when queue-time validation proves
        # Comfy's filename cache cannot see model files that are present on the
        # persistent volume. Restart only while idle so queued/running work is
        # never interrupted by the self-heal path.
        if rescan_request.exists() and comfy.queue_idle():
            print("[mmh3] stale Comfy model index detected; restarting Comfy with refreshed persistent paths", flush=True)
            startup_mark(comfy_model_rescan_requested=True)
            terminate(comfy_proc)
            comfy_proc = start_comfy()
            ready_after_rescan = comfy.wait_ready(240)
            startup_mark(
                comfy_model_rescan_complete=ready_after_rescan,
                comfy_ready=ready_after_rescan,
            )
            try:
                rescan_request.unlink()
            except OSError:
                pass

        if comfy_proc.poll() is not None:
            crash_times = [t for t in crash_times if time.time() - t < 600]
            crash_times.append(time.time())
            delay = min(60, 2 ** min(len(crash_times), 5))
            print(f"[mmh3] ComfyUI exited rc={comfy_proc.returncode}; restarting in {delay}s", flush=True)
            STOP.wait(delay)
            if not STOP.is_set():
                comfy_proc = start_comfy()
                comfy.wait_ready(240)
        if provision_proc is not None and provision_proc.poll() is not None:
            rc = provision_proc.returncode
            provision_proc = None
            if rc != 0 and provision_attempt < provision_max_attempts:
                delay = min(60.0, provision_backoff * (2 ** (provision_attempt - 1)))
                provision_retry_at = time.time() + delay
                print(
                    f"[mmh3] background provisioner exited rc={rc}; retry "
                    f"{provision_attempt + 1}/{provision_max_attempts} in {delay:.0f}s",
                    flush=True,
                )
                startup_mark(provisioner_retry_scheduled=True, provisioner_retry_in_seconds=delay)
            elif rc != 0:
                print(
                    f"[mmh3] background provisioner exhausted {provision_max_attempts} attempt(s) rc={rc}; "
                    "see provisioning.log",
                    flush=True,
                )
                startup_mark(provisioner_retry_exhausted=True)
            else:
                provision_retry_at = 0.0
                startup_mark(provisioner_complete=True)
        if provision_proc is None and provision_retry_at and time.time() >= provision_retry_at:
            provision_attempt += 1
            provision_retry_at = 0.0
            provision_proc = start_provisioner()
            startup_mark(
                provisioner_started=True,
                provisioner_attempt=provision_attempt,
                provisioner_retry_scheduled=False,
            )
        if phone_proc is not None and phone_proc.poll() is not None:
            print(f"[mmh3] Phone UI exited rc={phone_proc.returncode}; restarting", flush=True)
            phone_proc = start_phone()
        if jupyter_proc is not None and jupyter_proc.poll() is not None:
            print(f"[mmh3] Jupyter exited rc={jupyter_proc.returncode}; restarting", flush=True)
            jupyter_proc = start_jupyter()
        STOP.wait(2)

    terminate(provision_proc)
    terminate(phone_proc)
    terminate(jupyter_proc)
    terminate(comfy_proc)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
