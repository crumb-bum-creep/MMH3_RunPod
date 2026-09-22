from __future__ import annotations

import asyncio
import importlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
PHONE_ROOT = ROOT / "services" / "phone-ui"


def load_wrapper():
    sys.path.insert(0, str(PHONE_ROOT))
    try:
        sys.modules.pop("server", None)
        return importlib.import_module("server")
    finally:
        try:
            sys.path.remove(str(PHONE_ROOT))
        except ValueError:
            pass


class FakeResponse:
    def __init__(self, data, *, ok=True):
        self.data = data
        self.ok = ok

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.data

    async def text(self):
        return json.dumps(self.data)


class FakeSession:
    def __init__(self):
        self.gets = []
        self.posts = []

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        assert url.endswith("/queue")
        return FakeResponse({"queue_running": [], "queue_pending": []})

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if url.endswith("/prompt"):
            return FakeResponse({"prompt_id": "integration-prompt-123"})
        raise AssertionError(f"unexpected POST: {url}")


class FakeRequest:
    def __init__(self, app, payload):
        self.app = app
        self.payload = payload

    async def json(self):
        return self.payload


class ReadyModelPath:
    def is_file(self):
        return True

    def stat(self):
        return SimpleNamespace(st_size=2 * 1024**3)


def test_submit_posts_to_comfy_without_memory_cleanup(monkeypatch, tmp_path):
    wrapper = load_wrapper()
    base = wrapper.base
    session = FakeSession()

    # Simulate ordinary memory pressure that used to trigger /free and block
    # the request before Comfy ever saw /prompt. It is below the critical rail.
    def fake_load_json(path, default):
        name = Path(path).name
        if name == "provisioning.json":
            return {"core_ready": True}
        if name == "memory.json":
            return {
                "fraction": 0.88,
                "critical_fraction": 0.93,
                "free_bytes": 24 * 1024**3,
                "updated_at": time.time(),
            }
        return default

    monkeypatch.setattr(base, "_load_json", fake_load_json)
    monkeypatch.setattr(wrapper, "_profile_path", lambda _name: ReadyModelPath())
    monkeypatch.setattr(wrapper, "patch_workflow_v3", lambda payload: ({"1": {"class_type": "TestNode", "inputs": {}}, "9": {"class_type": "VHS_VideoCombine", "inputs": {}}}, {
        "mode": "t2v",
        "model_family": "fl2v",
        "seed": 123,
    }))
    monkeypatch.setattr(base, "_make_plan", lambda graph: {"nodes": {}})
    monkeypatch.setattr(base, "_save_json", lambda *args, **kwargs: None)
    touched = []
    monkeypatch.setattr(base, "_touch", lambda app, pid, **kwargs: touched.append((pid, kwargs)))

    # Prove the active v2 submit path itself never invokes the memory release API.
    def fail_free(*args, **kwargs):
        raise AssertionError("enqueue must never call free_memory")

    monkeypatch.setattr(base.comfy, "free_memory", fail_free)

    app = {
        "session": session,
        "comfy": "http://127.0.0.1:8188",
        "client_id": "phone-ui-test",
        "records": {},
        "plans": {},
    }
    request = FakeRequest(app, {"mode": "t2v", "prompt_mode": "custom", "prompt": "queue me"})
    response = asyncio.run(wrapper.api_generate(request))
    result = json.loads(response.text)

    assert result == {"prompt_id": "integration-prompt-123", "seed": 123}
    assert [url for url, _ in session.posts] == ["http://127.0.0.1:8188/prompt"]
    sent = session.posts[0][1]["json"]
    assert sent["client_id"] == "phone-ui-test"
    assert sent["prompt"]["1"]["class_type"] == "TestNode"
    assert sent["partial_execution_targets"] == ["9"]
    assert "integration-prompt-123" in app["records"]
    assert "integration-prompt-123" in app["plans"]
    assert touched == [("integration-prompt-123", {"status": "queued", "process_name": "Waiting in queue"})]


def test_critical_rejection_is_side_effect_free(monkeypatch):
    wrapper = load_wrapper()
    base = wrapper.base
    session = FakeSession()

    def fake_load_json(path, default):
        name = Path(path).name
        if name == "provisioning.json":
            return {"core_ready": True}
        if name == "memory.json":
            return {
                "fraction": 0.95,
                "critical_fraction": 0.93,
                "free_bytes": 8 * 1024**3,
                "updated_at": time.time(),
            }
        return default

    monkeypatch.setattr(base, "_load_json", fake_load_json)
    monkeypatch.setattr(wrapper, "_profile_path", lambda _name: ReadyModelPath())
    monkeypatch.setattr(base.comfy, "free_memory", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no cleanup on reject")))

    app = {
        "session": session,
        "comfy": "http://127.0.0.1:8188",
        "client_id": "phone-ui-test",
        "records": {},
        "plans": {},
    }
    request = FakeRequest(app, {"mode": "t2v", "prompt_mode": "custom", "prompt": "too critical"})

    try:
        asyncio.run(wrapper.api_generate(request))
    except wrapper.web.HTTPServiceUnavailable as exc:
        assert "critically high" in exc.text
        assert "no memory cleanup" in exc.text
    else:
        raise AssertionError("expected critical-memory rejection")

    assert session.posts == []


class NodeErrorSession(FakeSession):
    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if url.endswith("/prompt"):
            return FakeResponse({
                "prompt_id": "bad-prompt",
                "node_errors": {"9": {"errors": [{"message": "video branch invalid"}]}},
            })
        raise AssertionError(f"unexpected POST: {url}")


def test_node_errors_never_become_fake_success(monkeypatch):
    wrapper = load_wrapper()
    base = wrapper.base
    session = NodeErrorSession()

    monkeypatch.setattr(
        base,
        "_load_json",
        lambda path, default: {"core_ready": True}
        if Path(path).name == "provisioning.json"
        else default,
    )
    monkeypatch.setattr(wrapper, "_profile_path", lambda _name: ReadyModelPath())
    monkeypatch.setattr(wrapper, "patch_workflow_v3", lambda payload: ({
        "9": {"class_type": "VHS_VideoCombine", "inputs": {}},
    }, {
        "mode": "t2v",
        "model_family": "fl2v",
        "seed": 123,
    }))
    monkeypatch.setattr(base, "_make_plan", lambda graph: {"nodes": {}})

    app = {
        "session": session,
        "comfy": "http://127.0.0.1:8188",
        "client_id": "phone-ui-test",
        "records": {},
        "plans": {},
    }
    request = FakeRequest(app, {"mode": "t2v", "prompt_mode": "custom", "prompt": "fail correctly"})

    try:
        asyncio.run(wrapper.api_generate(request))
    except wrapper.web.HTTPBadGateway as exc:
        assert "video output branch" in exc.text
        assert "video branch invalid" in exc.text
    else:
        raise AssertionError("expected node validation failure")

    assert app["records"] == {}
    assert app["plans"] == {}


class ObjectInfoSession:
    def __init__(self, info):
        self.info = info
        self.gets = []

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        assert url.endswith("/object_info")
        return FakeResponse(self.info)


def test_stale_model_index_requests_safe_comfy_refresh(monkeypatch, tmp_path):
    wrapper = load_wrapper()
    monkeypatch.setattr(wrapper.base, "STATE_ROOT", tmp_path)
    session = ObjectInfoSession({
        "CLIPLoader": {"input": {"required": {"clip_name": [[]]}}},
    })
    app = {
        "session": session,
        "comfy": "http://127.0.0.1:8188",
    }
    graph = {
        "1": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"},
        }
    }

    try:
        asyncio.run(wrapper._ensure_graph_models_visible(app, graph))
    except wrapper.web.HTTPServiceUnavailable as exc:
        assert "Automatic Comfy refresh requested" in exc.text
    else:
        raise AssertionError("expected stale model-index rejection")

    request = tmp_path / "comfy_model_rescan.request"
    assert request.is_file()
    body = json.loads(request.read_text())
    assert "qwen3vl_32b_minimax_h3_int8_convrot.safetensors" in body["missing"]
