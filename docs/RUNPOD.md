# RunPod Deployment

This document will become the authoritative deployment guide after the known-good environment has been pinned.

## Planned exposed HTTP ports

- `7860/http` — MMH3 Phone UI
- `8188/http` — ComfyUI
- `8888/http` — JupyterLab

RunPod supports HTTP/TCP port declarations on Pods, and the default persistent volume mount is `/workspace`.

## Planned secrets

Create these in RunPod **Secrets**, not in Git:

| Secret | Purpose | Required |
|---|---|---|
| `OPENROUTER_API_KEY` | Auto-prompt generation | Yes for Auto workflows |
| `CIVITAI_TOKEN` | Authenticated LoRA downloads | Optional |
| `HF_TOKEN` | Gated/authenticated Hugging Face downloads | Optional |
| `JUPYTER_PASSWORD` | Jupyter authentication | Recommended |

The production template will map those secrets into environment variables.

## Storage

Use a persistent Pod volume or a network volume mounted at `/workspace`.

Keep model weights and outputs under `/workspace` so they survive container replacement.

## Current next step

Before locking the Docker image and startup command, capture the exact environment from the current known-good H3 Pod:

```bash
bash scripts/capture_known_good.sh
```

That capture lets us pin the exact ComfyUI commit, Python/PyTorch stack, and custom-node revisions instead of guessing.
