#!/usr/bin/env bash
set -Eeuo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
OUT="${1:-/workspace/mmh3_template_capture_${TS}}"
mkdir -p "$OUT"

redact() {
  python3 -c 'import re,sys
s=sys.stdin.read()
s=re.sub(r"(?i)(sk-[A-Za-z0-9_-]{12,})","<REDACTED_KEY>",s)
s=re.sub(r"(?i)((?:token|password|api[_-]?key|secret)\s*[=:]\s*)[^\s\"'\'';]+",r"\1<REDACTED>",s)
s=re.sub(r"(?i)([?&](?:token|key|api_key|secret)=)[^&\s]+",r"\1<REDACTED>",s)
sys.stdout.write(s)'
}

echo "Capturing template/runtime layout to $OUT"

{
  echo "=== /ComfyUI/extra_model_paths.yaml ==="
  cat /ComfyUI/extra_model_paths.yaml 2>/dev/null || true
  echo
  echo "=== KEY DIRECTORIES / SYMLINKS ==="
  for p in /ComfyUI /ComfyUI/models /workspace /workspace/ComfyUI /workspace/models /comfyui-minimax /comfyui-runtime; do
    echo "--- $p"; ls -lad "$p" 2>/dev/null || true; readlink -f "$p" 2>/dev/null || true
  done
  echo
  echo "=== /ComfyUI/models CONTENTS ==="
  find -L /ComfyUI/models -maxdepth 3 -type f \( -name '*.safetensors' -o -name '*.ckpt' -o -name '*.pt' -o -name '*.pth' \) -printf '%p\t%s\n' 2>/dev/null | sort || true
  echo
  echo "=== WORKSPACE TOP-LEVEL ==="
  find /workspace -maxdepth 2 -mindepth 1 -printf '%y\t%p\t%l\n' 2>/dev/null | sort | head -4000 || true
  echo
  echo "=== MOUNTS ==="
  mount 2>/dev/null || true
} > "$OUT/paths-and-mounts.txt" 2>&1

for src in /start_script.sh /comfyui-runtime/src/start.sh /ComfyUI/extra_model_paths.yaml; do
  if [ -f "$src" ]; then
    name="$(echo "$src" | sed 's#^/##; s#/#__#g')"
    cat "$src" | redact > "$OUT/${name}.txt"
  fi
done

{
  echo "=== /comfyui-runtime FILE TREE ==="
  find /comfyui-runtime -maxdepth 4 -type f -printf '%p\t%s\n' 2>/dev/null | sort | head -5000 || true
  echo
  echo "=== /comfyui-minimax FILE TREE ==="
  find /comfyui-minimax -maxdepth 4 -type f -printf '%p\t%s\n' 2>/dev/null | sort | head -5000 || true
} > "$OUT/template-file-tree.txt" 2>&1

{ echo "=== ENVIRONMENT VARIABLE NAMES ONLY ==="; env | sed 's/=.*//' | sort; } > "$OUT/env-names.txt"

{
  echo "=== MODEL LOADER OPTIONS FROM COMFY ==="
  python3 - <<'PY'
import json, urllib.request
try:
    d=json.load(urllib.request.urlopen("http://127.0.0.1:8188/object_info",timeout=10))
except Exception as e:
    print("object_info error:",e); raise SystemExit
for cls in ("UNETLoader","CLIPLoader","VAELoader","LoraLoaderModelOnly"):
    print("\n##",cls)
    x=d.get(cls,{})
    req=((x.get("input") or {}).get("required") or {})
    for k,v in req.items():
        if isinstance(v,list) and v and isinstance(v[0],list):
            print(k)
            for item in v[0]: print(" ",item)
PY
} > "$OUT/model-options.txt" 2>&1

ARCHIVE="${OUT}.tar.gz"
tar -C "$(dirname "$OUT")" -czf "$ARCHIVE" "$(basename "$OUT")"
echo
echo "=== SUPPLEMENTAL CAPTURE COMPLETE ==="
echo "$ARCHIVE"
echo "Upload that .tar.gz to ChatGPT."
