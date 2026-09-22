# MMH3 Phone UI

The standalone 7860 control plane lives here.

## vNext layout

- `server.py` — canonical vNext service entrypoint and MMH3-specific generation/runtime features.
- `server_core.py` — reusable base handlers and workflow plumbing used by the entrypoint.
- `output_indexer.py` — persistent-output library indexer.
- `static/` — mobile/desktop UI assets.

The old `server_v2.py -> server.py -> server_legacy.py` runtime-copy arrangement was removed. Container startup no longer rewrites backend source files.

vNext uses explicit handler composition between `server.py` and `server_core.py`, generation recipes from `config/generation_profiles.yaml`, and persistent controls under `/workspace/mmh3`.
