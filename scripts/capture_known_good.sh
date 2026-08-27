#!/usr/bin/env bash
set -Eeuo pipefail

OUT="${1:-/workspace/mmh3_capture_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$OUT"

echo "Capturing MMH3 environment into $OUT"

{
  echo "=== DATE ==="
  date -Is
  echo
  echo "=== OS ==="
  cat /etc/os-release 2>/dev/null || true
  echo
  echo "=== KERNEL ==="
  uname -a
  echo
  echo "=== GPU ==="
  nvidia-smi 2>/dev/null || true
  echo
  echo "=== PYTHON ==="
  command -v python3 || true
  python3 --version 2>&1 || true
  echo
  echo "=== PYTORCH/CUDA ==="
  python3 - <<'PY'
try:
    import torch
    print("torch", torch.__version__)
    print("cuda", torch.version.cuda)
    print("cuda_available", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("gpu", torch.cuda.get_device_name(0))
except Exception as e:
    print("torch_probe_error", repr(e))
PY
  echo
  echo "=== MEMORY ==="
  free -h || true
  echo
  echo "=== FILESYSTEM ==="
  df -h / /workspace 2>/dev/null || true
} > "$OUT/system.txt" 2>&1

python3 -m pip freeze > "$OUT/pip-freeze.txt" 2>&1 || true

COMFY="${MMH3_COMFY_DIR:-/workspace/ComfyUI}"
if [ -d "$COMFY" ]; then
  {
    echo "COMFY_DIR=$COMFY"
    git -C "$COMFY" rev-parse HEAD 2>/dev/null || true
    git -C "$COMFY" remote -v 2>/dev/null || true
    git -C "$COMFY" status --short 2>/dev/null || true
  } > "$OUT/comfy-git.txt"

  find "$COMFY/custom_nodes" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort > "$OUT/custom-node-dirs.txt"

  : > "$OUT/custom-node-git.txt"
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    echo "=== $d ===" >> "$OUT/custom-node-git.txt"
    git -C "$d" rev-parse HEAD >> "$OUT/custom-node-git.txt" 2>/dev/null || echo "NO_GIT" >> "$OUT/custom-node-git.txt"
    git -C "$d" remote -v >> "$OUT/custom-node-git.txt" 2>/dev/null || true
    echo >> "$OUT/custom-node-git.txt"
  done < "$OUT/custom-node-dirs.txt"

  find "$COMFY/models" -type f \( -name '*.safetensors' -o -name '*.ckpt' -o -name '*.pt' -o -name '*.pth' \) -printf '%P\t%s\n' 2>/dev/null | sort > "$OUT/models.tsv"

  find "$COMFY/user/default/workflows" -type f -name '*.json' -print 2>/dev/null | sort > "$OUT/workflow-paths.txt"
fi

{
  echo "=== RUNNING RELEVANT PROCESSES ==="
  ps auxww | grep -E '[m]ain.py|[j]upyter|[s]erver.py.*7860|[u]vicorn|[g]unicorn' || true
  echo
  echo "=== LISTENING PORTS ==="
  ss -ltnp 2>/dev/null | grep -E ':7860|:8188|:8888' || true
} > "$OUT/runtime.txt" 2>&1

tar -C "$(dirname "$OUT")" -czf "$OUT.tar.gz" "$(basename "$OUT")"
echo
echo "=== CAPTURE COMPLETE ==="
echo "$OUT.tar.gz"
