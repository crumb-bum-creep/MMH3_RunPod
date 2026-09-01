import json
from pathlib import Path

from mmh3 import workflow_ui

ROOT = Path(__file__).resolve().parents[1]


def fake_object_info(graph):
    info = {}
    # Sufficient schema for converter structure tests: every API input gets a
    # declared widget type unless it is visibly connected to another node.
    for node in graph.values():
        if not isinstance(node, dict) or not node.get("class_type"):
            continue
        required = {}
        order = []
        for name, value in (node.get("inputs") or {}).items():
            order.append(name)
            if isinstance(value, bool):
                spec = ["BOOLEAN", {"default": value}]
            elif isinstance(value, int):
                spec = ["INT", {"default": value}]
            elif isinstance(value, float):
                spec = ["FLOAT", {"default": value}]
            elif isinstance(value, str):
                spec = ["STRING", {"default": value}]
            else:
                spec = ["*", {}]
            required[name] = spec
        outputs_used = []
        for other in graph.values():
            for value in (other.get("inputs") or {}).values():
                if isinstance(value, list) and len(value) == 2:
                    outputs_used.append(int(value[1]))
        count = max(outputs_used + [0]) + 1
        info[node["class_type"]] = {
            "input": {"required": required},
            "input_order": {"required": order},
            "output": ["*"] * count,
            "output_name": [f"output_{i}" for i in range(count)],
        }
    return info


def test_api_to_ui_workflow_has_drawable_nodes_and_links():
    graph = json.loads((ROOT / "workflows" / "api" / "r2v_custom.json").read_text())
    ui = workflow_ui.api_graph_to_ui(graph, fake_object_info(graph))

    assert ui["version"] == 0.4
    assert len(ui["nodes"]) == len(graph)
    assert ui["links"]
    assert all("pos" in node and "size" in node for node in ui["nodes"])
    assert any(node["type"] == "ModelPreviewOverrideKJ" for node in ui["nodes"])
    assert any(node["type"] == "KSamplerSelect" for node in ui["nodes"])
    assert any(node["type"] == "MMH3AssetReferenceSelector" for node in ui["nodes"])


def test_generated_workflow_uses_sequential_litegraph_input_slots():
    graph = {
        "a": {"inputs": {"value": 1}, "class_type": "Source", "_meta": {"title": "Source"}},
        "b": {
            "inputs": {"first": ["a", 0], "middle": "widget", "last": ["a", 0]},
            "class_type": "Dest",
            "_meta": {"title": "Dest"},
        },
    }
    info = {
        "Source": {
            "input": {"required": {"value": ["INT", {"default": 1}]}},
            "input_order": {"required": ["value"]},
            "output": ["INT"],
            "output_name": ["value"],
        },
        "Dest": {
            "input": {"required": {
                "first": ["INT", {"default": 0}],
                "middle": ["STRING", {"default": ""}],
                "last": ["INT", {"default": 0}],
            }},
            "input_order": {"required": ["first", "middle", "last"]},
            "output": [],
            "output_name": [],
        },
    }
    ui = workflow_ui.api_graph_to_ui(graph, info)
    dest = next(node for node in ui["nodes"] if node["type"] == "Dest")
    assert [x["name"] for x in dest["inputs"]] == ["first", "middle", "last"]
    assert [x["link"] for x in dest["inputs"]] == [1, None, 2]
    assert dest["inputs"][1]["widget"] == {"name": "middle"}
    target_slots = [link[4] for link in ui["links"]]
    assert target_slots == [0, 2]


def test_bootstrap_no_longer_installs_api_json_as_comfy_workflows():
    source = (ROOT / "runtime" / "mmh3" / "bootstrap.py").read_text()
    assert 'drawable workflows generated after Comfy startup' in source
    assert 'shutil.copy2(p, dst / p.name)' not in source


def test_generated_nodes_serialize_declared_unlinked_widget_inputs():
    graph = {
        "1": {
            "inputs": {"sampler_name": "euler"},
            "class_type": "KSamplerSelect",
            "_meta": {"title": "Sampler"},
        }
    }
    info = {
        "KSamplerSelect": {
            "input": {"required": {"sampler_name": [["euler", "seeds_2"], {"default": "euler"}]}},
            "input_order": {"required": ["sampler_name"]},
            "output": ["SAMPLER"],
            "output_name": ["sampler"],
        }
    }
    ui = workflow_ui.api_graph_to_ui(graph, info)
    sampler = ui["nodes"][0]
    assert sampler["inputs"] == [{
        "name": "sampler_name",
        "type": "COMBO",
        "link": None,
        "slot_index": 0,
        "widget": {"name": "sampler_name"},
    }]
    assert sampler["widgets_values"] == ["euler"]
