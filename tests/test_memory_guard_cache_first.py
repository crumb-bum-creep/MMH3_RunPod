from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memory_guard_is_cache_first_and_enqueue_is_side_effect_free():
    guard = (ROOT / "runtime" / "mmh3" / "memory_guard.py").read_text(encoding="utf-8")
    runtime = (ROOT / "config" / "runtime.yaml").read_text(encoding="utf-8")
    wrapper = (ROOT / "services" / "phone-ui" / "server_v2.py").read_text(encoding="utf-8")

    # Background memory protection remains staged/cache-first.
    assert 'memory_cfg.get("cache_first", True)' in guard
    assert "comfy.free_memory(False, True)" in guard
    assert "comfy.free_memory(True, True)" in guard
    assert 'memory_cfg.get("cache_grace_seconds", 15)' in guard
    assert "cache_first: true" in runtime
    assert "cache_grace_seconds: 15" in runtime

    # The System-tab manual controls still support cache-only and full release.
    assert '_real_free_memory(not cache_only, True)' in wrapper

    # The active continuation owns /api/generate and must POST to Comfy without
    # invoking cleanup or waiting for memory to cross an ordinary-pressure gate.
    assert "base.api_generate = api_generate" in wrapper
    start = wrapper.index("async def api_generate")
    end = wrapper.index("async def index", start)
    enqueue = wrapper[start:end]
    assert 'f"{app[\'comfy\']}/prompt"' in enqueue
    assert "free_memory(" not in enqueue
    assert "queue_idle(" not in enqueue
    assert "deadline" not in enqueue
    assert "critical_fraction" in enqueue
    assert "memory_is_fresh" in enqueue
