# MMH3 Development Handoff

> **Updated:** 2026-09-22  
> **Repository:** `crumb-bum-creep/MMH3_RunPod`  
> **vNext branch:** `next/vnext-runtime-cleanup`  
> **Historical production input:** `9d134f338f5374393a037f243551b1fe790517c6`  
> **Historical production image:** `ghcr.io/crumb-bum-creep/mmh3-runpod:sha-9d134f338f53`

## Purpose

MMH3 is a RunPod-first MiniMax H3 environment with:

- Phone UI on 7860;
- ComfyUI on 8188;
- JupyterLab on 8888;
- six canonical T2V / I2V / R2V Auto + Custom API workflows;
- persistent models, inputs, outputs and user state under `/workspace`;
- image-local application code under `/opt/mmh3` and `/ComfyUI`.

The vNext branch is a cleanup and reliability pass after repeated real RunPod migrations of the 9d image. Do not reintroduce the old live-patch/server-overlay architecture.

## Why vNext exists

The 9d image worked, but repeated migrations exposed several runtime defects:

1. Comfy could start before persistent model paths were fully reflected in its filename cache. Models existed on disk, but CLIP/Turbo selector lists were empty. Phone UI jobs then appeared to complete instantly without producing video. Manual bootstrap + Comfy restart repaired it.
2. Phone UI queue submission did not force validation of the actual `VHS_VideoCombine` branch, allowing a valid display-only branch to look like a successful job.
3. The output indexer could recursively scan a large persistent output library immediately after startup and compete with Comfy/provisioning for disk and CPU.
4. Persistent `runtime.yaml` was initialized once and then frozen, so new image defaults could silently fail to reach an existing volume.
5. Generation recipes were hardcoded in Python. Testing sampler/scheduler changes required live scripts against `/opt/mmh3`.
6. `server.py`, `server_v2.py`, runtime renaming and `server_legacy.py` created unnecessary patching risk.
7. Memory protection could not be disabled when the user intentionally wanted mixed-family queueing.
8. Comfy was always launched with `--disable-dynamic-vram`. Real RunPod sessions showed inconsistent host-RAM retention; vNext returns to normal dynamic-VRAM behavior by default and treats this as a testable runtime choice rather than a universal truth.
9. Output reuse did not provide a clean path from an Auto result to the exact generated prompt/configuration in Custom mode.
10. Output filenames used a fixed prefix with no user-configurable naming convention.

## vNext architecture

### Canonical Phone UI backend

The supervised service is now:

`services/phone-ui/server.py`

Reusable baseline implementation code lives in:

`services/phone-ui/server_core.py`

There is no `server_v2.py`, no `server_legacy.py`, and `runtime/entrypoint.sh` no longer rewrites Python server files at container startup.

Git commits/tags are the rollback mechanism.

### Migration-safe Comfy model visibility

Before every Comfy launch, the supervisor calls `configure_persistent_paths()`.

Queue submission also verifies the selected CLIP / UNET / VAE / Turbo filenames against Comfy `/object_info`. If the files should exist but Comfy's selector cache is stale, Phone UI:

- rejects the job instead of pretending it ran;
- writes `/workspace/mmh3/state/comfy_model_rescan.request`;
- tells the user an automatic refresh was requested.

The supervisor notices that request and, once the Comfy queue is idle, restarts only Comfy with persistent paths reasserted.

### Real video-output validation

Phone UI submits jobs with the workflow's `VHS_VideoCombine` node(s) as `partial_execution_targets`.

A queue response containing `node_errors` is treated as a failure and is not added to the generation record/plan store as a successful queued job.

This is the regression guard for the historical instant-completion/no-video failure.

### Output library startup behavior

`output_indexer.py` persists both the video index and watched directory mtimes.

Warm starts serve the existing index immediately and avoid an unconditional full recursive output scan. Older indexes without watched-directory state receive a startup grace before reconciliation. Failed background scans preserve the previous usable index rather than blanking Outputs.

Do not reintroduce request-time recursive scanning.

### Runtime config migration

Image defaults live in `config/runtime.yaml`; persistent user config remains under `/workspace/mmh3/config/runtime.yaml`.

Runtime config now has a schema version. Bootstrap deep-merges new defaults with persistent overrides and performs explicit migrations with a backup when a schema transition changes behavior.

v1 -> v2 intentionally changes:

`comfy.disable_dynamic_vram: true -> false`

A user who later explicitly sets it in a v2 config keeps that choice.

### Cold vs warm startup sequencing

Cold volume:

`Phone UI + Comfy -> short grace -> provision immediately while Comfy initializes`

Warm/migrated volume with local core already present:

`Phone UI + Comfy -> wait for Comfy health -> warm-start grace -> background provisioner`

Default warm grace is 8 seconds. This reduces startup contention without delaying first-time model downloads.

## Generation recipes

Generation recipes are defined in:

`config/generation_profiles.yaml`

Resolver/validation code lives in:

`runtime/mmh3/generation_profiles.py`

Phone UI can apply a named profile and optionally override supported tuning fields per generation. The fully resolved recipe is saved into output metadata as `generation_settings`.

### T2V / I2V

| ID | Purpose | Turbo | Strength | Schedule |
| --- | --- | --- | ---: | --- |
| `balanced8` | current 8-step profile | FL2V 8-step v1.0 | 1.0 | Euler + Simple, 8 steps, shift 6/3 |
| `fast4` | current fast profile | FL2V 4-step v1.2 | 1.0 | Euler + Simple, 4 steps, shift 6/3 |
| `legacy_exact` | historical pre-profile reproduction | FL2V LightX2V v0.1 | 0.5 | Euler + Beta 0.79/0.5 + Extend 3 |

The historical FL2V v0.1 LoRA is again a managed accelerator so a fresh deployment can actually reproduce legacy outputs.

### R2V

| ID | Purpose | Turbo | Strength | Schedule |
| --- | --- | --- | ---: | --- |
| `tuned` | vNext default under evaluation | Ref2V v0.1 | 0.85 | Euler + historical Beta/Extend chain |
| `legacy_exact` | exact historical baseline | Ref2V v0.1 | 0.85 | seeds_2 + Beta 0.6/0.6 + Extend 2 |
| `balanced8` | newer 8-step comparison | Ref2V 8-step v1.0 | 1.0 | Euler + Simple, 8 steps, shift 12/3 |

Important empirical findings from RunPod testing:

- `seeds_2` reproducibly reintroduced the historical left-shift/composition problem;
- Euler has been the best R2V sampler tested so far;
- `res_multistep` looked substantially worse and should not be promoted;
- with Euler, remaining composition drift appeared by the second preview/early denoising update, so scheduler/sigma-chain tuning is a higher-priority diagnostic than late decode behavior.

Do not rewrite `legacy_exact` to reflect newer preferences. It exists for reproduction. Continue tuning under `tuned`.

### Advanced tuning UI

The Generate tab exposes a collapsed Advanced section backed by actual recipe data:

- sampler;
- schedule type (Basic / Beta);
- steps;
- Turbo strength;
- Basic scheduler;
- video/audio sigma shifts;
- Beta alpha/beta;
- ExtendIntermediateSigmas enable/steps/start/end/spacing;
- R2V reference sizing `max` / `match`.

Sampler and BasicScheduler options are queried from the running Comfy instance when available.

## Reuse Exact

Outputs expose one reuse action: **Reuse Exact**.

It loads the output into the matching **Custom Prompt** mode and restores:

- `actual_prompt` for Auto-generated outputs;
- seed, with randomization disabled;
- starting/ending images;
- R2V references;
- user LoRAs and strengths;
- aspect ratio, megapixels and duration;
- checkpoint;
- generation profile;
- fully resolved `generation_settings` recipe;
- output naming metadata where applicable.

Historical outputs that predate resolved recipe metadata must map to historical profile defaults rather than silently using today's default.

## Output naming

Default behavior remains equivalent to production:

`MMH3/{mode}`

A persistent System setting allows a naming template using:

- `{mode}`
- `{prompt_mode}`
- `{profile}`
- `{checkpoint}`
- `{seed}`
- `{date}`
- `{time}`

Rendered path segments are sanitized before reaching `VHS_VideoCombine`.

## Memory protection

Persistent runtime controls live in:

`/workspace/mmh3/data/runtime_controls.json`

**Memory Protection** defaults ON.

ON:
- background cache-first memory guard operates normally;
- critically high memory can block new jobs;
- mixed FL2V / Ref2V queue families are blocked to preserve cleanup windows.

OFF:
- memory hold/automatic cleanup pressure logic is disabled;
- critical-memory admission blocking is bypassed;
- mixed-family queueing is allowed.

Manual cache/model release controls remain available either way.

Memory telemetry continues to distinguish raw cgroup usage from working set by subtracting reclaimable inactive file cache.

## Image/build housekeeping

The production image is defined by the root `Dockerfile`.

Superseded `Dockerfile.fast`, `Dockerfile.ultra`, their build workflows, and the obsolete startup-branch test workflow were removed. The optimized split-runtime-layer strategy remains in the production Dockerfile.

Primary workflows:

- `.github/workflows/test.yml` — runtime/unit/contracts;
- `.github/workflows/build-image.yml` — production image on `main`;
- `.github/workflows/build-production-candidate.yml` — explicit manual candidate build;
- `.github/workflows/audit-runtime-weight.yml` — image/runtime weight auditing.

## Non-negotiable contracts

- persistent data remains under `/workspace`;
- outputs are never deleted as part of migration/repair;
- exactly six canonical API workflow files remain installed;
- Custom R2V never invokes OpenRouter Auto prompt generation;
- no secrets in Git/workflow JSON;
- `MMH3Director` remains excluded from the normal output library;
- queue admission never performs synchronous model/cache cleanup;
- a failed video branch must never surface as a successful generation;
- migration recovery must not require ad-hoc scripts against `/opt`;
- live sampler experiments must go through recipe/tuning controls, not source-file surgery.

## Before promoting vNext

Do not merge/promote until all of the following are true:

1. runtime CI is green;
2. candidate image builds and passes image smoke tests;
3. warm migration boots without manual bootstrap/Comfy restart;
4. Phone UI remains responsive during startup with a large persistent output library;
5. Fast/Balanced/Legacy accelerator files are visible to Comfy;
6. T2V, I2V and R2V each produce a real video through Phone UI;
7. an intentionally invalid video branch is rejected instead of instant-completing;
8. Memory Protection OFF permits intentional mixed-family queueing;
9. Memory Protection ON retains safe blocking/cleanup behavior;
10. dynamic-VRAM-on candidate is exercised across a mixed sequence such as R2V -> R2V -> I2V -> T2V -> R2V while watching both working-set and raw host RAM;
11. Reuse Exact restores an Auto result into Custom mode with generated prompt, references, LoRAs, seed and recipe intact;
12. output naming default matches historical behavior and custom templates write where expected.

## Current development rule

The 9d image is the historical input baseline; vNext is the active continuation.

Do not copy live duct-tape scripts into production architecture. Convert the behavior they proved into tested runtime features, then delete the need for the script.
