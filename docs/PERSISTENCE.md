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
- `/workspace/mmh3/data/*.json` — catalogs and generation metadata

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

## Templates and reference assets

Templates and uploaded assets already persist on the RunPod volume. They are intentionally not baked into the image because they change frequently and may become large.

A future Git-backed profile sync can version small portable configuration separately from large model/media files without changing these persistent paths.
