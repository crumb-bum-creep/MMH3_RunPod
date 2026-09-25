# MMH3 Operations

MMH3 is designed so normal maintenance does not require remembering long ComfyUI launch commands.

## Primary services

| Service | Port | Supervisor behavior |
|---|---:|---|
| MMH3 Phone UI | 7860 | Restarted if it exits |
| ComfyUI | 8188 | Restarted with backoff if it exits |
| JupyterLab | 8888 | Restarted if it exits |
| Memory guard | internal | Runs inside MMH3 supervisor |
| Provisioner | one-shot background task | Downloads/checks core models and managed LoRAs |

## Persistent logs

```text
/workspace/mmh3/logs/comfyui.log
/workspace/mmh3/logs/phone-ui.log
/workspace/mmh3/logs/jupyter.log
/workspace/mmh3/logs/memory-guard.log
/workspace/mmh3/logs/provisioning.log
```

## Current helper commands

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
```

### `mmh3 status`

Shows:

- detected GPU
- VRAM size
- cgroup RAM usage / limit
- Comfy health
- paths to memory/hardware state

### `mmh3 doctor`

Checks the local 7860/8188/8888 services, MMH3 processes, GPU, cgroup RAM, startup timing, and provisioning state. If the local services are healthy while RunPod's exposed links are unavailable, the command explicitly calls that out as a likely RunPod proxy/host issue.

### `mmh3 gpu-smoke`

Runs a small real CUDA matmul and verifies Triton, SageAttention, and ONNX Runtime's CUDA provider. Use it after moving the template to a new image/runtime architecture.

### `mmh3 free`

Requests ComfyUI to unload models and release cache through its API.

Use this instead of killing Comfy when the goal is simply to reclaim memory.

### `mmh3 interrupt`

Interrupts the current Comfy execution.

### `mmh3 restart-comfy`

Stops the supervised ComfyUI child process. The MMH3 supervisor starts a fresh Comfy process automatically,
using the hardware/profile-aware launch configuration.

This is the replacement for the long Pod-specific restart commands used during development.

### `mmh3 restart-phone`

Restarts only the 7860 control plane without touching ComfyUI or Jupyter.

### `mmh3 sync-models`

Checks the MMH3 core model manifest and downloads missing/invalid files.

### `mmh3 sync-loras`

Reads:

```text
/workspace/mmh3/config/loras.yaml
```

plus optional `MMH3_LORA_VERSION_IDS`, then synchronizes CivitAI LoRAs and refreshes the UI catalog.

## Memory policy

The memory guard reads the **container cgroup limit**, not host physical RAM.

This distinction matters on RunPod because a host can expose far more RAM through `free -h` than the
container is allowed to consume.

The guard tracks:

- current cgroup RAM
- free cgroup headroom
- current Comfy queue activity
- cleanup threshold
- critical threshold
- hardware profile

State is written to:

```text
/workspace/mmh3/state/memory.json
```

### Why MMH3 does not unload after every job

Telemetry from the known-good 96 GB RTX PRO 6000 pod showed:

- cold R2V leaves a large but reusable resident model baseline
- repeated jobs in the same family add relatively little
- changing model families is what causes the large persistent RAM jumps

Therefore MMH3 preserves warm state when there is enough cgroup headroom and cleans only when measured
pressure requires it.

## If ComfyUI crashes

The MMH3 supervisor should restart ComfyUI automatically.

Watch:

```bash
mmh3 logs-comfy
```

The phone UI and Jupyter should remain alive while Comfy restarts.

If a crash loop occurs, the supervisor applies restart backoff instead of rapidly respawning Comfy.

## Cold-volume provisioning

Check:

```bash
cat /workspace/mmh3/state/provisioning.json
```

and:

```bash
tail -100 /workspace/mmh3/logs/provisioning.log
```

The 7860 UI also reports provisioning state.

Generation remains disabled until the core H3 model set is ready. On warm/migrated volumes MMH3 verifies that Comfy can actually see the ready model filenames and automatically restarts only Comfy if its selector cache is stale.

The System tab also exposes the Comfy dynamic-VRAM launch setting. vNext defaults to normal dynamic-VRAM behavior (the "Disable dynamic VRAM" toggle is OFF). Changing that setting writes the persistent runtime configuration and schedules a Comfy-only restart once the queue is idle.

## Files you should consider irreplaceable

Persistent/user-owned:

```text
/workspace/ComfyUI/models/loras/
/workspace/ComfyUI/input/
/workspace/ComfyUI/output/
/workspace/mmh3/config/
/workspace/mmh3/data/
```

Everything in the image under `/opt/mmh3` and application code under `/ComfyUI` should be reproducible.

## Emergency terminal inspection

GPU:

```bash
nvidia-smi
```

True container RAM:

```bash
cat /sys/fs/cgroup/memory.current 2>/dev/null; cat /sys/fs/cgroup/memory.max 2>/dev/null
```

Ports:

```bash
ss -ltnp 2>/dev/null | grep -E ':7860|:8188|:8888'
```

Relevant processes:

```bash
ps auxww | grep -E '[m]mh3|[m]ain.py|[j]upyter|[s]erver.py'
```
