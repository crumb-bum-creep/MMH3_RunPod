"""CivitAI downloads (resume, retry, lanes, checkpoints) and the boot volume-copy gate."""
from __future__ import annotations

import pytest
import requests

from studio import bootgate, loras, paths, util

DATA = bytes(range(256)) * 400  # 102400 bytes


class FakeResp:
    def __init__(self, status, body=b"", headers=None, fail_after=None):
        self.status_code, self.body, self.headers = status, body, headers or {}
        self.fail_after = fail_after

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=1):
        sent = 0
        for i in range(0, len(self.body), 10000):
            if self.fail_after is not None and sent >= self.fail_after:
                raise requests.exceptions.ChunkedEncodingError("connection reset")
            part = self.body[i:i + 10000]
            sent += len(part)
            yield part


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(loras.time, "sleep", lambda s: None)


def _drain():
    """Wait for the download workers to finish their queues."""
    for w in list(loras._workers.values()):
        w.join(timeout=10)


def _serve(monkeypatch, plan):
    """plan: list of callables(headers) -> FakeResp, one per request."""
    calls = []

    def fake_get(url, stream=True, headers=None, timeout=None, allow_redirects=True):
        calls.append(dict(headers or {}))
        return plan[min(len(calls), len(plan)) - 1](headers or {})

    monkeypatch.setattr(loras.requests, "get", fake_get)
    return calls


def test_a_dropped_connection_resumes_instead_of_starting_over(workspace, monkeypatch, no_sleep):
    dest = paths.MODELS / "diffusion_models" / "resume_me.safetensors"
    dest.unlink(missing_ok=True)

    def first(h):
        return FakeResp(200, DATA, {"Content-Length": str(len(DATA)), "Content-Type": "application/octet-stream"},
                        fail_after=40000)

    def second(h):
        start = int(h["Range"].split("=")[1].rstrip("-"))
        return FakeResp(206, DATA[start:], {"Content-Range": f"bytes {start}-{len(DATA) - 1}/{len(DATA)}",
                                            "Content-Length": str(len(DATA) - start)})

    calls = _serve(monkeypatch, [first, second])
    assert loras._fetch(1, {"id": 2, "size_kb": len(DATA) / 1024}, dest, "ck1:2")
    assert dest.read_bytes() == DATA
    assert "Range" not in calls[0] and calls[1]["Range"] == "bytes=40000-"
    assert util.read_json(loras.STATUS)["items"]["ck1:2"]["state"] == "ready"
    assert not dest.with_suffix(".safetensors.part").exists()


def test_a_refusal_fails_at_once_with_a_reason_and_no_retries(workspace, monkeypatch, no_sleep):
    dest = paths.MODELS / "diffusion_models" / "refused.safetensors"
    calls = _serve(monkeypatch, [lambda h: FakeResp(401)])
    assert not loras._fetch(5, {"id": 6}, dest, "ck5:6")
    assert len(calls) == 1
    st = util.read_json(loras.STATUS)["items"]["ck5:6"]
    assert st["state"] == "failed" and "CIVITAI_TOKEN" in st["error"]


def test_a_web_page_is_never_saved_as_a_model(workspace, monkeypatch, no_sleep):
    dest = paths.MODELS / "diffusion_models" / "html.safetensors"
    _serve(monkeypatch, [lambda h: FakeResp(200, b"<html>login</html>", {"Content-Type": "text/html"})])
    assert not loras._fetch(7, {"id": 8}, dest, "ck7:8")
    assert not dest.exists()


def test_a_short_transfer_is_retried_then_reported(workspace, monkeypatch, no_sleep):
    dest = paths.MODELS / "diffusion_models" / "short.safetensors"
    dest.unlink(missing_ok=True)
    dest.with_suffix(".safetensors.part").unlink(missing_ok=True)
    calls = _serve(monkeypatch, [lambda h: FakeResp(500)])
    assert not loras._fetch(9, {"id": 10}, dest, "ck9:10", retries=3)
    assert len(calls) == 3
    assert "HTTP 500" in util.read_json(loras.STATUS)["items"]["ck9:10"]["error"]


def test_browser_checkpoint_installs_download_and_show_progress(workspace, monkeypatch, no_sleep):
    """The bug: a checkpoint picked in the CivitAI browser showed up as a base model
    and then never arrived. It now downloads on its own lane, resumes, and reports."""
    loras.CHECKPOINTS.unlink(missing_ok=True)
    version = {"id": 77, "name": "v1", "model_name": "Big", "image": None,
               "files": [{"id": 701, "name": "big_ck.safetensors", "size_kb": len(DATA) / 1024, "family": "any"}]}
    monkeypatch.setattr(loras.civitai, "version", lambda vid: version)
    _serve(monkeypatch, [lambda h: FakeResp(200, DATA, {"Content-Length": str(len(DATA))})])
    ck = loras.add_checkpoint(77, 701)
    assert ck["downloads"][0]["id"] == 701
    _drain()
    assert (paths.MODELS / "diffusion_models/big_ck.safetensors").read_bytes() == DATA
    row = next(c for c in loras.checkpoints() if c["id"] == "civ77")
    assert all(row["available"].values()) and "download" not in row


def test_an_interrupted_checkpoint_is_shown_and_resumed_at_boot(workspace, monkeypatch, no_sleep):
    loras.CHECKPOINTS.unlink(missing_ok=True)
    util.write_json(loras.CHECKPOINTS, {"items": [{"id": "civ88", "label": "Half", "version_id": 88,
                                                   "files": {"fl2v": "diffusion_models/half.safetensors"}}]})
    part = paths.MODELS / "diffusion_models/half.safetensors.part"
    part.write_bytes(DATA[:30000])
    loras._set_status("ck88:801", state="downloading", done=30000, total=len(DATA))  # a Studio that died mid-way
    version = {"id": 88, "files": [{"id": 801, "name": "half.safetensors", "size_kb": len(DATA) / 1024, "family": "fl2v"}]}
    monkeypatch.setattr(loras.civitai, "version", lambda vid: version)
    row = next(c for c in loras.checkpoints() if c["id"] == "civ88")
    assert row["download"]["state"] == "interrupted" and row["download"]["done"] == 30000

    calls = _serve(monkeypatch, [lambda h: FakeResp(206, DATA[30000:], {
        "Content-Range": f"bytes 30000-{len(DATA) - 1}/{len(DATA)}", "Content-Length": str(len(DATA) - 30000)})])
    loras.resume_checkpoints()
    _drain()
    assert (paths.MODELS / "diffusion_models/half.safetensors").read_bytes() == DATA
    assert calls[0]["Range"] == "bytes=30000-"
    saved = util.read_json(loras.CHECKPOINTS)["items"][0]
    assert saved["downloads"][0]["id"] == 801  # learned once, for next time


def test_the_same_file_is_never_queued_twice(workspace):
    job = {"version_id": 99, "file": {"id": 1, "name": "x.safetensors"}, "dest": paths.MODELS / "x"}
    with loras._lock:
        loras._pending.add("ck99:1")
        try:
            assert loras._enqueue(("checkpoint", job)) is False
        finally:
            loras._pending.discard("ck99:1")


# --------------------------------------------------------------------------- boot gate

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


CFG = {"enabled": True, "fresh_window_seconds": 60, "partial_window_seconds": 300, "settle_seconds": 120,
       "max_wait_seconds": 7200, "threshold_mb": 256, "poll_seconds": 10}


def test_gate_opens_after_the_window_on_a_new_volume(workspace, monkeypatch):
    monkeypatch.setattr(bootgate, "core_share", lambda: 0.0)
    monkeypatch.setattr(bootgate, "has_history", lambda: False)
    clk = Clock()
    bootgate.begin()
    assert not bootgate.is_open()
    reason = bootgate.watch(CFG, clock=clk, sleep=clk.sleep, measure=lambda: 1000)
    assert reason == "new volume" and bootgate.is_open()
    assert 60 <= clk.t <= 80


def test_gate_holds_while_files_arrive_and_opens_when_quiet(workspace, monkeypatch):
    monkeypatch.setattr(bootgate, "core_share", lambda: 0.0)
    monkeypatch.setattr(bootgate, "has_history", lambda: False)
    clk = Clock()
    GB = 1024 ** 3

    def measure():  # a copy that writes 1 GB every 10 s for 10 minutes, then stops
        return int(min(clk.t, 600) / 10) * GB

    seen = []
    orig = bootgate._write
    monkeypatch.setattr(bootgate, "_write", lambda state, **kw: (seen.append(state), orig(state, **kw)))
    reason = bootgate.watch(CFG, clock=clk, sleep=clk.sleep, measure=measure)
    assert reason == "the volume copy finished"
    assert "copying" in seen
    assert clk.t >= 600 + 120  # never before the copy has been quiet for settle_seconds


def test_gate_opens_at_once_on_a_set_up_volume_and_on_skip(workspace, monkeypatch):
    monkeypatch.setattr(bootgate, "core_share", lambda: 1.0)
    assert bootgate.watch(CFG, measure=lambda: 0) == "volume already set up"
    monkeypatch.setattr(bootgate, "core_share", lambda: 0.0)
    monkeypatch.setattr(bootgate, "has_history", lambda: True)
    clk = Clock()
    bootgate.begin()
    bootgate.skip()
    assert bootgate.watch(CFG, clock=clk, sleep=clk.sleep, measure=lambda: 0) == "you chose to download now"
    bootgate.GATE_FILE.unlink(missing_ok=True)
    assert bootgate.is_open()  # no boot check in this container (dev, tests): open


def test_tree_bytes_ignores_what_this_container_writes(workspace, tmp_path):
    (tmp_path / "mmh3/logs").mkdir(parents=True)
    (tmp_path / "mmh3/logs/studio.log").write_bytes(b"x" * 5000)
    (tmp_path / "ComfyUI/models/vae").mkdir(parents=True)
    (tmp_path / "ComfyUI/models/vae/a.safetensors").write_bytes(b"x" * 700)
    assert bootgate.tree_bytes(tmp_path) == 700


# --------------------------------------------------------------------------- add by link

from studio import sources  # noqa: E402


@pytest.mark.parametrize("text,want", [
    ("3429412", {"source": "civitai", "version_id": 3429412}),
    ("m:2851079", {"source": "civitai", "model_id": 2851079}),
    ("https://civitai.com/models/2851079/h3-eros-max?modelVersionId=3429412",
     {"source": "civitai", "version_id": 3429412, "model_id": 2851079}),
    ("https://civitai.red/api/download/models/3429412?type=Diffusion%20Model&fileId=3329966",
     {"source": "civitai", "version_id": 3429412, "file_id": 3329966}),
    ("urn:air:minimaxh3:checkpoint:civitai:12@34", {"source": "civitai", "version_id": 34, "model_id": 12,
                                                   "kind": "checkpoint"}),
    ("https://huggingface.co/a/b/blob/main/sub/x.safetensors",
     {"source": "hf", "repo": "a/b", "revision": "main", "path": "sub/x.safetensors"}),
    ("https://huggingface.co/a/b", {"source": "hf", "repo": "a/b", "revision": "main", "path": None, "folder": ""}),
    ("a/b", {"source": "hf", "repo": "a/b", "revision": "main", "path": None}),
    ("hf:a/b/x.safetensors", {"source": "hf", "repo": "a/b", "revision": "main", "path": "x.safetensors"}),
    ("https://example.com/f/x.safetensors", {"source": "url", "url": "https://example.com/f/x.safetensors"}),
])
def test_links_and_ids_parse(text, want):
    assert sources.parse(text) == want


def test_nonsense_and_non_safetensors_are_refused():
    with pytest.raises(sources.SourceError):
        sources.parse("make it pretty")
    with pytest.raises(loras.civitai.CivitaiError):
        loras.safe_filename("model.gguf")
    assert loras.safe_filename("../../etc/bad name.safetensors") == "bad_name.safetensors"


def test_check_off_installs_a_direct_link_without_looking_it_up(workspace, monkeypatch):
    queued = []
    monkeypatch.setattr(loras, "_enqueue", lambda item: queued.append(item) or True)
    monkeypatch.setattr(sources, "_probe", lambda url: pytest.fail("no lookup with the check off"))
    r = sources.quick_install("https://huggingface.co/a/b/resolve/main/my_lora-ref2va.safetensors", "lora", "Mine")
    entry = r["item"]
    assert entry["nickname"] == "Mine" and entry["files"][0]["family"] == "ref2v"
    assert queued[0][1]["file"]["url"].endswith("/my_lora-ref2va.safetensors")
    assert loras._job_key(*queued[0]) == "url:my_lora-ref2va.safetensors"
    row = next(i for i in loras.listing()["items"] if i["key"] == "my_lora-ref2va.safetensors")
    assert row["source_url"] and not row.get("untracked")

    r = sources.quick_install("https://example.com/m/big_unet.safetensors", "checkpoint")
    ck = r["item"]
    assert ck["files"] == {"fl2v": "diffusion_models/big_unet.safetensors", "ref2v": "diffusion_models/big_unet.safetensors"}
    assert any(c["id"] == ck["id"] and c["download"] for c in loras.checkpoints())
