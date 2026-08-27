#!/usr/bin/env bash
set -Eeuo pipefail

export PATH="/opt/venv/bin:$PATH"
export PYTHONPATH="/opt/mmh3/runtime${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p /workspace/mmh3/{config,data,state,logs} /workspace/ComfyUI/{models,user,input,output}

TCMALLOC="$(ldconfig -p | grep -Po 'libtcmalloc.so.\d' | head -n1 || true)"
if [[ -n "$TCMALLOC" ]]; then export LD_PRELOAD="$TCMALLOC"; fi

python -m mmh3.bootstrap || {
  echo "[mmh3] bootstrap reported an error; keeping pod alive in degraded mode" >&2
}

exec python -m mmh3.supervisor
