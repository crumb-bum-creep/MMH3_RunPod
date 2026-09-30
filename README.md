# MMH3 Studio

MiniMax H3 video (picture and soundtrack in one pass) on a RunPod GPU, driven from your phone.

Studio is a ComfyUI runtime plus a phone-first app on port 7860 that queues jobs, drafts prompts,
manages references and LoRAs, and keeps a library of everything you make. ComfyUI's own editor
stays available on 8188.

## Deploy on RunPod

1. **Image.** `ghcr.io/crumb-bum-creep/mmh3-runpod:studio`. CI builds it from this branch after
   the tests pass. `:studio-<sha>` pins an exact build.
2. **Volume.** Attach your network volume at `/workspace`. An existing MMH3 volume works as-is:
   models, outputs, favorites/groups, LoRA catalog, nicknames and system prompts all carry over,
   and nothing is re-downloaded. A new volume needs about 100 GB.
3. **Ports (HTTP).** `7860` Studio, `8188` ComfyUI, `8888` JupyterLab.
4. **Environment variables.** Use RunPod secrets for the keys.

   | Variable | Purpose |
   |---|---|
   | `OPENROUTER_API_KEY` | Auto prompts. You can also paste a key in System instead. |
   | `CIVITAI_TOKEN` | LoRA and checkpoint downloads, CivitAI bookmarks and collections |
   | `HF_TOKEN` | Optional. Faster, rate-limit-free model downloads |
   | `MMH3_PASSWORD` | Optional. Puts a login on Studio. Recommended, since pod URLs are public. |
   | `JUPYTER_TOKEN` | Optional. Puts a login on JupyterLab. |
   | `MMH3_DOWNLOAD_EROS=true` | Optional. Downloads the Eros checkpoint at boot (also available as a button). |
   | `COMFY_EXTRA_ARGS` | Optional. Extra ComfyUI flags, appended last. |

5. **First boot.** Open port 7860. Studio is up within seconds and shows download progress. You
   can queue jobs right away; they start once their models are on disk.

## Using it

- **Create.** Pick Text, Image or Reference, and either write the full prompt or switch to
  **Auto** and write an idea. *Preview the prompt* drafts it so you can read and edit it before
  queueing. You can also skip the preview: Auto jobs get their prompt written in the background
  while earlier jobs render.
- **Speed.** Picks a recipe (below). **Compare** queues the same prompt and seed once per recipe;
  Library → Comparisons plays them side by side, in sync.
- **Queue.** Shows the live sampler preview, the current step and a real ETA. You can pause,
  reorder, cancel, edit a waiting job's prompt, or run a finished job again. The red tally in the
  top bar follows the render from any screen.
- **Library.** A contact sheet of every output, including those from the old build. Open a clip
  to favorite it, group it, **Reuse** its exact settings, **Continue** (its last frame becomes the
  next I2V start), use it **as a reference**, download it or delete it.
- **References.** Upload images, clips and audio from your phone and save named **kits** of
  references to reuse in R2V. Tags like `<Picture 1>` are shown on each tile, and a clip's
  soundtrack can be toggled.
- **More → LoRAs and models.** Your catalog, plus **Browse CivitAI** (search, bookmarks and your
  collections), with LoRAs and base checkpoints filtered to MiniMax H3.

## Recipes (`config/recipes.yaml`)

A recipe is only values: turbo LoRA, strength, steps, sampler, scheduler, and optionally sigma
shift and extra low-noise passes. The graph never gets rewired.

| Family | Recipe | What it is |
|---|---|---|
| T2V / I2V | **Balanced** (default) | FL2V turbo 8-step v1.0 768p, strength 1.0, shift 6/3. This is your previous known-good setting, and LightX2V's trained shift for 768p. |
| | Fast | FL2V turbo 4-step v1.2 768p, shift 6/3. About half the sampling time. |
| | Upstream v9 | Hearmeman v9: strength 0.8, ComfyUI's default shift 12/3, plus extra passes (slower). |
| R2V | **Balanced** (default) | Ref2V turbo 8-step v1.0 768p, strength 1.0, shift 12/3. Your previous known-good setting. |
| | Balanced · shift 6 | Same at 6/3, like every other 768p turbo LoRA. Unpublished for Ref2V, so run a Compare. |
| | Upstream v9 | Strength 0.85, default shift, extra passes. |

There is intentionally no 4-step R2V recipe. The only 4-step Ref2V LoRA is the v0.1 preview,
which paired with `seeds_2` was what drifted the camera off-subject. Upstream dropped it too.
Every setting can be tuned per job under *Tune this recipe*, and every output records the
resolved recipe.

## Memory: why RAM stays flat now

The previous image ran ComfyUI 0.32, which sized its RAM cache and pinned memory against the
*host's* RAM rather than the container's limit, so it kept caching until the pod was OOM-killed.
ComfyUI fixed this in 0.34 (Comfy-Org/ComfyUI#15927), and this image is on the 0.36 line. On top
of that fix, Studio:

- **Unloads models when the family changes.** It feeds ComfyUI one job at a time; when the next
  job needs a different model family (T2V/I2V ↔ R2V) or base checkpoint, it fully unloads first.
  You can queue any mix without babysitting.
- **Sets the cache headroom explicitly.** ComfyUI starts with `--cache-ram` at 12% of the
  container limit (12–32 GB).
- **Keeps dynamic VRAM off** (`--disable-dynamic-vram`), which MiniMax INT8 needs
  (Comfy-Org/ComfyUI#15271).
- **Drops cached outputs after a job** when RAM passes 70%, and has an idle safety net at 85%.

System shows the container-limit numbers, which are the ones the OOM killer uses.

## LoRAs with several files

A CivitAI version can ship several files, for example `X.safetensors` for T2V/I2V plus
`X-ref2va.safetensors` for R2V, or a visual LoRA with a motion helper. The catalog
(`/workspace/mmh3/config/loras.yaml`, same format as before plus a `files:` list) records each
file's mode family (`fl2v`, `ref2v`, `any`) and role (`main`, `helper`). At generation time
Studio loads the right file or files for the mode. Families and roles are guessed from file
names and editable in the LoRA sheet. Existing single-file entries have their extra files
discovered on the first sync.

**Collections.** CivitAI's public API only lists your *bookmarks*. Your named collections come
through the site's own API, which accepts your API key but is undocumented, so treat that tab as
best-effort. When it fails it says why.

## Where things live

```
/workspace/ComfyUI/models        models (unchanged layout)
/workspace/ComfyUI/input/mmh3    uploads, kit files, last frames
/workspace/ComfyUI/output/MMH3   videos (<MODE>_#####-audio.mp4 + .h3.json record + .png first frame)
/workspace/mmh3/config           settings.yaml (your overrides), loras.yaml, system_prompts.yaml, secrets.json
/workspace/mmh3/data             library, kits, jobs, indexes
/workspace/mmh3/logs             comfyui.log, studio.log, provision.log, supervisor.log (also viewable in More → Logs)
```

## Development

```
python scripts/dev_workspace.py /tmp/ws                     # a fake volume that looks like yours
python tests/fake_comfy.py --port 8189 --output /tmp/ws/ComfyUI/output &
MMH3_WORKSPACE=/tmp/ws python -m studio.server              # http://localhost:7860
python -m pytest tests                                      # unit + end-to-end against the fake ComfyUI
python scripts/validate_graphs.py --setup-dummies <ComfyUI dir>   # real ComfyUI prompt validation
```

The code layout is one module per job: `graph.py` builds the ComfyUI prompt, `recipes.py`,
`jobs.py` (the queue), `prompting.py`, `library.py`, `loras.py`, `civitai.py`, `provision.py`,
`supervisor.py`, `server.py`, and `web/` holds the phone app (no build step).
