# MMH3 Persistence

MMH3 separates immutable application code from persistent user state.

## Persistent paths

Everything below survives image/container replacement when the same RunPod volume is mounted at `/workspace`:

- `/workspace/ComfyUI/models` — core models and LoRA weights
- `/workspace/ComfyUI/input` — uploaded/reusable reference assets
- `/workspace/ComfyUI/output` — generated video output
- `/workspace/ComfyUI/user` — Comfy user data
- `/workspace/mmh3/config/loras.yaml` — managed LoRA IDs and user metadata
- `/workspace/mmh3/config/system_prompts.yaml` — edited Auto prompt contracts
- `/workspace/mmh3/data/templates.json` — phone-UI generation templates
- `/workspace/mmh3/data/ui_state.json` — six independent autosaved T2V/I2V/R2V × Auto/Custom drafts
- `/workspace/mmh3/data/assets.json` — reusable asset nicknames/metadata
- `/workspace/mmh3/data/input_thumbs/` — generated image thumbnails for the asset picker
- `/workspace/mmh3/data/*.json` — catalogs and generation metadata
- `/workspace/mmh3/data/runtime_controls.json` — live Phone UI controls such as Memory Protection and output naming
- `/workspace/mmh3/config/runtime.yaml` — versioned launch/runtime configuration, including Comfy dynamic-VRAM behavior

The application source itself remains image-local under `/opt/mmh3` and `/ComfyUI`.

## LoRA seed behavior

`/opt/mmh3/config/loras.yaml` is a **first-volume seed**, not a continuously authoritative file.

At bootstrap it is copied to:

```text
/workspace/mmh3/config/loras.yaml
```

only if the persistent file does not already exist.

That means:

1. A brand-new empty volume receives the repository's seed LoRA catalog.
2. The background provisioner downloads any missing managed LoRA weights.
3. Nicknames, strengths, trigger words, tags, filenames, and notes edited in the 7860 UI are written to the persistent file.
4. Later image upgrades do **not** overwrite those edits.

The current seed was imported from the user's 2026-08-27 LoRA backup and includes the managed version IDs and resolved filenames needed to recreate that catalog.

## Templates, drafts, and reference assets

Templates and uploaded assets persist on the RunPod volume. Asset nicknames are stored separately from the media in `assets.json`, so the original Comfy filename remains stable while the phone UI can display a friendly name.

The Generate page autosaves a separate draft for each of:

- T2V Auto / T2V Custom
- I2V Auto / I2V Custom
- R2V Auto / R2V Custom

Those drafts include prompt text, generation parameters, starting image, ordered R2V references, and selected LoRAs/strengths. The browser also keeps a local mirror so a page refresh can recover immediately; the persistent `/workspace` copy is authoritative across container replacements.

Large media files stay on the persistent volume rather than in Git. vNext also stores the fully resolved generation recipe with completed-output metadata so Reuse Exact can restore the sampler/scheduler/Turbo configuration that produced a video. A future Git-backed profile sync can version small portable configuration separately without changing these paths.
