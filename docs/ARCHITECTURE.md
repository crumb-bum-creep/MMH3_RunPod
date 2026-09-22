# MMH3 Architecture

## Design principles

1. **Git is the source of truth** for image code, canonical workflows, default configuration, and model metadata.
2. **Persistent user state lives under /workspace** and survives container/image replacement.
3. **Secrets stay outside Git** and are injected by RunPod at runtime.
4. **Large model files and generated media are never baked into the application image.**
5. **Generation recipes are data**, defined in `config/generation_profiles.yaml`, with runtime overrides validated by MMH3.
6. **Hardware and memory policy are selected from direct runtime/cgroup probes.**
7. **Services are independently restartable** so Comfy recovery does not require recreating the Pod.
8. **Migration repair is automatic**; a normal image change must not require editing files under `/opt`.

## Runtime services

| Service | Port | Purpose |
|---|---:|---|
| ComfyUI | 8188 | Generation backend |
| MMH3 Phone UI | 7860 | Primary control plane |
| JupyterLab | 8888 | Maintenance / file access |
| Memory guard | internal | cgroup-aware cache/model protection |
| Provisioner | background one-shot/retry | model + managed LoRA verification/download |

## Phone UI layout

`services/phone-ui/server.py` is the canonical service entrypoint.

`server_core.py` contains reusable baseline handlers and workflow plumbing. The entrypoint composes explicit route-handler overrides into the core app; it does not rename/copy Python files or monkey-patch the core module at container startup.

Generation-specific configuration is resolved through:

- `config/generation_profiles.yaml`
- `runtime/mmh3/generation_profiles.py`

Live/persistent user controls are stored separately from image defaults.

## Persistent storage

The RunPod volume mount is `/workspace`.

Important state includes:

- `/workspace/ComfyUI/models`
- `/workspace/ComfyUI/input`
- `/workspace/ComfyUI/output`
- `/workspace/ComfyUI/user`
- `/workspace/mmh3/config`
- `/workspace/mmh3/data`
- `/workspace/mmh3/state`
- `/workspace/mmh3/logs`

Application/runtime source remains reproducible under `/opt/mmh3` and `/ComfyUI`.

## Startup sequence

Bootstrap runs before the supervisor and:

1. creates persistent directories;
2. migrates/merges versioned runtime configuration;
3. writes Comfy persistent model paths;
4. inventories existing local models without network access;
5. installs the six canonical workflows;
6. records hardware/provisioning state.

The supervisor then:

1. starts the Phone UI and Comfy;
2. reasserts persistent model paths immediately before every Comfy launch;
3. on a cold volume, starts background provisioning after a short grace while Comfy initializes;
4. on a warm/migrated volume, lets Comfy become healthy first, then starts background verification after a short grace;
5. verifies that Comfy can actually see ready persistent model filenames and repairs a stale selector cache with a Comfy-only restart when necessary;
6. starts Jupyter after Comfy health;
7. keeps the memory guard and service-restart loop active.

Provisioning that adds model files requests a safe Comfy-only refresh when the queue becomes idle.

## Generation admission

Phone UI queueing validates the actual `VHS_VideoCombine` output branch. A response containing Comfy `node_errors` is a failed queue attempt, never a completed generation.

Memory Protection defaults ON and can be disabled intentionally from the System tab. When ON, MMH3 blocks unsafe cross-family stacking and applies the cgroup-aware background memory policy. When OFF, those automatic protections are bypassed.

## Output library

The Outputs UI is backed by a persisted index under:

`/workspace/ComfyUI/output/.mmh3/output-index.json`

Warm starts serve the prior index immediately. Filesystem reconciliation happens in the background rather than recursively walking the full output tree on every request or immediately competing with Comfy at startup.

See `docs/DEVELOPMENT_HANDOFF.md` for the active vNext acceptance checklist and historical 9d defects.
