from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memory_guard_is_cache_first_and_can_escalate():
    guard = (ROOT / "runtime" / "mmh3" / "memory_guard.py").read_text(encoding="utf-8")
    runtime = (ROOT / "config" / "runtime.yaml").read_text(encoding="utf-8")
    wrapper = (ROOT / "services" / "phone-ui" / "server_v2.py").read_text(encoding="utf-8")

    assert 'memory_cfg.get("cache_first", True)' in guard
    assert "comfy.free_memory(False, True)" in guard
    assert "comfy.free_memory(True, True)" in guard
    assert 'memory_cfg.get("cache_grace_seconds", 15)' in guard
    assert "cache_first: true" in runtime
    assert "cache_grace_seconds: 15" in runtime
    assert "_cache_first_free" in wrapper
    assert "_real_free_memory(False, free_memory_flag)" in wrapper
    assert '_real_free_memory(not cache_only, True)' in wrapper
