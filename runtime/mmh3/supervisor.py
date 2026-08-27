from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

from . import comfy
from .common import CONFIG_ROOT, IMAGE_ROOT, LOG_ROOT, COMFY_DIR, load_yaml
from .hardware import detect, select_profile
from .memory_guard import run as run_memory_guard

STOP = threading.Event()

def log_file(name: str):
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    return (LOG_ROOT / name).open("ab", buffering=0)

def start_comfy() -> subprocess.Popen:
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

    # reserve_vram_gb is intentionally not applied yet: the known-good pod did
    # not use --reserve-vram. The detected profile records it for later tuning.
    _reserve = ((profile.get("comfy") or {}).get("reserve_vram_gb"))

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
    runtime = load_yaml(CONFIG_ROOT / "runtime.yaml", {}) or {}
    hw = detect()
    _, profile = select_profile(hw, IMAGE_ROOT / "config" / "hardware_profiles.yaml")
    mem_cfg = dict(runtime.get("memory") or {})
    mem_cfg.update(profile.get("memory") or {})
    threading.Thread(target=run_memory_guard, args=(mem_cfg,), daemon=True).start()

    comfy_proc = start_comfy()
    if not comfy.wait_ready(240):
        print("[mmh3] ComfyUI did not become healthy within 240s; supervisor remains alive.", flush=True)
    phone_proc = start_phone()
    jupyter_proc = start_jupyter()

    def _signal(_sig, _frame):
        STOP.set()
    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)

    crash_times: list[float] = []
    while not STOP.is_set():
        if comfy_proc.poll() is not None:
            crash_times = [t for t in crash_times if time.time() - t < 600]
            crash_times.append(time.time())
            delay = min(60, 2 ** min(len(crash_times), 5))
            print(f"[mmh3] ComfyUI exited rc={comfy_proc.returncode}; restarting in {delay}s", flush=True)
            STOP.wait(delay)
            if not STOP.is_set():
                comfy_proc = start_comfy()
                comfy.wait_ready(240)
        if phone_proc is not None and phone_proc.poll() is not None:
            print(f"[mmh3] Phone UI exited rc={phone_proc.returncode}; restarting", flush=True)
            phone_proc = start_phone()
        if jupyter_proc is not None and jupyter_proc.poll() is not None:
            print(f"[mmh3] Jupyter exited rc={jupyter_proc.returncode}; restarting", flush=True)
            jupyter_proc = start_jupyter()
        STOP.wait(2)

    terminate(phone_proc)
    terminate(jupyter_proc)
    terminate(comfy_proc)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
