# RunPod Deployment

MMH3 targets NVIDIA RunPod Pods with a persistent volume mounted at `/workspace`.

## Exposed HTTP ports

- `7860/http` — MMH3 Phone UI
- `8188/http` — ComfyUI
- `8888/http` — JupyterLab

## Secrets

Create secrets in RunPod, not in Git:

| Secret | Purpose | Required |
|---|---|---|
| `OPENROUTER_API_KEY` | Auto-prompt generation | Yes for Auto workflows |
| `CIVITAI_TOKEN` | Managed CivitAI LoRA downloads | Optional |
| `HF_TOKEN` | Hugging Face model downloads | Recommended |
| `JUPYTER_PASSWORD` | Jupyter authentication | Recommended |

Map them to the environment variables described in `docs/FIRST_RUNPOD_DEPLOY.md`.

## Image policy

Use immutable GHCR `sha-...` tags for normal templates. Do not pin a long-lived production template to `latest`.

The historical production input to vNext is:

`ghcr.io/crumb-bum-creep/mmh3-runpod:sha-9d134f338f53`

The vNext candidate must pass the checklist in `docs/DEVELOPMENT_HANDOFF.md` before replacing that tag in a production template.

## Storage

Keep models, inputs, outputs and MMH3 user/configuration state on the persistent `/workspace` volume. Application code remains image-local and disposable.

A network volume is useful when Pods are frequently destroyed/recreated; a normal Pod volume remains supported when GPU/location flexibility matters more.

## Hardware/runtime truth

MMH3 uses direct GPU and cgroup probes as the source of truth for VRAM/RAM policy. RunPod environment variables are supplemental metadata only.

The primary validated high-memory target is the RTX PRO 6000 Blackwell class. See `docs/KNOWN_GOOD_AND_MEMORY.md` for captured hardware/runtime details and `docs/OPERATIONS.md` for health/recovery commands.
