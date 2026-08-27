# Community Template Runtime Findings

Captured 2026-08-27 from the functioning MiniMax H3 RunPod template.

## Storage layout

The pod uses `/workspace` as persistent storage. In the captured pod it was mounted via a RunPod MooseFS/FUSE network volume.

The community template deliberately keeps the ComfyUI source tree on the image-local filesystem at:

```text
/ComfyUI
```

Persistent state lives under:

```text
/workspace/ComfyUI
├── models/
├── custom_nodes/
├── user/
├── input/
└── output/
```

At startup, the runtime symlinks at least `user`, `input`, and `output` into `/ComfyUI`. Models are made visible through `/ComfyUI/extra_model_paths.yaml` rather than requiring the entire `/ComfyUI/models` tree to be moved onto the network volume.

This is a good architectural pattern for MMH3: keep application code ephemeral and reproducible, keep expensive/personal state persistent.

## extra_model_paths.yaml

The captured mapping uses:

```yaml
network_volume:
    base_path: /workspace/ComfyUI
    checkpoints: "models/checkpoints"
    configs: "models/configs"
    loras: "models/loras"
    vae: "models/vae"
    text_encoders: "models/text_encoders\nmodels/clip"
    diffusion_models: "models/unet\nmodels/diffusion_models"
    clip_vision: "models/clip_vision"
    style_models: "models/style_models"
    embeddings: "models/embeddings"
    diffusers: "models/diffusers"
    vae_approx: "models/vae_approx"
    controlnet: "models/controlnet\nmodels/t2i_adapter"
    gligen: "models/gligen"
    upscale_models: "models/upscale_models"
    latent_upscale_models: "models/latent_upscale_models"
    hypernetworks: "models/hypernetworks"
    photomaker: "models/photomaker"
    classifiers: "models/classifiers"
    model_patches: "models/model_patches"
    audio_encoders: "models/audio_encoders"
    background_removal: "models/background_removal"
    frame_interpolation: "models/frame_interpolation"
    geometry_estimation: "models/geometry_estimation"
    optical_flow: "models/optical_flow"
    detection: "models/detection"
    custom_nodes: custom_nodes
```

## Startup chain

The image entrypoint is `/start_script.sh`.

Its job is roughly:

1. Sync the MiniMax template repo into `/comfyui-minimax`.
2. Read a pinned shared-runtime ref from `/comfyui-minimax/pins.json`.
3. Sync `/comfyui-runtime` to that pinned revision.
4. Execute:
   ```bash
   bash /comfyui-runtime/src/start.sh /comfyui-minimax
   ```

If GitHub is temporarily unreachable, it retries and can fall back to the already baked/on-disk checkout rather than killing the pod.

## Shared runtime features worth preserving

The captured shared runtime includes reusable components for:

- boot/deployment reports
- model download management
- model-path generation
- model validation
- custom-node provisioning
- persistent-volume sync
- SageAttention probing
- CivitAI environment handling
- CUDA-specific PyTorch constraint files
- Jupyter startup/auth policy

The MMH3 project does not need to copy this runtime wholesale. We should preserve the useful behaviors while simplifying around our much narrower H3-only scope.

## Memory-related startup detail

The community runtime explicitly preloads tcmalloc:

```bash
TCMALLOC="$(ldconfig -p | grep -Po "libtcmalloc.so.\d" | head -n 1)"
export LD_PRELOAD="${TCMALLOC}"
```

This may affect allocator behavior and should be included in MMH3 reproducibility tests rather than silently omitted.

## RunPod-provided environment signals

Useful environment variable names present on the captured pod include:

- `RUNPOD_GPU_NAME`
- `RUNPOD_GPU_COUNT`
- `RUNPOD_MEM_GB`
- `RUNPOD_CPU_COUNT`
- `RUNPOD_DC_ID`
- `RUNPOD_POD_ID`
- `CUDA_VERSION`

Hardware policy should still use direct runtime/cgroup probes as the source of truth, with RunPod variables serving as supplemental metadata.

## Model visibility

The Comfy object-info API confirmed the expected MiniMax model families:

- `minimax_h3_fl2va_pruned_int8_convrot.safetensors`
- `minimax_h3_ref2va_pruned_int8_convrot.safetensors`
- `qwen3vl_32b_minimax_h3_int8_convrot.safetensors`
- MiniMax video/audio VAEs
- FL2V and Ref2V Turbo LoRAs

It also confirmed that LoRA discovery through Comfy's model registry is a better source for the UI than assuming every file is directly under `/ComfyUI/models`.

## MMH3 design decision

Adopt the same high-level separation:

- immutable/reproducible application image
- persistent `/workspace` state
- explicit model paths
- startup validation
- pinned source revisions
- degraded-but-alive behavior for nonfatal bootstrap failures

But simplify the deployment chain so MMH3 has one repository and one narrow H3 runtime rather than a general template + shared runtime + per-model template hierarchy.
