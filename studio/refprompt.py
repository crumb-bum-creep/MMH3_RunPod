"""Subprocess worker: write an R2V prompt with MiniMaxRefPack's prompt writer.

Reads one JSON request on stdin, prints {"prompt": ...} or {"error": ...}.
Kept out of the server process because RefPack's media loaders import torch.
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    req = json.loads(sys.stdin.read() or "{}")
    try:
        from minimax_refpack import prompt as rp
        from minimax_refpack import refs as rr

        references = rr.ReferenceSet.from_json(req["references_json"])
        system_prompt = str(req.get("system_prompt") or "")
        text = rp.write_prompt(
            references=references,
            input_dir=req["input_dir"],
            direction=req.get("direction", ""),
            api_key="",  # resolved by RefPack from OPENROUTER_API_KEY in our env
            model=req.get("model") or rp.DEFAULT_MODEL,
            system_prompt=system_prompt,
            width=int(req.get("width", 0)),
            height=int(req.get("height", 0)),
            length_seconds=float(req.get("length_seconds", 0)),
            reasoning_effort=req.get("reasoning_effort") or rp.DEFAULT_REASONING_EFFORT,
            # A custom system prompt already fixes the register; skip the classifier call.
            job_type="standard" if system_prompt.strip() else "auto",
        )
        print(json.dumps({"prompt": text}))
    except Exception as exc:  # reported back to the UI verbatim
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"[:600]}))


if __name__ == "__main__":
    main()
