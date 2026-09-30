"""Container entrypoint: start and watch ComfyUI, Studio, Jupyter, provisioning.

    python -m studio.supervisor

ComfyUI and Studio are separate processes, each restarted if it dies. Studio can
ask for a ComfyUI restart by touching state/restart_comfy.request.
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import loras, memory, paths, util

log = util.setup_logging("supervisor")
paths.LOGS.mkdir(parents=True, exist_ok=True)
import logging as _logging  # noqa: E402

_fh = _logging.FileHandler(paths.LOGS / "supervisor.log")
_fh.setFormatter(_logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
_logging.getLogger().addHandler(_fh)
SERVICES_FILE = paths.STATE / "services.json"
RESTART_REQUEST = paths.STATE / "restart_comfy.request"
SAGE_FILE = paths.STATE / "sage.json"
PY = sys.executable

SAGE_PROBE = """
import sys, torch
try:
    from sageattention import sageattn
except Exception as e:
    print("import failed:", e); sys.exit(2)
q = torch.randn(1, 8, 128, 64, dtype=torch.float16, device="cuda")
sageattn(q, q.clone(), q.clone()); torch.cuda.synchronize()
print("ok sm%d%d" % torch.cuda.get_device_capability())
"""


def _logfile(name: str):
    path = paths.LOGS / name
    if path.exists() and path.stat().st_size > 0:
        os.replace(path, path.with_suffix(path.suffix + ".1"))
    return path.open("ab", buffering=0)


def sage_ok(cfg: dict[str, Any]) -> bool:
    mode = str(cfg.get("sage_attention", "auto")).lower()
    if mode in ("false", "off", "no"):
        return False
    gpu = memory.gpu().get("name", "")
    cached = util.read_json(SAGE_FILE, {}) or {}
    if cached.get("gpu") == gpu and "ok" in cached:
        return bool(cached["ok"])
    try:
        out = subprocess.run([PY, "-c", SAGE_PROBE], capture_output=True, text=True, timeout=180)
        ok = out.returncode == 0 and out.stdout.strip().startswith("ok")
        detail = (out.stdout + out.stderr).strip()[-300:]
    except (OSError, subprocess.SubprocessError) as exc:
        ok, detail = False, str(exc)
    util.write_json(SAGE_FILE, {"gpu": gpu, "ok": ok, "detail": detail, "checked": time.time()})
    log.info("SageAttention probe on %s: %s (%s)", gpu or "unknown GPU", "on" if ok else "off", detail)
    return ok


def comfy_args(settings: dict[str, Any], sage: bool) -> list[str]:
    cfg = settings.get("comfy") or {}
    port = str((settings.get("ports") or {}).get("comfy", 8188))
    args = [PY, str(paths.COMFY_CODE / "main.py"), "--listen", "0.0.0.0", "--port", port,
            "--models-directory", str(paths.MODELS), "--input-directory", str(paths.INPUT),
            "--output-directory", str(paths.OUTPUT), "--user-directory", str(paths.COMFY_USER),
            "--cache-ram", str(memory.cache_headroom_gb(cfg.get("cache_ram_headroom_gb", "auto")))]
    if cfg.get("disable_dynamic_vram", True):
        args.append("--disable-dynamic-vram")
    if sage:
        args.append("--use-sage-attention")
    args += [str(a) for a in cfg.get("extra_args") or []]
    args += shlex.split(os.environ.get("COMFY_EXTRA_ARGS", ""))
    return args


class Service:
    def __init__(self, name: str, argv: list[str], env: dict[str, str] | None = None,
                 cwd: Path | None = None, logname: str | None = None) -> None:
        self.name, self.argv, self.env, self.cwd = name, argv, env, cwd
        self.logname = logname or f"{name}.log"
        self.proc: subprocess.Popen | None = None
        self.restarts = 0
        self.started = 0.0

    def start(self) -> None:
        log.info("starting %s: %s", self.name, " ".join(self.argv))
        self.proc = subprocess.Popen(self.argv, cwd=self.cwd, env=self.env, stdout=_logfile(self.logname),
                                     stderr=subprocess.STDOUT, start_new_session=True)
        self.started = time.time()

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def stop(self, timeout: float = 20) -> None:
        if not self.alive():
            return
        assert self.proc is not None
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def status(self) -> dict[str, Any]:
        return {"alive": self.alive(), "pid": self.proc.pid if self.proc else None,
                "started": self.started, "restarts": self.restarts, "argv": self.argv}


def main() -> None:
    paths.ensure_dirs()
    loras.ensure_seeded()
    settings = util.settings()
    ports = settings.get("ports") or {}

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{paths.IMAGE_ROOT}:{env.get('PYTHONPATH', '')}".rstrip(":")
    env.setdefault("PYTHONUNBUFFERED", "1")
    tcmalloc = next((p for p in ("/usr/lib/x86_64-linux-gnu/libtcmalloc_minimal.so.4",
                                 "/usr/lib/x86_64-linux-gnu/libtcmalloc.so.4") if os.path.exists(p)), None)
    comfy_env = dict(env, LD_PRELOAD=tcmalloc) if tcmalloc else env

    studio = Service("studio", [PY, "-m", "studio.server"], env=env, cwd=paths.IMAGE_ROOT)
    studio.start()  # up first, so the phone UI shows boot progress immediately

    provisioner = Service("provision", [PY, "-m", "studio.provision"], env=env, cwd=paths.IMAGE_ROOT)
    provisioner.start()

    jupyter = None
    if (settings.get("services") or {}).get("jupyter", True):
        token = os.environ.get("JUPYTER_TOKEN", "")
        jupyter = Service("jupyter", ["jupyter-lab", "--ip=0.0.0.0", f"--port={ports.get('jupyter', 8888)}",
                                      "--allow-root", "--no-browser", f"--ServerApp.root_dir={paths.WORKSPACE}",
                                      f"--IdentityProvider.token={token}", "--ServerApp.allow_origin=*"],
                          env=env, cwd=paths.WORKSPACE)
        try:
            jupyter.start()
        except OSError as exc:
            log.warning("jupyter not started: %s", exc)
            jupyter = None

    sage = sage_ok(settings.get("comfy") or {})
    comfy = Service("comfy", comfy_args(settings, sage), env=comfy_env, cwd=paths.COMFY_CODE, logname="comfyui.log")
    comfy.start()

    stopping = False

    def on_signal(signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    def write_state() -> None:
        util.write_json(SERVICES_FILE, {
            "comfy": comfy.status(), "studio": studio.status(),
            "jupyter": jupyter.status() if jupyter else None,
            "provisioner": provisioner.status(), "sage": sage, "updated_at": time.time(),
        })

    backoff = {"comfy": 0.0, "studio": 0.0}
    while not stopping:
        write_state()
        if RESTART_REQUEST.exists():
            log.info("ComfyUI restart requested")
            RESTART_REQUEST.unlink(missing_ok=True)
            comfy.stop()
            comfy.argv = comfy_args(util.settings(), sage)
            comfy.restarts += 1
            comfy.start()
        for svc in (comfy, studio):
            if not svc.alive() and time.time() >= backoff[svc.name]:
                code = svc.proc.returncode if svc.proc else None
                log.warning("%s exited (code %s); restarting", svc.name, code)
                svc.restarts += 1
                backoff[svc.name] = time.time() + min(60, 5 * svc.restarts)
                if svc is comfy:
                    comfy.argv = comfy_args(util.settings(), sage)
                svc.start()
        time.sleep(2)

    log.info("shutting down")
    for svc in (comfy, studio, provisioner, jupyter):
        if svc:
            svc.stop(timeout=10)


if __name__ == "__main__":
    main()
