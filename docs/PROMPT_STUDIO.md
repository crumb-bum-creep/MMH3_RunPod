# MMH3 Prompt Studio

Prompt Studio is a separate prompt-development workflow inside the 7860 phone UI. It does not queue a ComfyUI job until the compiled prompt is explicitly transferred into a Custom generation workflow.

## Purpose

Existing Auto prompting remains available for fast one-shot generation. Prompt Studio is for scenes that need more control, iteration, reusable subjects, or reference-aware planning.

The Studio flow is:

```text
idea + optional references
        ↓
structured scene planner
        ↓
editable persistent scene spec
        ↓
scoped AI edits + locked fields
        ↓
H3 compiler
        ↓
deterministic validator / repair pass
        ↓
Use in T2V/I2V/R2V Custom
```

## OpenRouter model

The default Studio model is:

```text
google/gemini-3-flash-preview
```

The model is stored per project and can be changed in the Studio UI without changing the six canonical Comfy workflows.

**Test model** makes a tiny text-only request through the selected project model. It verifies the configured OpenRouter key, model slug, and provider path without loading Comfy, sending reference images, or starting a video generation.

OpenRouter calls happen in the MMH3 backend. The browser never receives the OpenRouter API key.

## Reference modes

Each Studio subject can use one of three reference modes.

### Text only

No H3 picture reference is assigned. The subject is defined from the written description.

### Actual asset

A persistent image from `/workspace/ComfyUI/input` is assigned to a specific H3 `<Picture N>` slot.

If **Let Gemini analyze this reference** is enabled, Prompt Studio sends a bounded private image copy to OpenRouter during planning/editing so the planner can describe visible appearance.

The original asset remains on the RunPod volume and is not duplicated into Prompt Studio state.

### Opaque Picture slot

The subject is assigned to a specific `<Picture N>` without sending an image to OpenRouter.

This is intentional for portable prompts where the final H3 reference may be chosen later. The planner/compiler is instructed not to invent unseen identity, appearance, wardrobe, hair, body, or other visual traits.

## Subjects

Project subjects have:

- project-local ID and H3 subject order
- label
- semantic type
- written description
- performance/behavior notes
- reference binding
- lock state

A subject can be saved to the persistent reusable subject library and added to other projects. When reused, it receives a new project-local ID while keeping its reusable description/reference defaults.

Persistent file:

```text
/workspace/mmh3/data/prompt_subjects.json
```

## Shots

Each shot stores:

- start time
- framing
- MiniMax camera-motion vocabulary or custom camera detail
- optional amplitude/speed modifier
- participating subjects
- action/performance
- dialogue
- shot-specific sound
- lock state

Shot 1 is always normalized to start at 0. Later shot times are normalized to remain inside project duration.

## Director controls

The shot editor includes director-oriented controls beyond plain text fields:

- exact shot-count control (1–12)
- even timing distribution
- visual timeline with per-cut range controls
- visual framing preset cards
- semantic shot blocking

### Semantic blocking

Each shot can open a frame whose shape follows the project's selected aspect ratio.

Subjects can be added to the frame as labeled semantic blocks. A block stores:

- the project subject ID
- normalized frame position
- normalized size
- facing direction
- an optional blocking note

Blocks can be dragged directly on the frame and resized from the editor controls.

This board is **not** an H3 reference image and does not consume a `<Picture N>` slot. During compilation MMH3 translates the semantic placement into natural composition language such as left/right/center, foreground/midground/background, relative scale, and facing.

AI scene edits may read blocking as context but cannot silently erase or replace user blocking data.


### Freehand subject-tagged sketching

A semantic block can also be selected as the active drawing target.

While drawing:

1. every stroke is tagged to the selected semantic block/subject
2. stroke coordinates are stored only as persistent Studio UI metadata
3. MMH3 derives a normalized semantic bounding region from the strokes
4. the compiler uses that compact semantic region rather than the raw drawing

Raw sketch strokes are removed from planner/editor/compiler OpenRouter payloads. They therefore do not consume model context, do not become an H3 `<Picture N>`, and cannot accidentally be interpreted as a video reference.

Dragging a semantic block also translates its tagged strokes so the saved sketch and semantic placement remain aligned.

## Scoped AI edits

Prompt Studio can edit:

- the whole scene
- scene-level fields such as environment/style/sound/music
- one subject
- one shot

Structured output is used for planner/editor responses. Broad edits restore locked fields after the model response, so locks are enforced by MMH3 rather than relying only on prompt wording.

Subject reference bindings are always preserved across AI edits.

## Local project preflight

Prompt Studio can validate the editable project structure before making an OpenRouter request.

The **Check project** control verifies locally:

- referenced persistent assets still exist
- actual Picture slots have selected assets
- one Picture number is not accidentally assigned to two different concrete images
- subject and shot IDs remain unique
- shot timing is structurally valid
- shot subject references point to defined subjects
- semantic blocking points to defined subjects
- freehand sketch strokes still point to existing blocking items

Incomplete-but-useful states are warnings rather than hard failures. For example, an I2V prompt project may be drafted without choosing its final starting image yet.

Build/Rebuild Plan and Compile + Validate run this preflight automatically. Structural errors stop before any OpenRouter call, while warnings remain visible and allow the operation to continue.

## Compilation and validation

Compilation is a separate model call from planning.

The compiler receives:

- project mode
- duration/aspect ratio
- the explicit subject/reference contract
- the structured scene plan

It returns the final MiniMax prompt format for the selected mode.

MMH3 then validates deterministic contracts such as:

- required output sections
- section order
- Shot 1 timestamp rule
- increasing shot timestamps
- timestamps inside duration
- undefined R2V `<Picture N>` references
- undefined R2V `<Subject N>` references

If validation fails, Prompt Studio performs one constrained repair pass and validates again.

## Use in Custom

**Use in Custom Workflow** loads the compiled prompt into the matching Custom generation draft.

For I2V, a configured starting asset is transferred automatically.

For R2V, actual picture assets are transferred only when the complete used Picture sequence can be resolved without renumbering. If the project contains sparse or opaque Picture slots, Prompt Studio deliberately does not auto-fill references because doing so would silently change `<Picture N>` numbering.

## Persistence and revisions

Projects are stored at:

```text
/workspace/mmh3/data/prompt_projects.json
```

Each project includes revision checkpoints. AI planning, AI edits, and compilation create checkpoints automatically; manual checkpoints can also be created in the UI.

Revision history is capped to a bounded number of recent checkpoints per project.

## First live-pod validation

Before promoting a new image containing Prompt Studio:

1. Open Studio on 7860 and create a project.
2. Confirm the project survives a phone-UI restart.
3. Test one text-only T2V project.
4. Test I2V with an analyzed starting image.
5. Test R2V with an actual image reference.
6. Test R2V with an opaque Picture slot and verify no image is required for planning.
7. Lock a shot field, perform a whole-scene AI edit, and verify the locked value does not change.
8. Compile and confirm validator status.
9. Use the compiled prompt in the matching Custom workflow.
10. Run at least one real H3 generation from the transferred prompt before treating the image as production.
