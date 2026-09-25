from __future__ import annotations

import requests

from mmh3 import comfy


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_queue_failure_is_not_idle(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.Timeout("busy")

    monkeypatch.setattr(comfy.requests, "get", fail)
    assert comfy.queue_state() is None
    assert comfy.queue_idle() is False


def test_verified_empty_queue_is_idle(monkeypatch):
    monkeypatch.setattr(
        comfy.requests,
        "get",
        lambda *args, **kwargs: FakeResponse({"queue_running": [], "queue_pending": []}),
    )
    assert comfy.queue_idle() is True


def test_stable_idle_requires_repeated_idle_samples(monkeypatch):
    samples = iter([True, False])
    monkeypatch.setattr(comfy, "queue_idle", lambda: next(samples))
    monkeypatch.setattr(comfy.time, "sleep", lambda *_: None)
    assert comfy.queue_idle_stable(checks=2, delay=1.0) is False


def test_supervisor_restart_path_is_fail_safe():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "runtime"
        / "mmh3"
        / "supervisor.py"
    ).read_text(encoding="utf-8")

    assert "comfy.queue_idle_stable(checks=2, delay=1.0)" in source
    assert "if ready_after_restart:" in source
    assert "retaining restart request and forcing recovery" in source
    assert "A live-but-unhealthy process would otherwise evade the crash" in source
    assert "if recovered:" in source
    assert "Comfy recovery launch is still unhealthy; forcing another retry" in source
