"""End to end: Studio + a fake ComfyUI as real processes, on a throwaway volume."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROMPT = "integrated_multimodal_description: [Shot 1] test.\n\noverall_soundscape: rain.\n\nnon_diegetic_music: N/A"
pytestmark = pytest.mark.skipif(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0,
                                reason="needs ffmpeg")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Stack:
    def __init__(self, tmp: Path) -> None:
        self.ws = tmp / "ws"
        self.comfy_port, self.studio_port = free_port(), free_port()
        subprocess.run([sys.executable, str(ROOT / "scripts/dev_workspace.py"), str(self.ws),
                        "--comfy-port", str(self.comfy_port)], check=True, capture_output=True)
        settings = self.ws / "mmh3/config/settings.yaml"
        settings.write_text(settings.read_text() + f"ports:\n  comfy: {self.comfy_port}\n  studio: {self.studio_port}\n"
                            if "ports" not in settings.read_text() else
                            f"ports:\n  comfy: {self.comfy_port}\n  studio: {self.studio_port}\n")
        self.env = dict(os.environ, MMH3_WORKSPACE=str(self.ws), PYTHONPATH=str(ROOT))
        self.env.pop("OPENROUTER_API_KEY", None)
        self.env.pop("LLM_KEY", None)
        self.fake = subprocess.Popen([sys.executable, str(ROOT / "tests/fake_comfy.py"), "--port", str(self.comfy_port),
                                      "--output", str(self.ws / "ComfyUI/output"), "--step", "0.15"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.studio = None
        self.start_studio()

    def start_studio(self) -> None:
        self.log = open(self.ws / "studio-test.log", "ab")
        self.studio = subprocess.Popen([sys.executable, "-m", "studio.server"], cwd=ROOT, env=self.env,
                                       stdout=self.log, stderr=subprocess.STDOUT)
        self.wait(lambda: self.call("/api/state")["comfy"], 20)

    def call(self, path: str, body=None, method=None, port=None):
        url = f"http://127.0.0.1:{port or self.studio_port}{path}"
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method=method or ("POST" if body is not None else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return {"http": e.code, **json.loads(e.read() or b"{}")}
        except (urllib.error.URLError, ConnectionError):
            return {}

    def wait(self, pred, timeout=60, step=0.3):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if pred():
                    return True
            except (KeyError, TypeError):
                pass
            time.sleep(step)
        raise AssertionError("timed out waiting")

    def job(self, jid):
        s = self.call("/api/state")
        return next(j for j in s["pending"] + s["finished"] if j["id"] == jid)

    def wait_done(self, ids, timeout=90):
        self.wait(lambda: all(self.job(j)["status"] in ("done", "failed", "cancelled") for j in ids), timeout)
        return [self.job(j) for j in ids]

    def fake_debug(self):
        return self.call("/_debug", port=self.comfy_port)

    def stop(self):
        for p in (self.studio, self.fake):
            if p and p.poll() is None:
                p.terminate()
                p.wait(10)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    s = Stack(tmp_path_factory.mktemp("e2e"))
    yield s
    s.stop()


def test_t2v_job_renders_and_records(stack):
    r = stack.call("/api/generate", {"mode": "t2v", "prompt": PROMPT, "duration": 6, "loras": [{"key": "3224980", "strength": 1.1}]})
    (job,) = stack.wait_done(r["jobs"])
    assert job["status"] == "done", job
    out = stack.ws / "ComfyUI/output" / job["output"]["file"]
    assert out.exists() and out.name.endswith("-audio.mp4")
    assert not out.with_name(out.name.replace("-audio", "")).exists(), "silent twin should be removed"
    rec = json.loads(out.with_suffix(".mp4.h3.json").read_text())
    assert rec["studio"] == 1 and rec["prompt"] == PROMPT and rec["frames"] == 158
    assert rec["loras"][0]["file"] == "minimax-h3-digicam.safetensors" and rec["recipe"]["shift"] == [6.0, 3.0]
    lib = stack.call("/api/outputs")
    assert job["output"]["file"] in [i["file"] for i in lib["items"]]


def test_family_switch_unloads_models_once(stack):
    before = len(stack.fake_debug()["frees"])
    a = stack.call("/api/generate", {"mode": "r2v", "prompt": PROMPT,
                                     "refs": [{"kind": "image", "file": "mmh3/2026-09/portrait_1.png"}]})["jobs"]
    b = stack.call("/api/generate", {"mode": "r2v", "prompt": PROMPT,
                                     "refs": [{"kind": "image", "file": "mmh3/2026-09/portrait_2.png"}]})["jobs"]
    c = stack.call("/api/generate", {"mode": "i2v", "prompt": PROMPT, "start_image": "mmh3/2026-09/portrait_3.png"})["jobs"]
    jobs = stack.wait_done(a + b + c)
    assert all(j["status"] == "done" for j in jobs), jobs
    unloads = [f for f in stack.fake_debug()["frees"][before:] if f.get("unload_models")]
    assert len(unloads) == 2, "t2v->r2v and r2v->i2v switch; r2v->r2v must not unload"


def test_compare_shares_seed_and_prompt(stack):
    r = stack.call("/api/generate", {"mode": "t2v", "prompt": PROMPT, "compare": True})
    jobs = stack.wait_done(r["jobs"])
    assert len(jobs) == 3 and len({j["seed"] for j in jobs}) == 1 and len({j["group"] for j in jobs}) == 1
    assert sorted(j["recipe_id"] for j in jobs) == ["balanced", "fast", "upstream"]
    files = {j["output"]["file"] for j in jobs}
    assert len(files) == 3, "each recipe gets its own file"


def test_failure_is_reported_readably(stack):
    (job,) = stack.wait_done(stack.call("/api/generate", {"mode": "t2v", "prompt": PROMPT + " FAILME"})["jobs"])
    assert job["status"] == "failed" and "out of memory" in job["error"].lower()


def test_cancel_running_job(stack):
    r = stack.call("/api/generate", {"mode": "t2v", "prompt": PROMPT})
    jid = r["jobs"][0]
    stack.wait(lambda: stack.job(jid)["status"] == "running" and stack.job(jid)["step"] >= 1, 30)
    stack.call(f"/api/jobs/{jid}/cancel", {})
    (job,) = stack.wait_done([jid])
    assert job["status"] == "cancelled"


def test_auto_without_key_fails_clearly(stack):
    r = stack.call("/api/generate", {"mode": "t2v", "idea": "a cat on a roof"})
    (job,) = stack.wait_done(r["jobs"])
    assert job["status"] == "failed" and "OpenRouter key" in job["error"]


def test_validation_errors(stack):
    assert stack.call("/api/generate", {"mode": "i2v", "prompt": PROMPT})["http"] == 400
    assert stack.call("/api/generate", {"mode": "r2v", "prompt": PROMPT, "refs": [{"kind": "image", "file": "nope.png"}]})["http"] == 400
    assert stack.call("/api/generate", {"mode": "t2v", "prompt": PROMPT, "overrides": {"steps": 999}})["http"] == 400


def test_job_survives_studio_restart(stack):
    r = stack.call("/api/generate", {"mode": "t2v", "prompt": PROMPT, "duration": 8})
    jid = r["jobs"][0]
    stack.wait(lambda: stack.job(jid)["status"] == "running" and stack.job(jid)["step"] >= 1, 30)
    stack.studio.send_signal(signal.SIGKILL)
    stack.studio.wait(10)
    stack.start_studio()
    (job,) = stack.wait_done([jid])
    assert job["status"] == "done" and job["output"], job


def test_reuse_roundtrip(stack):
    lib = stack.call("/api/outputs")["items"]
    new = next(i for i in lib if i["meta"].get("studio"))
    rec = stack.call(f"/api/outputs/reuse?file={urllib.request.quote(new['file'])}")
    assert rec["mode"] and rec["prompt"] and "loras" in rec
    legacy = stack.call("/api/outputs/reuse?file=MMH3/T2V_00001-audio.mp4")
    assert legacy["loras"][0]["file"] == "minimax-h3-digicam.safetensors"
    cont = stack.call("/api/outputs/continue", {"file": new["file"]})
    assert (stack.ws / "ComfyUI/input" / cont["start_image"]).stat().st_size > 0


def test_end_frame_only_i2v_gets_its_alignment_line(stack):
    r = stack.call("/api/generate", {"mode": "i2v", "prompt": PROMPT, "end_image": "mmh3/2026-09/portrait_3.png", "duration": 6})
    (job,) = stack.wait_done(r["jobs"])
    assert job["status"] == "done", job
    rec = json.loads((stack.ws / "ComfyUI/output" / job["output"]["file"]).with_suffix(".mp4.h3.json").read_text())
    assert rec["prompt"].startswith("How the reference pictures align with the target video — <Picture 1> (from [Shot 1]) "
                                    "aligns with the 6.58-second mark")
    assert rec["prompt"].endswith(PROMPT)


def test_tweak_and_seen_endpoints(stack):
    r = stack.call("/api/refine", {"mode": "t2v", "prompt": PROMPT, "request": "make it night"})
    assert r["http"] == 400 and "OpenRouter key" in r["error"]
    lib = stack.call("/api/outputs")
    assert "new" in lib and all("new" in i for i in lib["items"])
    stack.call("/api/outputs/seen", {"all": True})
    assert stack.call("/api/outputs")["new"] == 0


def test_compare_runs_the_recipes_you_ticked(stack):
    refs = [{"kind": "image", "file": "mmh3/2026-09/portrait_1.png"}]
    stack.call("/api/recipes/ref2v/choices", {"compare": ["balanced"]}, method="PUT")
    assert stack.call("/api/generate", {"mode": "r2v", "prompt": PROMPT, "refs": refs, "compare": True})["http"] == 400
    stack.call("/api/recipes/ref2v/choices", {"compare": ["balanced", "legacy_euler"]}, method="PUT")
    jobs = stack.wait_done(stack.call("/api/generate", {"mode": "r2v", "prompt": PROMPT, "refs": refs, "compare": True})["jobs"])
    assert sorted(j["recipe_id"] for j in jobs) == ["balanced", "legacy_euler"] and all(j["status"] == "done" for j in jobs)
    legacy = next(j for j in jobs if j["recipe_id"] == "legacy_euler")
    assert (legacy["recipe"]["scheduler"], legacy["recipe"]["steps"], legacy["recipe"]["lora"]) == ("beta", 4, "ref2v_turbo_4step_v01")
