# MMH3 RunPod First Deployment

This is the first-deployment procedure for the MMH3 image.

> **Image status.** `sha-9d134f338f53` is the historical deployed baseline feeding the current vNext cleanup. While `next/vnext-runtime-cleanup` is still under candidate validation, keep existing production templates pinned to 9d. After vNext promotion, update the template to the new immutable `sha-...` tag produced by the main image build.

## 1. RunPod Secrets

Create these under **RunPod → Settings → Secrets**.

Recommended RunPod secret names:

| RunPod secret | Value |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `HF_TOKEN` | Hugging Face access token |
| `CIVITAI_TOKEN` | CivitAI API token |
| `JUPYTER_PASSWORD` | A long random token/password you choose for Jupyter |

The Pod template maps them to these container variables:

```text
OPENROUTER_API_KEY={{ RUNPOD_SECRET_OPENROUTER_API_KEY }}
HF_TOKEN={{ RUNPOD_SECRET_HF_TOKEN }}
CIVITAI_TOKEN={{ RUNPOD_SECRET_CIVITAI_TOKEN }}
JUPYTER_TOKEN={{ RUNPOD_SECRET_JUPYTER_PASSWORD }}
```

RunPod supports secret references in template environment variables with
`{{ RUNPOD_SECRET_secret_name }}`.

Do **not** store the actual values in this Git repository or inside workflow JSON.

## 2. GitHub Container Registry authentication

The MMH3 image is currently intended to remain private in GHCR.

Create a GitHub Personal Access Token that can **read packages** for this account/repository.

Then in RunPod create **Container Registry Credentials** for GHCR:

- Registry/image host: `ghcr.io`
- Username: your GitHub username
- Password/token: the GitHub token with package-read access

When creating the RunPod template, select those registry credentials for the private image.

If the GHCR package is intentionally made public later, registry credentials are no longer required.

## 3. Template image

The production image will be:

```text
ghcr.io/crumb-bum-creep/mmh3-runpod:sha-9d134f338f53
```

Prefer the pinned `sha-...` tag for normal deployments after validation. Do not rely on
`latest` for a long-lived production template.

## 4. Template configuration

Create a new **private NVIDIA Pod template**.

Recommended initial settings:

| Setting | Value |
|---|---|
| Name | `MMH3 RunPod` |
| Container image | pinned GHCR image above |
| Container registry auth | GHCR credentials created above |
| Container disk | **50 GB** |
| Volume mount path | `/workspace` |
| HTTP ports | `7860/http,8188/http,8888/http` |
| Docker entrypoint | leave blank |
| Docker start command | leave blank |

Leaving Docker entrypoint/start command blank is intentional: the image owns startup through
`/opt/mmh3/runtime/entrypoint.sh`.

### Environment variables

Add:

```text
OPENROUTER_API_KEY={{ RUNPOD_SECRET_OPENROUTER_API_KEY }}
HF_TOKEN={{ RUNPOD_SECRET_HF_TOKEN }}
CIVITAI_TOKEN={{ RUNPOD_SECRET_CIVITAI_TOKEN }}
JUPYTER_TOKEN={{ RUNPOD_SECRET_JUPYTER_PASSWORD }}

MMH3_AUTO_DOWNLOAD_MODELS=true
MMH3_AUTO_DOWNLOAD_LORAS=true
```

Optional convenience list:

```text
MMH3_LORA_VERSION_IDS=3236596,ANOTHER_CIVITAI_VERSION_ID,...
```

Those are **CivitAI model-version IDs**, not model-page IDs.

You do not have to maintain this environment-variable list permanently. In the 7860 **LoRAs** tab you can:

- paste a CivitAI model-version ID or a CivitAI URL containing `modelVersionId`
- download/sync it using the RunPod CivitAI secret
- give it a friendly nickname
- record your preferred/recommended strength
- override/add trigger words
- add tags and personal usage notes
- select the LoRA by friendly metadata while Comfy still receives the real filename

The LoRA list itself is not sensitive and does not need to be a RunPod Secret. Long-term LoRA metadata lives persistently at:

```text
/workspace/mmh3/config/loras.yaml
```

## 5. Persistent storage

MMH3 keeps reproducible application code inside the container image and persistent/user state under
`/workspace`.

Important persistent paths:

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

### Storage size

For the full H3 INT8 T2V/I2V/R2V model set plus custom LoRAs and video output, start with approximately
**250 GB or more** of persistent storage.

A larger volume is appropriate if generated videos will remain on the Pod for long periods.

### Pod volume vs network volume

Either is supported.

A **network volume** is especially useful if you regularly destroy and replace Pods because the H3 models,
custom LoRAs, inputs, and output can survive replacement without redownloading.

Tradeoff: RunPod network-volume availability is tied to compatible locations/data centers, so it can reduce
GPU-location flexibility. If maximum spot/host availability is more important, a fresh Pod volume plus MMH3
automatic provisioning remains supported.

## 6. Hardware selection

Target hardware:

- NVIDIA RTX 5090
- NVIDIA RTX PRO 6000 Blackwell / equivalent 96 GB model

Minimum system/container RAM target:

- **100 GB+**

The exact system RAM does not need to be fixed. MMH3 detects the container's actual cgroup memory limit at
runtime and derives its memory safety behavior from that limit.

Do not use host `free -h` as the authoritative Pod limit; RunPod hosts can expose far more physical RAM than
the container is allowed to consume.

### CUDA selection

When RunPod offers compatibility filters, choose hosts compatible with **CUDA 13.0 or newer**.

The known-good captured stack uses:

- container CUDA toolkit: 13.0
- PyTorch: 2.11.0+cu130
- known-good captured driver: 595.91.07
- captured driver CUDA compatibility: 13.2

The host driver can advertise a newer CUDA compatibility level than the CUDA toolkit/PyTorch packaged in the
container. Those values do not need to be numerically identical.

## 7. First boot behavior

On an empty persistent volume, startup is intentionally staged:

1. MMH3 detects GPU, VRAM, and the cgroup RAM limit.
2. Persistent paths are created.
3. Canonical workflows are installed.
4. ComfyUI, the phone UI, and Jupyter start.
5. Core H3 models and managed accelerators/LoRAs provision in the background.
6. Port 7860 reports provisioning state.
7. Generation remains locked until the required core model set reports ready.
8. On warm/migrated volumes, MMH3 verifies that Comfy can see persistent model filenames and repairs a stale Comfy model index automatically.
9. Once the selected profile/checkpoint is ready, jobs can be queued normally.

This prevents a fresh Pod from appearing dead during very large model downloads.

## 8. Interfaces

- **7860** — MMH3 mobile/control UI
- **8188** — ComfyUI
- **8888** — JupyterLab

Normal day-to-day use should happen primarily through 7860.

Jupyter remains token-protected. Because RunPod's unauthenticated HTTP-service health probe can receive a 403 from Jupyter even while Jupyter itself is healthy, the console may transiently show the 8888 service as not ready. Use:

```bash
mmh3 jupyter-url
```

to print the correctly URL-encoded RunPod Jupyter URL. Do not share that URL because it contains the Jupyter token.

## 9. First health checks

In the web terminal, run the complete first-boot validator:

```bash
bash /opt/mmh3/scripts/first_boot_check.sh
```

Then use the compact status command whenever needed:

```bash
mmh3 status
```

Logs:

```bash
mmh3 logs-comfy
mmh3 logs-phone
mmh3 logs-memory
```

Persistent boot/provisioning state:

```text
/workspace/mmh3/data/boot_report.json
/workspace/mmh3/data/provisioning_report.json
/workspace/mmh3/state/hardware.json
/workspace/mmh3/state/memory.json
/workspace/mmh3/state/provisioning.json
```

## 10. First paid GPU smoke test

Do not begin with a large production generation.

Recommended sequence:

1. Confirm 7860 shows the expected GPU/RAM and all three credentials as configured.
2. Confirm provisioning is complete.
3. Open 8188 and verify required H3 node classes loaded.
4. Run a short low-MP T2V Custom generation.
5. Run a short I2V Custom generation.
6. Run a short R2V Custom generation.
7. Run one Auto generation to validate OpenRouter injection.
8. Verify LoRA selection and metadata.
9. Verify queue progress, output playback, reuse, and memory status.
10. Only then run a normal 15-second 0.7 MP workload.

## References

RunPod documentation:

- https://docs.runpod.io/pods/templates/manage-templates
- https://docs.runpod.io/pods/templates/environment-variables
- https://docs.runpod.io/pods/templates/secrets
- https://docs.runpod.io/pods/templates/create-custom-template
