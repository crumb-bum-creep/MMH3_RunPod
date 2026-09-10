from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memory_guard_is_cache_first_and_can_escalate_without_patching_enqueue_path():
    guard = (ROOT / "runtime" / "mmh3" / "memory_guard.py").read_text(encoding="utf-8")
    runtime = (ROOT / "config" / "runtime.yaml").read_text(encoding="utf-8")
    wrapper = (ROOT / "services" / "phone-ui" / "server_v2.py").read_text(encoding="utf-8")
    legacy = (ROOT / "services" / "phone-ui" / "server.py").read_text(encoding="utf-8")

    # Background memory protection remains staged/cache-first.
    assert 'memory_cfg.get("cache_first", True)' in guard
    assert "comfy.free_memory(False, True)" in guard
    assert "comfy.free_memory(True, True)" in guard
    assert 'memory_cfg.get("cache_grace_seconds", 15)' in guard
    assert "cache_first: true" in runtime
    assert "cache_grace_seconds: 15" in runtime

    # The System-tab manual controls still support cache-only and full release.
    assert '_real_free_memory(not cache_only, True)' in wrapper

    # Crucially, the continuation must not monkey-patch comfy.free_memory. The
    # d2103 generation-admission path needs its original immediate full release
    # when a user attempts to enqueue under pressure, otherwise /api/generate can
    # reject before the prompt ever reaches Comfy.
    assert "base.comfy.free_memory =" not in wrapper
    assert "comfy.free_memory(True, True)" in legacy
