from __future__ import annotations

import importlib
import sys
from pathlib import Path


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


def test_output_naming_defaults_to_existing_scheme(monkeypatch):
    wrapper = load_wrapper()
    monkeypatch.setattr(
        wrapper,
        "load_controls",
        lambda: {"memory_protection": True, "output_naming_template": "MMH3/{mode}"},
    )
    prefix, template = wrapper._output_prefix(
        {},
        {"mode": "r2v", "prompt_mode": "auto", "seed": 123},
        "tuned",
        "stock_convrot_int8",
    )
    assert prefix == "MMH3/R2V"
    assert template == "MMH3/{mode}"


def test_output_naming_supports_tokens_and_sanitizes(monkeypatch):
    wrapper = load_wrapper()
    monkeypatch.setattr(
        wrapper,
        "load_controls",
        lambda: {
            "memory_protection": True,
            "output_naming_template": "Tests/{mode}/{profile}/{seed}",
        },
    )
    prefix, _ = wrapper._output_prefix(
        {},
        {"mode": "i2v", "prompt_mode": "custom", "seed": 44},
        "fast",
        "stock_convrot_int8",
    )
    assert prefix == "Tests/I2V/fast/44"


def test_reuse_exact_static_contract():
    app = (PHONE_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    library = (PHONE_ROOT / "static" / "library-v2.js").read_text(encoding="utf-8")
    assert "function reuseExactSnapshot" in app
    assert 'prompt_mode:"custom"' in app
    assert "actual_prompt||v.prompt||v.prompt_idea" in app
    assert "randomize_seed:false" in app
    assert 'generation_profile:v.generation_profile||"legacy_exact"' in app
    assert "historicalFallback=!v.generation_settings" in app
    assert "Reuse Exact" in library
    assert "reuseExactSnapshot(m)" in library


def test_memory_protection_runtime_control_is_wired():
    wrapper = (PHONE_ROOT / "server.py").read_text(encoding="utf-8")
    guard = (ROOT / "runtime" / "mmh3" / "memory_guard.py").read_text(encoding="utf-8")
    ui = (PHONE_ROOT / "static" / "features-v3.js").read_text(encoding="utf-8")
    assert 'app.router.add_get("/api/runtime-controls"' in wrapper
    assert 'app.router.add_put("/api/runtime-controls"' in wrapper
    assert "if memory_protection and active_families" in wrapper
    assert "memory_protection_enabled" in guard
    assert "memoryProtectionToggle" in ui


def test_phone_ui_has_one_canonical_entrypoint():
    phone = PHONE_ROOT
    entrypoint = (ROOT / "runtime" / "entrypoint.sh").read_text(encoding="utf-8")
    assert (phone / "server.py").is_file()
    assert (phone / "server_core.py").is_file()
    assert not (phone / "server_v2.py").exists()
    assert "server_legacy.py" not in entrypoint
    assert "server_v2.py" not in entrypoint
