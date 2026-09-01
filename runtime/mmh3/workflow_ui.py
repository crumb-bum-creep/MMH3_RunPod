from __future__ import annotations

import json
import math
import urllib.request
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .common import COMFY_PERSIST, IMAGE_ROOT

CANONICAL = (
    "t2v_auto.json",
    "t2v_custom.json",
    "i2v_auto.json",
    "i2v_custom.json",
    "r2v_auto.json",
    "r2v_custom.json",
)


def _fetch_object_info(comfy_url: str) -> dict[str, Any]:
    with urllib.request.urlopen(comfy_url.rstrip("/") + "/object_info", timeout=30) as response:
        value = json.loads(response.read().decode("utf-8"))
    return value if isinstance(value, dict) else {}


def _input_order(info: dict[str, Any]) -> list[str]:
    explicit = info.get("input_order")
    if isinstance(explicit, dict):
        out = []
        for group in ("required", "optional"):
            values = explicit.get(group) or []
            if isinstance(values, list):
                out.extend(str(x) for x in values)
        if out:
            return out
    out = []
    inputs = info.get("input") or {}
    for group in ("required", "optional"):
        values = inputs.get(group) or {}
        if isinstance(values, dict):
            out.extend(str(x) for x in values)
    return out


def _input_spec(info: dict[str, Any], name: str) -> Any:
    inputs = info.get("input") or {}
    for group in ("required", "optional", "hidden"):
        values = inputs.get(group) or {}
        if isinstance(values, dict) and name in values:
            return values[name]
    return None


def _type_name(spec: Any) -> str:
    if isinstance(spec, (list, tuple)) and spec:
        first = spec[0]
        if isinstance(first, str):
            return first
        if isinstance(first, (list, tuple)):
            return "COMBO"
    return "*"


def _is_widget_spec(spec: Any) -> bool:
    if not isinstance(spec, (list, tuple)) or not spec:
        return False
    first = spec[0]
    return first in {"INT", "FLOAT", "STRING", "BOOLEAN"} or isinstance(first, (list, tuple))


def _default_widget(spec: Any) -> Any:
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    first = spec[0]
    options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if "default" in options:
        return options["default"]
    if isinstance(first, (list, tuple)):
        return first[0] if first else ""
    return {"INT": 0, "FLOAT": 0.0, "STRING": "", "BOOLEAN": False}.get(first)


def _widget_value(value: Any) -> Any:
    # LiteGraph widget serialization can safely carry the same JSON values that
    # the API prompt uses (including rgthree's small widget dictionaries).
    return value


def _topological_positions(graph: dict[str, Any]) -> dict[str, list[float]]:
    parents: dict[str, set[str]] = {str(k): set() for k in graph}
    children: dict[str, set[str]] = {str(k): set() for k in graph}
    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        for value in (node.get("inputs") or {}).values():
            if isinstance(value, list) and len(value) == 2 and str(value[0]) in graph:
                parent = str(value[0])
                parents[str(nid)].add(parent)
                children[parent].add(str(nid))

    indegree = {nid: len(values) for nid, values in parents.items()}
    queue = deque(sorted((nid for nid, degree in indegree.items() if degree == 0)))
    depth = {nid: 0 for nid in graph}
    while queue:
        nid = queue.popleft()
        for child in children.get(nid, ()):
            depth[child] = max(depth.get(child, 0), depth[nid] + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    by_depth: dict[int, list[str]] = defaultdict(list)
    for nid in graph:
        by_depth[depth.get(str(nid), 0)].append(str(nid))

    positions = {}
    for column, d in enumerate(sorted(by_depth)):
        for row, nid in enumerate(sorted(by_depth[d])):
            positions[nid] = [80.0 + column * 390.0, 80.0 + row * 250.0]
    return positions


def api_graph_to_ui(graph: dict[str, Any], object_info: dict[str, Any]) -> dict[str, Any]:
    id_map = {str(nid): i for i, nid in enumerate(graph.keys(), start=1)}
    positions = _topological_positions(graph)

    # One link row per API connection. Resolve destination slot positions from
    # Comfy's actual node definitions so custom/core nodes stay synchronized.
    links = []
    node_inputs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    output_links: dict[tuple[str, int], list[int]] = defaultdict(list)
    link_id = 1

    for dest_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        info = object_info.get(str(node.get("class_type") or "")) or {}
        order = _input_order(info)
        order_index = {name: i for i, name in enumerate(order)}
        pending_inputs = []
        for name, value in (node.get("inputs") or {}).items():
            if not (isinstance(value, list) and len(value) == 2 and str(value[0]) in graph):
                continue
            src_id, src_slot = str(value[0]), int(value[1])
            spec = _input_spec(info, name)
            input_type = _type_name(spec)
            pending_inputs.append((order_index.get(name, 10_000), str(name), value, spec, input_type))
        pending_inputs.sort(key=lambda item: (item[0], item[1]))

        for dest_slot, (_, name, value, spec, input_type) in enumerate(pending_inputs):
            src_id, src_slot = str(value[0]), int(value[1])
            serialized = {
                "name": name,
                "type": input_type,
                "link": link_id,
            }
            if _is_widget_spec(spec):
                serialized["widget"] = {"name": name}
            node_inputs[str(dest_id)].append(serialized)
            output_links[(src_id, src_slot)].append(link_id)
            src_info = object_info.get(str((graph.get(src_id) or {}).get("class_type") or "")) or {}
            out_types = src_info.get("output") or []
            out_type = str(out_types[src_slot]) if src_slot < len(out_types) else "*"
            links.append([link_id, id_map[src_id], src_slot, id_map[str(dest_id)], dest_slot, out_type])
            link_id += 1

    nodes = []
    for order_index, (raw_id, node) in enumerate(graph.items()):
        if not isinstance(node, dict) or not node.get("class_type"):
            continue
        nid = str(raw_id)
        class_type = str(node["class_type"])
        info = object_info.get(class_type) or {}
        input_order = _input_order(info)
        linked_names = {
            name for name, value in (node.get("inputs") or {}).items()
            if isinstance(value, list) and len(value) == 2 and str(value[0]) in graph
        }

        widgets = []
        for name in input_order:
            spec = _input_spec(info, name)
            if not _is_widget_spec(spec):
                continue
            if name in linked_names:
                widgets.append(_widget_value(_default_widget(spec)))
            elif name in (node.get("inputs") or {}):
                widgets.append(_widget_value(node["inputs"][name]))
            else:
                widgets.append(_widget_value(_default_widget(spec)))

        # Keep any dynamic API-only widgets (notably rgthree LoRAs) after the
        # declared widget sequence instead of silently throwing them away.
        for name, value in (node.get("inputs") or {}).items():
            if name in input_order or name in linked_names:
                continue
            widgets.append(_widget_value(value))

        outputs = []
        out_types = list(info.get("output") or [])
        out_names = list(info.get("output_name") or [])
        max_used = max(
            [slot for (source, slot) in output_links if source == nid] + [-1]
        )
        count = max(len(out_types), max_used + 1)
        for slot in range(count):
            outputs.append({
                "name": str(out_names[slot]) if slot < len(out_names) else f"output_{slot}",
                "type": str(out_types[slot]) if slot < len(out_types) else "*",
                "links": output_links.get((nid, slot)) or None,
                "slot_index": slot,
            })

        inputs = list(node_inputs.get(nid, []))
        approx_rows = max(len(inputs), len(widgets), 2)
        nodes.append({
            "id": id_map[nid],
            "type": class_type,
            "pos": positions.get(nid, [80.0, 80.0]),
            "size": [330.0, float(max(120, min(720, 92 + approx_rows * 26)))],
            "flags": {},
            "order": order_index,
            "mode": 0,
            "inputs": inputs,
            "outputs": outputs,
            "properties": {
                "Node name for S&R": class_type,
                "mmh3_api_node_id": nid,
            },
            "widgets_values": widgets,
            "title": str((node.get("_meta") or {}).get("title") or info.get("display_name") or class_type),
        })

    return {
        "id": "mmh3-generated-workflow",
        "revision": 0,
        "last_node_id": max(id_map.values(), default=0),
        "last_link_id": link_id - 1,
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"ds": {"scale": 0.72, "offset": [40, 40]}, "mmh3_generated": True},
        "version": 0.4,
    }


def install_ui_workflows(comfy_url: str) -> list[str]:
    object_info = _fetch_object_info(comfy_url)
    src = IMAGE_ROOT / "workflows" / "api"
    dst = COMFY_PERSIST / "user" / "default" / "workflows" / "MMH3"
    dst.mkdir(parents=True, exist_ok=True)
    installed = []
    for name in CANONICAL:
        path = src / name
        if not path.exists():
            continue
        graph = json.loads(path.read_text(encoding="utf-8"))
        ui = api_graph_to_ui(graph, object_info)
        pretty = name.replace("_", " ").replace(".json", "").title() + ".json"
        (dst / pretty).write_text(json.dumps(ui, indent=2), encoding="utf-8")
        # Remove the old API-shaped workflow if a previous image installed it.
        legacy = dst / name
        if legacy.exists():
            legacy.unlink()
        installed.append(pretty)
    return installed
