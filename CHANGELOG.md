# Changelog

## 2.1.0

**Prompts, rewritten from MiniMax's official guides** (`VIDEO_PROMPT_WRITING_GUIDE_base_en.md` and
`_ref_en.md` on huggingface.co/MiniMaxAI/MiniMax-H3)
- Text, Image and Reference system prompts follow the guides: `<d>[English] …</d>` dialogue with
  stable `(S1)` speaker IDs, voiceover / `<scenetrans>` / `<cutoff>`, the camera vocabulary with
  amplitude and speed, MM:SS.mmm cuts, and the six-section full-reference format for R2V.
  Your house style (smartphone look by default, ARRI/Panavision for film scenes, lived-in locations,
  micro-expressions, explicit language) is kept as a section the user's idea overrides.
- Image mode picks the guide's task from the frames you give it: start only (I2VA), start + end
  (FL2VA), or **end only (L2VA, new)**. Each has its own prompt. Studio writes the first-line
  alignment instruction itself, so its shot number and seconds always match the final prompt and
  the clip's real length, including after you change the length or edit the prompt.
- Prompt edits you saved for the old defaults no longer override the new ones. They're kept, and
  More → Auto prompt instructions offers "Load my old version".

**New**
- **Tweak**: under any prompt (typed, or an Auto draft) and on a queued job, ask for a change in
  plain words ("make it night", "she whispers instead") or tap a quick one (More detail, Tighter,
  Add dialogue, …). The prompting model edits only what you asked for and keeps the format, tags
  and timing intact. Undo restores the previous version.
- **Library**: a NEW pill and amber ring on clips you haven't opened, a count on the Library tab,
  a New filter and Mark all watched. Thumbnails show the file name (`T2V_00042`). The player has
  previous/next buttons, swipe on the video and ← → keys, and favourite/delete update in place.
  Copy prompt button.
- **LoRAs**: the seed catalog no longer downloads everything on boot. Five general LoRAs still do;
  the rest are under **Quick install** (one tap to Get, live progress) in the LoRA screen and in
  Create's LoRA picker. Per-LoRA "Download on startup" switch, and Uninstall (deletes the files,
  keeps it under Quick install). An existing volume picks up the split once.

**Fixed**
- Typing in the LoRA search no longer re-renders the screen and drops the keyboard.
- The Library refreshes on its own when a clip finishes.
- Prompt-writer replies get a larger token budget, so long R2V prompts and reasoning don't truncate.

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
