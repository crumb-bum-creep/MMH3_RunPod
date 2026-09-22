# MMH3 RunPod

A reproducible RunPod environment for MiniMax H3 video generation.

## Goal

Deploy a fresh Pod and have the MMH3 stack come up ready to use:

- ComfyUI on **8188**
- MMH3 Phone UI on **7860**
- JupyterLab on **8888**
- Canonical T2V / I2V / R2V workflows
- Hardware-aware launch settings for RTX 5090 and RTX 6000 Pro-class machines
- LoRA catalog with friendly names, recommended strengths, trigger words, and notes
- Model / custom-node validation
- Memory monitoring and service recovery
- Secrets injected at runtime rather than stored in workflows or Git

## Repository layout

```text
config/        Runtime configuration and hardware profiles
docs/          Deployment and operations documentation
scripts/       Bootstrap, diagnostics, service management
services/      MMH3 services such as the phone UI
workflows/     Canonical ComfyUI API workflows
loras/         LoRA metadata catalog (not model weights)
.github/       CI / image build automation
```

## Current status

The last deployed production input to the current cleanup is:

```text
ghcr.io/crumb-bum-creep/mmh3-runpod:sha-9d134f338f53
```

Active development is on `next/vnext-runtime-cleanup` / draft PR #28. vNext turns the migration/runtime fixes proven against the 9d image into first-class behavior: migration-safe Comfy model visibility, real video-output validation, warm-start-friendly output indexing, configurable generation recipes, Memory Protection controls, configurable output naming, Reuse Exact, runtime-config migration, and a simplified Phone UI/server layout.

Do not treat vNext as production until its candidate image passes the acceptance checklist in `docs/DEVELOPMENT_HANDOFF.md`.

See:

- `docs/DEVELOPMENT_HANDOFF.md` — current architecture, known 9d defects, vNext decisions, and promotion checklist
- `docs/FIRST_RUNPOD_DEPLOY.md` — RunPod setup
- `docs/OPERATIONS.md` — daily commands/recovery
- `scripts/first_boot_check.sh` — one-command first-pod health check
- `docs/PERSISTENCE.md` — persistent state and LoRA seed behavior


## Security

Never commit API keys, tokens, or passwords. OpenRouter, CivitAI, Hugging Face, and other credentials are supplied as RunPod secrets / environment variables.
