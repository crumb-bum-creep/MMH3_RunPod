import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
WF = ROOT / "workflows" / "api"

EXPECTED = {
    "t2v_auto.json",
    "t2v_custom.json",
    "i2v_auto.json",
    "i2v_custom.json",
    "r2v_auto.json",
    "r2v_custom.json",
}


def load(name):
    return json.loads((WF / name).read_text())


def nodes_of(graph, cls):
    return [n for n in graph.values() if isinstance(n, dict) and n.get("class_type") == cls]


def titles(graph, title):
    return [
        n for n in graph.values()
        if isinstance(n, dict)
        and (n.get("_meta") or {}).get("title") == title
    ]


def test_exact_workflow_set():
    assert {p.name for p in WF.glob("*.json")} == EXPECTED


def test_all_links_reference_existing_nodes():
    for name in EXPECTED:
        graph = load(name)
        ids = set(graph)
        for nid, node in graph.items():
            if not isinstance(node, dict):
                continue
            for value in (node.get("inputs") or {}).values():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    assert value[0] in ids, f"{name}: {nid} links to missing node {value[0]}"


def test_core_h3_contract():
    for name in EXPECTED:
        graph = load(name)
        assert nodes_of(graph, "ResolutionSelector"), name
        assert nodes_of(graph, "RandomNoise"), name
        assert nodes_of(graph, "Power Lora Loader (rgthree)"), name
        assert nodes_of(graph, "LoraLoaderModelOnly"), name
        assert nodes_of(graph, "VHS_VideoCombine"), name

    for name in ("t2v_auto.json", "t2v_custom.json", "i2v_auto.json", "i2v_custom.json"):
        graph = load(name)
        assert nodes_of(graph, "MiniMaxH3ImageToVideo"), name

    for name in ("r2v_auto.json", "r2v_custom.json"):
        graph = load(name)
        assert nodes_of(graph, "MiniMaxH3ReferencePack"), name
        assert nodes_of(graph, "MiniMaxH3ReferenceToVideo"), name


def test_auto_openrouter_schema_is_not_legacy_false_zero():
    for name in ("t2v_auto.json", "i2v_auto.json"):
        graph = load(name)
        ors = nodes_of(graph, "OpenRouterNode")
        assert len(ors) == 1
        inputs = ors[0]["inputs"]
        assert inputs["aspect_ratio"] == "auto"
        assert inputs["image_resolution"] == "1K"
        assert isinstance(inputs["seed"], int)
        assert 0 <= inputs["seed"] <= 2_147_483_647


def test_custom_r2v_has_no_openrouter_api_key_node():
    graph = load("r2v_custom.json")
    assert not titles(graph, "OpenRouter API Key")
    pack = nodes_of(graph, "MiniMaxH3ReferencePack")[0]["inputs"]
    assert pack["prompt_provider"] == "none"
    assert pack["openrouter_api_key"] == ""
    assert pack["job_type"] == "standard"


def test_phone_ui_custom_r2v_uses_valid_referencepack_enum():
    source = (ROOT / "services" / "phone-ui" / "server.py").read_text()
    assert 'inp["prompt_provider"] = "none"' in source
    assert 'inp["job_type"] = "standard"' in source
    assert 'inp["job_type"] = "custom"' not in source


def test_r2v_auto_uses_referencepack_openrouter():
    graph = load("r2v_auto.json")
    pack = nodes_of(graph, "MiniMaxH3ReferencePack")[0]["inputs"]
    assert pack["prompt_provider"] == "openrouter"
    assert pack["job_type"] == "auto"
    assert titles(graph, "OpenRouter API Key")


def test_workflows_do_not_contain_embedded_credentials():
    suspicious = re.compile(r"(sk-or-|OPENROUTER_API_KEY\s*=|civitai[_-]?token\s*=)", re.I)
    for name in EXPECTED:
        raw = (WF / name).read_text()
        assert not suspicious.search(raw), name


def test_phone_ui_has_r2v_cache_nonce():
    source = (ROOT / "services" / "phone-ui" / "server.py").read_text()
    assert 'inp["local_model_slug"] = f"mmh3-cache-{random_openrouter_seed()}"' in source


def test_all_workflows_expose_ksampler_select():
    for name in EXPECTED:
        graph = load(name)
        samplers = nodes_of(graph, "KSamplerSelect")
        assert len(samplers) == 1, name
        assert isinstance(samplers[0]["inputs"]["sampler_name"], str)


def test_r2v_workflows_use_persistent_asset_selector_for_comfy_ui():
    for name in ("r2v_auto.json", "r2v_custom.json"):
        graph = load(name)
        selectors = nodes_of(graph, "MMH3AssetReferenceSelector")
        assert len(selectors) == 1, name
        selector_id = next(nid for nid, node in graph.items() if node is selectors[0])
        pack = nodes_of(graph, "MiniMaxH3ReferencePack")[0]
        assert pack["inputs"]["references_json"] == [selector_id, 0]
        for i in range(1, 10):
            assert selectors[0]["inputs"][f"picture_{i}"] == "(none)"
        for i in range(1, 4):
            assert selectors[0]["inputs"][f"video_{i}"] == "(none)"
            assert selectors[0]["inputs"][f"audio_{i}"] == "(none)"
