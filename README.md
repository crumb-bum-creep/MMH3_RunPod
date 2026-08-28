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

**MMH3 v0.6 production runtime validated and published.**

Immutable production image:

```text
ghcr.io/crumb-bum-creep/mmh3-runpod:sha-d2103b98e602
```

Validated in GitHub CI:

- runtime/unit/workflow-contract tests
- phone UI JavaScript syntax
- CUDA 13 / PyTorch 2.11 image build
- pinned ComfyUI commit
- six canonical H3 API workflows
- MMH3 runtime imports
- ONNX Runtime CUDA provider
- ComfyUI custom-node initialization via `--quick-test-for-ci`
- GHCR push

The runtime architecture was also validated on a real RTX 5090 with CUDA availability, FP16 CUDA matmul, required H3 nodes, automatic provisioning, and a successful H3 generation. v0.6 additionally includes the optimized startup image, persistent per-mode phone-UI drafts, the asset library, compact LoRA management, separated generation-side LoRA selection, and explicit queue ordering.

See:

- `docs/FIRST_RUNPOD_DEPLOY.md` — exact RunPod setup
- `docs/OPERATIONS.md` — daily commands/recovery
- `scripts/first_boot_check.sh` — one-command first-pod health check
- `docs/PERSISTENCE.md` — persistent state and LoRA seed behavior


## Security

Never commit API keys, tokens, or passwords. OpenRouter, CivitAI, Hugging Face, and other credentials are supplied as RunPod secrets / environment variables.
