# Changelog

## 2.0.0: MMH3 Studio (rewrite)

A clean rewrite on the `studio` branch. `main` is untouched, so the old image stays available.

**Why the rewrite.** The previous app had grown in layers. `server_v2.py` was copied over
`server.py` at container start. Overlay scripts were injected into the HTML with string replace.
Generation profiles added and removed graph nodes at queue time. About 50 tests asserted source
text, so any restructuring "failed". Real fixes lived on branches that were never merged.
Reverting any one change meant untangling all of it.

**Fixed**
- **RAM creep and OOM.** Root cause: ComfyUI 0.32 sized its cache against host RAM, not the
  container limit (fixed upstream in 0.34). The image moves to the v9 base (ComfyUI 3dd559d,
  0.36 line) and adds family-switch unloads, an explicit cache headroom, and dynamic VRAM kept off.
- **R2V camera drift.** The 4-step v0.1 Ref2V LoRA with `seeds_2` is gone; R2V uses the released
  8-step v1.0 768p LoRA.
- **OpenRouter key exposure.** The key no longer goes into ComfyUI workflows, where it was visible
  in `/history` to anyone with the pod URL.
- **Multi-file LoRA versions.** An R2V file shipped as a secondary file, or a motion helper, was
  never downloaded before. Now every file is downloaded and used per mode.
- **Jobs that "completed" with no video** are now reported as failures with ComfyUI's actual error.

**New**
- A queue owned by Studio: mixed families, pause, reorder, per-job prompt editing, run again, and
  recovery if Studio restarts mid-render.
- Auto prompts drafted in Studio: preview and edit them, and they are written in the background
  for queued jobs.
- Recipes as data, with a per-job tuning drawer, and Compare with synced side-by-side playback.
- Live sampler preview, step and ETA, and a tally in the top bar.
- Reference kits, phone uploads, and automatic soundtrack detection so `<Audio N>` tags stay
  correct.
- Continue from last frame, and any output as an R2V reference.
- CivitAI browser (search, bookmarks, collections), including checkpoints into the base-model
  picker.
- Optional Studio password (`MMH3_PASSWORD`).
- Font bundled; the UI has no outside dependencies.

**Kept, same on-disk formats**: models layout, outputs, `output_library.json` (favorites and
groups), `output_meta.json` and `.h3.json` records, `assets.json` nicknames, `loras.yaml`,
`system_prompts.yaml`.

**Removed**: rgthree Power Lora Loader (plain `LoraLoaderModelOnly` chain), the OpenRouter nodes
(prompting happens in Studio), the staged/cache-first memory guard, and hardware profiles.
