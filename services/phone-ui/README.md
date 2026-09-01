# MMH3 Phone UI

The production standalone 7860 control plane is bundled here.

During the repository bootstrap phase the service directory is kept present so Docker image builds remain testable while the current phone UI is being folded into the unified MMH3 runtime.


## Sampler and output run metadata

The Generate tab exposes the running ComfyUI `KSamplerSelect.sampler_name` choices. Sampler selection is persisted independently for each of the six mode/prompt-mode drafts and is saved with generation metadata.

Completed-output summaries show the sampler and every applied LoRA with its model strength. This includes both user-selected Power LoRAs and the workflow's always-on MiniMax Turbo LoRA. Reusing a setup only restores user-selected LoRAs; it does not duplicate workflow-owned Turbo LoRAs.

## Drawable Comfy workflows

The six canonical files under `/opt/mmh3/workflows/api` remain the authoritative API execution graphs used by the phone UI.

API prompt JSON is not a drawable ComfyUI workflow format. MMH3 therefore no longer copies those files directly into the Comfy workflow browser. After ComfyUI becomes healthy, MMH3 reads `/object_info` and generates six LiteGraph workflow files under:

`/workspace/ComfyUI/user/default/workflows/MMH3/`

These generated workflows preserve the canonical node classes/connections while adding positions, widget values, and link metadata so they can be inspected and edited on the canvas, including the KJ model preview path.

### R2V persistent asset selector

R2V Auto and Custom include an MMH3-owned **Asset Reference Selector** before the existing MiniMax References Manager. It lists reusable files already present in `ComfyUI/input`:

- Picture 1–9
- Video 1–3, with soundtrack toggles
- Audio 1–3

The selector only builds the manager's existing `references_json`; media loading, H3 tagging, prompt writing, and conditioning remain owned by MiniMax References Manager / MiniMax H3 Reference to Video.

Phone-UI R2V generations continue to inject their own ordered reference JSON at queue time, so the same canonical R2V graph supports both interfaces.
