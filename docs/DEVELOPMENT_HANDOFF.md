# MMH3 Development Handoff

> **Canonical baseline reset:** 2026-09-10  
> **Repository:** `crumb-bum-creep/MMH3_RunPod`  
> **Development lineage:** continue from the validated production snapshot below, not from the later post-d2103 experiments.

## Canonical production baseline

The baseline for all new MMH3 development is the validated Phone UI v0.6 / optimized-runtime merge:

```text
commit: d2103b98e60242125b825b60efc726b974847230
image:  ghcr.io/crumb-bum-creep/mmh3-runpod:sha-d2103b98e602
```

The post-d2103 commits that previously accumulated on `main` are historical reference material only. Do **not** wholesale rebase or merge them back into the active lineage. Port a specific later fix only after verifying that it is still wanted and does not regress this baseline.

## Current continuation work

The first continuation from d2103 focuses on Phone UI usability and memory behavior while preserving the six known-good generation workflows and runtime stack.

### Indexed generation library

The old `/api/outputs` request recursively scanned every MP4, ran audio detection where necessary, statted everything, sorted everything, and then returned the list. With hundreds of videos that made the Outputs page increasingly expensive.

The continuation uses a background-derived index instead:

```text
user outputs:  /workspace/ComfyUI/output
cached index:  /workspace/ComfyUI/output/.mmh3/output-index.json
```

`services/phone-ui/output_indexer.py` watches output-directory mtimes and rebuilds the index only when output directories change, with a periodic safety rescan. Request-time `/api/outputs` reads the cached JSON index instead of walking the filesystem. Clients can send `?since=<index_updated_at>` and receive a tiny `unchanged` response when nothing changed.

Only completed H3/VHS muxed MP4s using the normal `audio` filename convention are cataloged. `MMH3Director` outputs are excluded.

### Compact Outputs UI

The v0.6 vertical `<details>` list is retained only as historical implementation code; the active continuation overlays a compact library UI:

- responsive thumbnail grid;
- 48 cards rendered initially, with Show More pagination;
- lazy image previews instead of one live `<video>` element per output;
- one detail dialog / video player created only when a card is opened;
- search across filenames, prompts, groups and tags;
- single group/folder assignment per video;
- multiple free-form tags per video;
- independent Favorite flag;
- group + tag + Favorites filters compose together.

Persistent user organization metadata lives at:

```text
/workspace/mmh3/data/output_library.json
```

The derived output index can be regenerated; `output_library.json` is user-owned persistent state and must not be treated as disposable cache.

### Always-visible generation progress

While a generation is queued or running, a compact fixed progress strip is visible on every tab. It contains:

- current-process progress bar + percent;
- overall-workflow progress bar + percent.

It intentionally does not show step numbers or consume significant screen height.

### Cache-first memory protection

The supervisor memory guard uses a staged cache-first policy during ordinary idle pressure:

1. request `unload_models=false, free_memory=true` first;
2. keep the hot model loaded and allow a short grace period (`cache_grace_seconds`, default 15s);
3. if pressure remains, escalate to `unload_models=true, free_memory=true`;
4. critical-memory interrupt behavior remains governed by the existing hardware/runtime policy.

The d2103 **generation-admission path is intentionally not monkey-patched**. If a user attempts to enqueue while host memory is already above the admission threshold, `/api/generate` retains the original d2103 behavior and may immediately request a full Comfy model/cache release before posting the prompt. This is deliberate: the background guard can optimize idle memory behavior, but enqueue must not stall on a cache-only cleanup and then reject the prompt before it reaches Comfy.

The System tab separately exposes **Clear cache** and **Unload models + cache**.

Default config additions:

```yaml
memory:
  cache_first: true
  cache_grace_seconds: 15
```

## Runtime compatibility layer

The goal is to continue from d2103 rather than rewrite it invisibly. The image still contains the known-good d2103 `services/phone-ui/server.py` at build time. On container startup, when `server_v2.py` is present, `runtime/entrypoint.sh` preserves the original as `server_legacy.py` and installs the continuation wrapper as the supervised `server.py`.

This makes the d2103 server an explicit rollback/reference layer while allowing the new output-library and cache-management behavior to extend it.

## Contracts that must not regress

- Phone UI: port 7860
- ComfyUI: port 8188
- JupyterLab: port 8888
- Persistent storage remains under `/workspace`.
- Application/runtime source remains image-local under `/opt/mmh3` and `/ComfyUI`.
- Exactly six canonical API generation workflows remain supported: T2V Auto/Custom, I2V Auto/Custom, R2V Auto/Custom.
- Custom R2V must not invoke Auto/OpenRouter prompt generation.
- Secrets must never be committed to Git.
- `MMH3Director` content must not be mixed into the normal generated-video library.
- Do not reintroduce request-time recursive output scanning or a live `<video>` element for every generation card.
- Prefer cache clearing before model unloading during ordinary idle memory pressure; retain full unload as escalation.
- Do not globally patch the d2103 Phone UI `comfy.free_memory` function; user-triggered generation admission must retain the baseline full-release escape path when already under pressure.

## Development rule from here

Treat the d2103 continuation lineage as authoritative. Before bringing over any code from the former post-d2103 `main`, inspect the exact change and port only the desired behavior intentionally. Do not assume a numerically newer commit is a better MMH3 baseline.
