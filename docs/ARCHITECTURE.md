# MMH3 Architecture

## Design principles

1. **Git is the source of truth** for code, workflows, configuration, and metadata.
2. **RunPod is the deployment target**, not the configuration database.
3. **Secrets stay outside Git** and are injected at runtime.
4. **Large model files and generated media live on persistent storage**, not in the application image.
5. **Workflows describe generation graphs; runtime values are injected by MMH3 services.**
6. **Hardware policy is selected at startup** from detected GPU and system RAM.
7. **Services are independently restartable** so a ComfyUI crash does not require recreating the Pod.

## Runtime services

| Service | Port | Purpose |
|---|---:|---|
| ComfyUI | 8188 | Generation backend |
| MMH3 Phone UI | 7860 | Primary mobile control plane |
| JupyterLab | 8888 | Maintenance / file access |

## Persistent storage

The target RunPod volume mount is `/workspace`.

Persistent data includes:

- model weights
- custom LoRAs
- input/reference files
- generated output
- LoRA user metadata
- templates/presets
- generation metadata

Code can be recreated from Git and should not be treated as irreplaceable state.

## Startup sequence

1. Detect GPU and RAM.
2. Select a hardware profile.
3. Validate persistent paths.
4. Validate required models.
5. Validate custom-node versions.
6. Install canonical workflows.
7. Start ComfyUI.
8. Wait for ComfyUI health.
9. Start Phone UI.
10. Start/verify JupyterLab.
11. Start memory/service watchdog.
