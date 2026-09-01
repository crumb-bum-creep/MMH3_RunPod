# MMH3 Development Handoff

> **Purpose:** This file is the continuity document for starting a fresh development chat/thread.  
> **Last reviewed:** 2026-09-01  
> **Repository:** `crumb-bum-creep/MMH3_RunPod`  
> **Default branch:** `main`

If a fresh assistant is taking over, **read this file first, then inspect the current `main` branch before making changes.** Treat the repository and current docs as authoritative when they conflict with chat history.

---

## 1. Current production snapshot

Current `main` head when this handoff was written:

```text
5cd3ff20d45b2e4cb84d336a4ad4e720a52a0e7d
```

The production runtime code was promoted in PR #14:

```text
PR #14: Promote optimized runtime and phone UI v0.6
merge commit: d2103b98e60242125b825b60efc726b974847230
```

Two documentation-only commits followed that merge and point deployment docs at the production image.

### Production image

```text
ghcr.io/crumb-bum-creep/mmh3-runpod:sha-d2103b98e602
```

Registry digest from the successful production build:

```text
sha256:f04fec0420e577c2a1c14936a75b00fd71c72a14d0c7d75705102145a22a6aee
```

Use the immutable `sha-d2103b98e602` tag for normal RunPod deployment. Do not move the production template back to an older tag unless intentionally rolling back.

The prior known-good production image, before startup optimization, was:

```text
ghcr.io/crumb-bum-creep/mmh3-runpod:sha-7200522bcf6e
```

That older image is useful only as a rollback/reference point.

---

## 2. What this project is

MMH3 is a reproducible RunPod environment for MiniMax H3 video generation.

Primary services:

- MMH3 Phone UI: **7860**
- ComfyUI: **8188**
- JupyterLab: **8888**

Canonical generation modes:

- T2V Auto
- T2V Custom
- I2V Auto
- I2V Custom
- R2V Auto
- R2V Custom

The design goal is that a fresh RunPod can start from one pinned image, mount persistent storage at `/workspace`, automatically provision missing H3 models/LoRAs, and expose the phone-first control UI without manual Comfy setup.

---

## 3. Main architectural rule

Keep **application/runtime code immutable in the image** and keep **expensive or user-owned state persistent under `/workspace`**.

Image-local:

```text
/ComfyUI
/opt/mmh3
/opt/venv
```

Persistent:

```text
/workspace/ComfyUI/models
/workspace/ComfyUI/input
/workspace/ComfyUI/output
/workspace/ComfyUI/user

/workspace/mmh3/config
/workspace/mmh3/data
/workspace/mmh3/logs
/workspace/mmh3/state
```

Do not move the whole `/ComfyUI` source tree onto persistent storage. Models and user data belong on `/workspace`; application source should remain reproducible from the image.

See `docs/PERSISTENCE.md` and `docs/TEMPLATE_RUNTIME_FINDINGS.md`.

---

## 4. Startup optimization effort

The major problem was not Comfy itself. RunPod frequently spent a long time at the pre-container stage fetching/extracting the custom image. There were no MMH3/Tini/Python logs yet, which showed the delay happened **before the container process started**.

The old production image was much larger and had several huge compressed layers. A clean GitHub runner reproduced similarly painful pull behavior.

The optimization work therefore focused on:

1. reducing total compressed image size;
2. balancing large Docker layers so Docker's concurrent layer downloads can help;
3. removing unnecessary runtime components;
4. shortening the in-container critical path after the image finally starts.

### Measured image progression

Registry-manifest measurements from the optimization workflow:

| Image | Registry layers | Compressed size | Largest compressed layer |
|---|---:|---:|---:|
| old production `sha-7200522bcf6e` | 40 | 8.7118 GB | 2.8077 GB |
| `fast-8237bab6e0f2` | 20 | 6.0938 GB | 3.8747 GB |
| `ultra-302d7ffe01ac` | 21 | 4.1416 GB | 0.6043 GB |

The final ultra benchmark is about **52.5% smaller** than the old production image and, more importantly, does not have a multi-gigabyte single-layer bottleneck.

The production `sha-d2103b98e602` image uses this optimized runtime architecture. Its exact compressed manifest total was not separately printed because the production build's final summary command had a quoting bug after a successful push, but the build itself succeeded and uses the same balanced-layer design.

### Useful benchmark evidence

Optimization benchmark workflow:

```text
Build ultra-startup image
run: 33183438127
candidate: ultra-302d7ffe01ac
digest: sha256:48c4b3579911543dd43f207042f5ae6eabd2ab43009ea0f81d032d3d98af2a11
compressed bytes: 4,141,599,088
registry layers: 21
```

Old baseline in that run:

```text
sha-7200522bcf6e
compressed bytes: 8,711,841,518
registry layers: 40
```

---

## 5. Final production Docker/runtime architecture

The optimization kept the known-good Comfy/PyTorch environment as the **builder source**, then moved the final runtime to plain Ubuntu instead of carrying a full CUDA base image.

Known-good builder lineage:

```text
hearmeman/comfyui-base:cu130-comfy0.32.0-torch2.11.0
```

Final runtime base:

```text
ubuntu:24.04
```

The runtime relies on:

- the host NVIDIA driver supplied by RunPod;
- PyTorch/cu130 userspace CUDA libraries already installed in the Python environment;
- only a small set of required Ubuntu runtime packages.

Core stack:

```text
Python 3.12.3
PyTorch 2.11.0+cu130
torch CUDA 13.0
Triton 3.6.0
SageAttention 2.2
ComfyUI 0.32.0
ONNX Runtime GPU 1.29.0
```

The production build smoke-tested:

- MMH3 runtime imports;
- PIL;
- PyTorch version/CUDA version;
- ONNX Runtime `CUDAExecutionProvider`;
- Triton import;
- SageAttention import;
- ComfyUI custom-node initialization via `--quick-test-for-ci`;
- phone server startup contract.

### Balanced runtime layers

The build uses `scripts/split_runtime_layers.py` to separate large Python/CUDA payloads into parallel-pull-friendly layers.

Measured uncompressed staging buckets in the production build included:

```text
remaining venv buckets: 686.2 MiB, 686.2 MiB, 686.2 MiB
NVIDIA buckets:         785.4 MiB, 779.5 MiB, 669.9 MiB
cudnn:                  470.9 MiB
onnxruntime:             279.9 MiB
torch:                  1080.5 MiB
triton:                  638.0 MiB
```

Do not casually collapse these back into one giant `COPY /opt/venv` layer. The first `fast` candidate showed why: it reduced total image size but created a ~3.87 GB compressed bottleneck layer.

---

## 6. Components intentionally removed from the runtime

### ComfyUI-Manager

ComfyUI-Manager was removed from the immutable production runtime.

Reason:

- it added about 4.3 seconds of pre-start time in CI;
- it attempted registry/network activity during Comfy startup;
- MMH3 already pins and bakes the required custom nodes;
- runtime mutation is contrary to the reproducible-image goal.

Do not re-add Manager without a specific reason.

### Git metadata

The production image strips Comfy/custom-node `.git` directories after recording exact commit IDs in lightweight `.mmh3_commit` marker files.

MMH3 validation reads those markers, with Git fallback for development environments.

This keeps reproducibility checks while removing unnecessary runtime weight.

---

## 7. Startup sequence inside the container

The optimized supervisor intentionally prioritizes the control plane and useful services.

Current order:

1. create persistent paths/config/state;
2. start **Phone UI (7860)**;
3. start **ComfyUI (8188)**;
4. give those services a short head start;
5. start background model/LoRA provisioning;
6. wait for Comfy to become responsive;
7. start **Jupyter (8888)**.

This is deliberate.

The phone UI should appear as early as practical and report provisioning state instead of making a fresh pod look dead while large model files download.

Startup timing is written to:

```text
/workspace/mmh3/state/startup.json
```

Use this file to distinguish:

- RunPod pre-container image-fetch time, which MMH3 cannot measure internally;
- container-start-to-service time, which MMH3 can measure.

---

## 8. Provisioning behavior

Fresh-volume model and LoRA provisioning is concurrent.

Default worker count:

```text
3
```

Relevant optional environment variables:

```text
MMH3_MODEL_DOWNLOAD_WORKERS
MMH3_LORA_DOWNLOAD_WORKERS
```

Both are clamped to a small safe range.

Do not increase parallelism aggressively without measuring host RAM, network behavior, and Hugging Face/CivitAI behavior.

### Warm-volume LoRA fast path

If a managed LoRA file already exists and previous persistent catalog metadata is available, startup reuses persistent metadata instead of re-querying CivitAI for every LoRA.

This avoids a pile of unnecessary metadata HTTP calls on every warm boot.

Editable YAML fields override cached remote metadata, including intentionally empty fields.

---

## 9. Phone UI v0.6 state model

PR #14 promoted the startup work together with Phone UI v0.6.

Important current features include:

- six independent persistent drafts:
  - T2V Auto / Custom
  - I2V Auto / Custom
  - R2V Auto / Custom
- asset library with persistent friendly nicknames;
- generated thumbnails for reusable image assets;
- explicit R2V P/V/A numbering and reorder controls;
- LoRA Manager separated from generation-side LoRA selection;
- compact sortable LoRA metadata management;
- explicit queue labels such as RUNNING and NEXT #N;
- persistent templates and generation metadata;
- browser-local draft mirror for immediate refresh recovery;
- persistent `/workspace` state as the authority across container replacement.

Key persistent files:

```text
/workspace/mmh3/config/loras.yaml
/workspace/mmh3/config/system_prompts.yaml
/workspace/mmh3/data/templates.json
/workspace/mmh3/data/ui_state.json
/workspace/mmh3/data/assets.json
/workspace/mmh3/data/input_thumbs/
```

See `docs/PERSISTENCE.md`.

---

## 10. Critical workflow contracts that must not regress

### Custom R2V

A real deployment exposed an invalid MiniMax ReferencePack enum.

Correct Custom R2V behavior is:

```text
prompt_provider = "none"
job_type = "standard"
system_prompt = ""
openrouter_api_key = ""
```

Custom R2V must pass the user's prompt through without invoking Auto/OpenRouter prompt generation.

Do not change `job_type` back to `custom`; that value is invalid for the node used here.

### Auto workflows

Auto modes use the appropriate OpenRouter prompt-generation path and runtime-injected secret. API keys must never be committed into workflow JSON.

### Canonical workflow count

There are exactly six canonical API workflow files under:

```text
workflows/api/
```

Tests enforce workflow contracts and scan for credentials.

---

## 11. Production image validation

PR #14 states that the optimized bare-Ubuntu/cu130 runtime was validated on a real RTX 5090 with:

- CUDA available;
- FP16 CUDA matmul;
- required H3 nodes;
- automatic provisioning;
- successful H3 generation.

The normal real-GPU compatibility check is:

```bash
mmh3 gpu-smoke
```

It verifies:

- `torch.cuda.is_available()`;
- GPU identity;
- a real FP16 CUDA matrix multiplication + synchronize;
- Triton import;
- SageAttention import;
- ONNX Runtime CUDA provider.

Use this whenever changing the base image, Python/PyTorch/CUDA stack, or moving to materially different GPU hardware.

---

## 12. CI evidence worth keeping

Current production merge:

```text
PR #14
Promote optimized runtime and phone UI v0.6
merge: d2103b98e60242125b825b60efc726b974847230
```

Main production workflows:

```text
Build MMH3 image
run: 33186023508
result: success

Test runtime
run: 33186023524
result: success
```

Final optimization benchmark before promotion:

```text
Build ultra-startup image
run: 33183438127
result: success

Test runtime
run: 33183443507
result: success
```

Runtime weight audit:

```text
Audit Python runtime weight
run: 33126939682
result: success
```

The audit is useful before attempting additional package removal. It showed that large remaining packages include Comfy/PyTorch/video/CUDA components that may be legitimate runtime dependencies. Do not delete large libraries merely because they are large.

---

## 13. Branch / PR history and what to use now

Important historical PRs:

- **#10** — post-deploy hardening and persistent LoRA seed.
- **#11** — startup critical-path trimming, `doctor`, `gpu-smoke`, warm LoRA fast path, service sequencing.
- **#12** — LoRA/asset UX and persistent drafts.
- **#14** — production promotion of optimized runtime + Phone UI v0.6.

The historical `startup-optimization` branch still exists and currently points to:

```text
302d7ffe01ac3e74dc7e6894180d8b12758a89ce
```

It contains useful experimental artifacts such as:

```text
Dockerfile.fast
Dockerfile.ultra
.github/workflows/build-fast-image.yml
.github/workflows/build-ultra-image.yml
.github/workflows/audit-runtime-weight.yml
scripts/audit_runtime_weight.py
```

However, **do not use `startup-optimization` as the base for new production work.**

It has diverged from `main` because its work was promoted through PR #14 and main then received production/documentation commits.

### Fresh development rule

For new work:

```bash
git checkout main
git pull
git checkout -b <new-topic-branch>
```

If experimental startup tooling from `startup-optimization` is needed, inspect or port the specific file/commit intentionally rather than treating that branch as newer than main.

---

## 14. RunPod template contract

Production template should use:

```text
Container image:
ghcr.io/crumb-bum-creep/mmh3-runpod:sha-d2103b98e602

Container disk:
50 GB

Persistent volume mount:
 /workspace

HTTP ports:
7860/http
8188/http
8888/http

Docker entrypoint:
blank

Docker start command:
blank
```

Runtime secrets/environment:

```text
OPENROUTER_API_KEY={{ RUNPOD_SECRET_OPENROUTER_API_KEY }}
HF_TOKEN={{ RUNPOD_SECRET_HF_TOKEN }}
CIVITAI_TOKEN={{ RUNPOD_SECRET_CIVITAI_TOKEN }}
JUPYTER_TOKEN={{ RUNPOD_SECRET_JUPYTER_PASSWORD }}

MMH3_AUTO_DOWNLOAD_MODELS=true
MMH3_AUTO_DOWNLOAD_LORAS=true
```

GHCR private-image auth requires a GitHub token with package-read access.

No actual secret values belong in Git.

See `docs/FIRST_RUNPOD_DEPLOY.md`.

---

## 15. Known-good operational commands

Prefer MMH3 helper commands over long ad-hoc process-management commands:

```bash
mmh3 status
mmh3 doctor
mmh3 gpu-smoke
mmh3 free
mmh3 interrupt
mmh3 restart-comfy
mmh3 restart-phone
mmh3 sync-models
mmh3 sync-loras
mmh3 provision-status
mmh3 logs-comfy
mmh3 logs-phone
mmh3 logs-memory
mmh3 logs-provisioning
mmh3 jupyter-url
```

First-boot full validator:

```bash
bash /opt/mmh3/scripts/first_boot_check.sh
```

If a fresh chat is helping with a live RunPod, use these first before inventing new kill/restart commands.

---

## 16. Development constraints / UX requirements

These are project requirements, not incidental preferences:

- 7860 is the primary user-facing interface.
- The UI must remain phone-friendly.
- RunPod web-terminal commands should be short, single-line, and paste-safe where practical.
- Avoid multi-line heredocs for normal operator instructions.
- Do not require additional exposed ports beyond 7860/8188/8888 unless the architecture is intentionally changed.
- Preserve the persistent `/workspace` contract.
- Do not make normal startup dependent on GitHub availability.
- Do not silently mutate pinned Comfy/custom-node versions at runtime.
- Do not commit secrets.
- Keep production image tags immutable.

---

## 17. Known-good pinned Comfy/custom-node baseline

ComfyUI pin:

```text
c2bcbecd82ec5ae66594340b395c24ef0217b238
```

Historical known-good custom-node pins used by this environment:

```text
ComfyUI-KJNodes
3f20054214fec9f9234fd3841ae6f1e4287948f6

ComfyUI-MiniMaxRefPack
7012734eabf6f98063d6eaf8ce1f9264ee803664

ComfyUI-OpenRouter-Simple
404b67229dd0f88373d35824ba624cb563e734b3

ComfyUI-Openrouter_node
45c67f94e335b978577773f05752e17ffe63a09e

ComfyUI-Spectrum-MiniMax-H3
ac247efcc2c9b6324fa106b3bd8e148a583db4a9

ComfyUI-VideoHelperSuite
4ee72c065db22c9d96c2427954dc69e7b908444b

rgthree-comfy
6b76ee6f2c5a007710b5a16f97c94330d6ecc871
```

The Dockerfile/config in current `main` is authoritative if these ever change.

---

## 18. Safe next optimization work

The large startup optimization is already in production. Any further work should be benchmark-driven.

Good next experiments:

1. Measure actual RunPod cold-start time across several fresh hosts/locations using the production image.
2. Record separately:
   - RunPod image-fetch/container-create duration;
   - MMH3 container-start-to-7860;
   - container-start-to-8188;
   - container-start-to-core-ready.
3. Compare warm-host/cached-image versus cold-host behavior.
4. Only if image transfer is still dominant, test compression/build-export changes on a separate branch.
5. Keep `sha-d2103b98e602` as the rollback baseline until a new candidate passes real GPU generation.

Potential experimental directions that were discussed but not made production requirements:

- higher gzip compression during build/export;
- zstd image compression, **only after confirming RunPod/container-runtime compatibility**;
- further package trimming based on measured imports/dependency tracing;
- additional layer rebalance if registry manifests show a new bottleneck.

### Do not do these casually

Do not strip CUDA/NVIDIA libraries such as NCCL/NVSHMEM merely to save bytes without real GPU validation. The old runtime-weight audit was intentionally used to avoid deleting dependencies based only on size.

Do not replace the current known-good environment with a freshly reconstructed minimal Python environment unless there is a strong measured payoff and comprehensive H3 inference validation.

---

## 19. Fresh-chat continuation checklist

A new development chat should do this in order:

1. Read this file.
2. Read current `README.md`, `docs/FIRST_RUNPOD_DEPLOY.md`, `docs/OPERATIONS.md`, and `docs/PERSISTENCE.md`.
3. Fetch current `main` head and recent CI status before changing code.
4. Treat `sha-d2103b98e602` as the current production image unless the repo now says otherwise.
5. Start new code from `main`, not the historical `startup-optimization` branch.
6. Preserve all six workflow contracts and the `/workspace` persistence model.
7. Run existing tests before opening/merging a PR.
8. For Docker/runtime changes, build and smoke-test the image.
9. For CUDA/base-image changes, run `mmh3 gpu-smoke` on a paid GPU.
10. Run at least one real H3 generation before promoting a new runtime image.

### Suggested first message in a fresh chat

```text
Continue development of crumb-bum-creep/MMH3_RunPod.
Read docs/DEVELOPMENT_HANDOFF.md on main first and treat the current repository as source of truth.
Then inspect current main/CI before making any changes.
```

---

## 20. Source-of-truth files

Use these instead of relying on old chat recollection:

```text
README.md
Dockerfile
config/hardware_profiles.yaml
config/models.yaml
config/custom_nodes.yaml
config/system_prompts.yaml
config/loras.yaml

runtime/mmh3/
services/phone-ui/
workflows/api/

docs/FIRST_RUNPOD_DEPLOY.md
docs/OPERATIONS.md
docs/PERSISTENCE.md
docs/KNOWN_GOOD_AND_MEMORY.md
docs/TEMPLATE_RUNTIME_FINDINGS.md

tests/
.github/workflows/
```

When chat history and these files disagree, inspect current `main` and follow the repository.
