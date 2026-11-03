"""Re-embed Studio's system prompts (config/system_prompts.yaml) into scripts/prompt_lab.py.

    python scripts/prompt_lab_sync.py

Run after editing the default system prompts; tests/test_units.py fails while they differ.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / "scripts" / "prompt_lab.py"


def main() -> None:
    prompts = (yaml.safe_load((ROOT / "config" / "system_prompts.yaml").read_text(encoding="utf-8")) or {})["prompts"]
    blob = base64.b64encode(json.dumps(prompts, ensure_ascii=False).encode("utf-8")).decode("ascii")
    src = LAB.read_text(encoding="utf-8")
    src, n = re.subn(r'PROMPTS = json\.loads\(base64\.b64decode\("[^"]*"\)', f'PROMPTS = json.loads(base64.b64decode("{blob}")', src)
    assert n == 1, "PROMPTS line not found"
    LAB.write_text(src, encoding="utf-8")
    print(f"embedded {len(prompts)} prompts into {LAB.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
