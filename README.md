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

**Phase 1 — environment capture and reproducible bootstrap.**

The first step is to capture exact package, CUDA, ComfyUI, custom-node, and model-path details from a known-good H3 Pod before pinning the production image. This avoids guessing at versions that are already known to work.

Run `scripts/capture_known_good.sh` on a functioning H3 Pod and save the generated archive. The resulting manifest is used to pin the MMH3 image.

## Security

Never commit API keys, tokens, or passwords. OpenRouter, CivitAI, Hugging Face, and other credentials are supplied as RunPod secrets / environment variables.
