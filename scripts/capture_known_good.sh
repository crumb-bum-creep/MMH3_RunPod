#!/usr/bin/env bash
set -Eeuo pipefail

STAMP="$(date +%Y%m%d_%H%M%S)"
BASE="${1:-/workspace/mmh3_capture_${STAMP}}"
COMFY="${MMH3_COMFY_DIR:-/workspace/ComfyUI}"
mkdir -p "$BASE"

say(){ printf '\n=== %s ===\n' "$1"; }
redact(){ sed -E \
  -e 's/(sk-or-v1-[A-Za-z0-9_-]+)/<REDACTED_OPENROUTER>/g' \
  -e 's/(hf_[A-Za-z0-9]+)/<REDACTED_HF>/g' \
  -e 's/([?&](token|key|api_key|auth|password)=)[^&[:space:]]+/\1<REDACTED>/Ig' \
  -e 's/(--(token|password|api-key|api_key))[=[:space:]]+[^[:space:]]+/\1 <REDACTED>/Ig'; }

{
  say DATE; date -Is
  say HOST; hostname 2>/dev/null || true
  say OS; cat /etc/os-release 2>/dev/null || true
  say KERNEL; uname -a
  say GPU; nvidia-smi 2>/dev/null || true
  say NVIDIA_QUERY; nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,pstate,temperature.gpu --format=csv,noheader 2>/dev/null || true
  say CUDA_COMPILER; command -v nvcc 2>/dev/null || true; nvcc --version 2>/dev/null || true
  say PYTHON; command -v python3 || true; python3 --version 2>&1 || true
  say PYTORCH_CUDA
  python3 - <<'PY'
mods=['torch','torchvision','torchaudio','xformers','sageattention','triton']
for name in mods:
    try:
        m=__import__(name)
        print(name, getattr(m,'__version__','<no __version__>'))
    except Exception as e:
        print(name, 'NOT_IMPORTABLE', repr(e))
try:
    import torch
    print('torch.version.cuda', torch.version.cuda)
    print('cuda_available', torch.cuda.is_available())
    if torch.cuda.is_available():
        print('device_count', torch.cuda.device_count())
        for i in range(torch.cuda.device_count()):
            p=torch.cuda.get_device_properties(i)
            print('device', i, p.name, 'vram_bytes', p.total_memory)
except Exception as e:
    print('torch_probe_error', repr(e))
PY
  say MEMORY; free -h || true
  say CGROUP
  cat /proc/1/cgroup 2>/dev/null || true
  for f in /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.events /sys/fs/cgroup/memory.limit_in_bytes /sys/fs/cgroup/memory.usage_in_bytes /sys/fs/cgroup/memory.failcnt; do
    [ -f "$f" ] && { echo "$f"; cat "$f"; }
  done
  say FILESYSTEM; df -h / /workspace 2>/dev/null || df -h || true
  say MOUNTS; mount | grep -E '(/workspace|overlay|nfs|runpod|fuse)' || true
  say WORKSPACE_TOP; ls -lah /workspace 2>/dev/null | head -100 || true
} > "$BASE/system.txt" 2>&1

python3 -m pip freeze > "$BASE/pip-freeze.txt" 2>&1 || true
python3 -m pip list --format=columns > "$BASE/pip-list.txt" 2>&1 || true

{
  say PYTHON_EXECUTABLES
  command -v python || true
  command -v python3 || true
  command -v pip || true
  command -v pip3 || true
  say CONDA
  command -v conda || true
  conda info --envs 2>/dev/null || true
  say VIRTUAL_ENVS
  find /workspace -maxdepth 3 -type f -path '*/bin/python' -print 2>/dev/null | head -50
} > "$BASE/python-env.txt" 2>&1

if [ -d "$COMFY" ]; then
  {
    echo "COMFY_DIR=$COMFY"
    echo -n 'COMFY_HEAD='; git -C "$COMFY" rev-parse HEAD 2>/dev/null || true
    echo -n 'COMFY_BRANCH='; git -C "$COMFY" branch --show-current 2>/dev/null || true
    echo '--- REMOTES ---'; git -C "$COMFY" remote -v 2>/dev/null || true
    echo '--- STATUS ---'; git -C "$COMFY" status --short 2>/dev/null || true
    echo '--- RECENT COMMITS ---'; git -C "$COMFY" log -5 --oneline 2>/dev/null || true
  } > "$BASE/comfy-git.txt" 2>&1

  find "$COMFY/custom_nodes" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort > "$BASE/custom-node-dirs.txt"
  : > "$BASE/custom-node-git.txt"
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    {
      echo "=== $d ==="
      echo -n 'HEAD='; git -C "$d" rev-parse HEAD 2>/dev/null || echo NO_GIT
      echo -n 'BRANCH='; git -C "$d" branch --show-current 2>/dev/null || true
      git -C "$d" remote -v 2>/dev/null || true
      echo
    } >> "$BASE/custom-node-git.txt"
  done < "$BASE/custom-node-dirs.txt"

  find "$COMFY/models" -type f \( -iname '*.safetensors' -o -iname '*.ckpt' -o -iname '*.pt' -o -iname '*.pth' -o -iname '*.bin' \) -printf '%P\t%s\n' 2>/dev/null | sort > "$BASE/models.tsv"
  find "$COMFY/user/default/workflows" -type f -name '*.json' -printf '%P\n' 2>/dev/null | sort > "$BASE/workflow-paths.txt"
  du -sh "$COMFY/models"/* 2>/dev/null | sort -h > "$BASE/model-dir-sizes.txt" || true

  if [ -f "$COMFY/extra_model_paths.yaml" ]; then
    redact < "$COMFY/extra_model_paths.yaml" > "$BASE/extra_model_paths.yaml"
  fi
fi

{
  say RELEVANT_PROCESSES
  ps auxww | grep -E '[m]ain.py|[j]upyter|[s]erver.py.*7860|[u]vicorn|[g]unicorn' | redact || true
  say LISTENING_PORTS
  ss -ltnp 2>/dev/null | grep -E ':7860|:8188|:8888' || true
  say COMFY_CMDLINE
  for pid in $(pgrep -f '[p]ython.*main.py' 2>/dev/null || true); do
    printf 'PID=%s CWD=' "$pid"; readlink -f "/proc/$pid/cwd" 2>/dev/null || true
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | redact || true
    echo
  done
  say PHONE_UI_CMDLINE
  for pid in $(pgrep -f '[p]ython.*server.py.*7860' 2>/dev/null || true); do
    printf 'PID=%s CWD=' "$pid"; readlink -f "/proc/$pid/cwd" 2>/dev/null || true
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | redact || true
    echo
  done
} > "$BASE/runtime.txt" 2>&1

{
  say COMFY_TREE
  [ -d "$COMFY" ] && find "$COMFY" -maxdepth 2 -mindepth 1 -type d -printf '%P\n' 2>/dev/null | sort | head -500 || true
  say WORKSPACE_LARGE_DIRS
  du -x -h --max-depth=2 /workspace 2>/dev/null | sort -h | tail -100 || true
} > "$BASE/layout.txt" 2>&1

mkdir -p "$BASE/config-files"
for f in \
  "$COMFY/requirements.txt" \
  "$COMFY/pyproject.toml" \
  "$COMFY/comfyui_version.py"; do
  [ -f "$f" ] && cp "$f" "$BASE/config-files/" || true
done

{
  echo 'MMH3 KNOWN-GOOD CAPTURE'
  echo "Created: $(date -Is)"
  echo "Comfy: $COMFY"
  echo
  echo 'GPU:'
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null || true
  echo
  echo 'RAM:'
  free -h | sed -n '1,2p' || true
  echo
  echo 'Comfy commit:'
  git -C "$COMFY" rev-parse HEAD 2>/dev/null || true
  echo
  echo 'Custom nodes:'
  wc -l < "$BASE/custom-node-dirs.txt" 2>/dev/null || echo 0
  echo
  echo 'Models:'
  wc -l < "$BASE/models.tsv" 2>/dev/null || echo 0
} > "$BASE/SUMMARY.txt"

ARCHIVE="$BASE.tar.gz"
tar -C "$(dirname "$BASE")" -czf "$ARCHIVE" "$(basename "$BASE")"

echo
echo '=== CAPTURE COMPLETE ==='
echo "$ARCHIVE"
ls -lh "$ARCHIVE"
echo
echo 'Upload that .tar.gz back into this ChatGPT conversation.'
