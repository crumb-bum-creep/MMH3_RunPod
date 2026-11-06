"""Subjects (experimental): characters, settings and objects you define once and reuse in R2V.

A subject is a name, a kind (character / setting / object), a description, and
reference files, each with an optional role ("face", "outfit", "voice"). A
subject can have variants ("Bob · tuxedo", "Bob · no voice", "simple Bob"):
a variant adds to or replaces the description, keeps or drops the base
references, adds its own, and can mute the subject's voice.

In Create (Reference mode) you pick subjects into numbered slots. Slot N is
`<Subject N>` in the prompt. Studio:
  - builds the reference list from the slots (then your loose references),
    so the <Picture N> / <Video N> / <Audio N> tags follow;
  - writes each slot's `subject_definitions` line itself (`definition_lines`),
    and for Auto asks the prompt writer to copy those lines and to refer to the
    subjects only by label elsewhere (`direction_block`), so appearance lives in
    one place;
  - swaps a subject in an existing prompt without rewriting it (`rewrite`):
    the slot's definition lines are regenerated, every reference tag that moved
    is renumbered, and words of the old description still found in the prompt
    are reported so you can tidy them (a Tweak with a ready-made request).

Stored in /workspace/mmh3/data/subjects.json. Nothing here changes how plain
references, kits or prompts work when no subject is used.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Any

from . import graph, library, paths, util

FILE = paths.DATA / "subjects.json"
KINDS = ("character", "setting", "object")
_lock = threading.Lock()
_TOKEN = re.compile(r"<(Picture|Video|Audio) (\d+)>")
SECTIONS = ("subject_definitions", "summary", "retention_analysis", "detailed_description",
            "overall_soundscape", "non_diegetic_music", "integrated_multimodal_description")
_HEADER = re.compile(r"^\s*(" + "|".join(SECTIONS) + r")\s*:\s*(.*)$", re.I)
_STOP = set("""a an the and or but with without of in on at to from by for as is are was were be been his her
their its this that these those very wearing wears has have had into onto over under about who which
while when where light dark small large long short""".split())


class SubjectError(ValueError):
    pass


# --------------------------------------------------------------------------- storage

def items() -> list[dict[str, Any]]:
    return list((util.read_json(FILE, {}) or {}).get("items") or [])


def get(subject_id: str) -> dict[str, Any] | None:
    return next((s for s in items() if s.get("id") == subject_id), None)


def _clean_refs(refs: Any) -> list[dict[str, Any]]:
    out = []
    for r in refs or []:
        if not isinstance(r, dict) or r.get("kind") not in ("image", "video", "audio") or not r.get("file"):
            continue
        library.safe_rel(paths.INPUT, r["file"])
        item: dict[str, Any] = {"kind": r["kind"], "file": r["file"], "role": str(r.get("role") or "").strip()[:40]}
        if r["kind"] == "video":
            item["use_soundtrack"] = r.get("use_soundtrack", True) is not False
        out.append(item)
    return out


def _clean_variant(v: dict[str, Any]) -> dict[str, Any]:
    name = str(v.get("name") or "").strip()[:40]
    if not name:
        raise SubjectError("give the variant a name")
    return {"id": str(v.get("id") or uuid.uuid4().hex[:8]), "name": name,
            "description": str(v.get("description") or "").strip()[:1500],
            "description_mode": "replace" if v.get("description_mode") == "replace" else "append",
            "inherit_refs": v.get("inherit_refs", True) is not False,
            "refs": _clean_refs(v.get("refs")), "mute": bool(v.get("mute"))}


def save(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()[:60]
    if not name:
        raise SubjectError("give the subject a name")
    kind = data.get("kind") if data.get("kind") in KINDS else "character"
    subject = {"id": str(data.get("id") or uuid.uuid4().hex[:10]), "name": name, "kind": kind,
               "description": str(data.get("description") or "").strip()[:2000],
               "refs": _clean_refs(data.get("refs")),
               "variants": [_clean_variant(v) for v in data.get("variants") or [] if isinstance(v, dict)]}
    names = [v["name"].lower() for v in subject["variants"]]
    if len(set(names)) != len(names):
        raise SubjectError("two variants have the same name")
    with _lock:
        all_items = items()
        now = time.time()
        for i, s in enumerate(all_items):
            if s.get("id") == subject["id"]:
                subject["created"] = s.get("created", now)
                subject["updated"] = now
                all_items[i] = subject
                break
        else:
            subject["created"] = subject["updated"] = now
            all_items.insert(0, subject)
        util.write_json(FILE, {"items": all_items})
    return subject


def delete(subject_id: str) -> None:
    with _lock:
        util.write_json(FILE, {"items": [s for s in items() if s.get("id") != subject_id]})


# --------------------------------------------------------------------------- resolving slots

def resolve(subject_id: str, variant_id: str | None = None, library_items: list[dict[str, Any]] | None = None
            ) -> dict[str, Any]:
    """A subject (or one of its variants) as Create uses it: label, kind, description, references."""
    s = next((x for x in (library_items if library_items is not None else items()) if x.get("id") == subject_id), None)
    if s is None:
        raise SubjectError("that subject no longer exists")
    v = next((x for x in s.get("variants") or [] if x.get("id") == variant_id), None) if variant_id else None
    desc = s.get("description") or ""
    refs = [dict(r) for r in s.get("refs") or []]
    label = s["name"]
    if v:
        label = f"{s['name']} · {v['name']}"
        extra = (v.get("description") or "").strip()
        if v.get("description_mode") == "replace":
            desc = extra or desc
        elif extra:
            desc = f"{desc.rstrip(' .;,')}, {extra}" if desc else extra
        refs = (refs if v.get("inherit_refs", True) else []) + [dict(r) for r in v.get("refs") or []]
        if v.get("mute"):
            refs = [r for r in refs if r["kind"] != "audio"]
            for r in refs:
                if r["kind"] == "video":
                    r["use_soundtrack"] = False
    return {"id": s["id"], "variant": v["id"] if v else None, "name": s["name"], "label": label,
            "kind": s.get("kind", "character"), "description": desc.strip(), "refs": refs}


def _slots(slots: Any) -> list[dict[str, Any]]:
    out, seen = [], set()
    for sl in slots or []:
        try:
            n = int(sl.get("n"))
        except (TypeError, ValueError, AttributeError):
            continue
        if n < 1 or n in seen or not sl.get("id"):
            continue
        seen.add(n)
        out.append({"n": n, "id": str(sl["id"]), "variant": sl.get("variant") or None})
    return sorted(out, key=lambda x: x["n"])


def compose(slots: Any, loose: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The reference list for these slots (subject refs first, slot order, then loose refs)."""
    lib = items()
    refs: list[dict[str, Any]] = []
    resolved = []
    for sl in _slots(slots):
        r = resolve(sl["id"], sl["variant"], lib)
        resolved.append({**r, "n": sl["n"]})
        for ref in r["refs"]:
            refs.append({**{k: v for k, v in ref.items() if k != "role"}, "role": ref.get("role") or "",
                         "subject": sl["n"]})
    have = {(r["kind"], r["file"]) for r in refs}  # a loose file a subject now brings isn't added twice
    refs += [{k: v for k, v in r.items() if k != "subject"} for r in loose or []
             if not r.get("subject") and (r.get("kind"), r.get("file")) not in have]
    graph.references_json(refs)  # raises when the slots need more references than R2V takes
    return {"refs": refs, "tags": graph.reference_tags(refs), "subjects": resolved}


# --------------------------------------------------------------------------- prompt text

def _tokens(tag: str) -> list[str]:
    return [m.group(0) for m in _TOKEN.finditer(tag or "")]


def _desc(kind: str, text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip()).rstrip(" .;,")
    if not text:
        return {"setting": "the location", "object": "the object"}.get(kind, "the person")
    if kind == "setting" and not re.match(r"(the|a|an)\b", text, re.I):
        return f"the location: {text}"
    return text


def definition_lines(subject: dict[str, Any], n: int, refs: list[dict[str, Any]], tags: list[str]) -> list[str]:
    """The subject_definitions lines for one slot: the subject line, then its sound lines."""
    visual, sounds = [], []
    for ref, tag in zip(refs, tags):
        if ref.get("subject") != n:
            continue
        role = (ref.get("role") or "").strip()
        for tok in _tokens(tag):
            if tok.startswith("<Audio"):
                sounds.append((tok, ref, role))
            else:
                visual.append(f"{tok} ({role})" if role and ref.get("kind") != "audio" else tok)
    line = f"<Subject {n}> is {_desc(subject.get('kind', 'character'), subject.get('description', ''))}"
    if visual:
        line += ", as shown in " + (visual[0] if len(visual) == 1 else ", ".join(visual[:-1]) + " and " + visual[-1])
    lines = [line + "."]
    for tok, ref, role in sounds:
        if subject.get("kind", "character") == "character" and (ref.get("kind") == "audio" or "voice" in role.lower()):
            lines.append(f"{tok} is the voice-timbre reference for <Subject {n}> (S{n}).")
        else:
            lines.append(f"{tok} is a sound reference for <Subject {n}>{f' ({role})' if role else ''}.")
    return lines


def _resolved_for(slots: Any, refs: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    lib = items()
    out = []
    for sl in _slots(slots):
        try:
            out.append((sl["n"], resolve(sl["id"], sl["variant"], lib)))
        except SubjectError:
            continue
    return out


def all_lines(slots: Any, refs: list[dict[str, Any]]) -> list[str]:
    tags = graph.reference_tags(refs)
    lines: list[str] = []
    for n, subj in _resolved_for(slots, refs):
        lines += definition_lines(subj, n, refs, tags)
    return lines


def direction_block(slots: Any, refs: list[dict[str, Any]]) -> str:
    """Appended to an Auto idea: the fixed subject lines, and how to keep them swappable."""
    resolved = _resolved_for(slots, refs)
    if not resolved:
        return ""
    lines = all_lines(slots, refs)
    top = max(n for n, _ in resolved)
    speakers = ", ".join(f"<Subject {n}> speaks as (S{n})" for n, s in resolved if s["kind"] == "character")
    return (
        "SUBJECTS (defined by the user; they override anything else about who or what these are):\n"
        + "\n".join(lines)
        + "\n\nRules for these subjects:\n"
        "- In subject_definitions, write each line above verbatim as that label's line, keep these labels and "
        f"numbers, and number any other subject you need from <Subject {top + 1}> on.\n"
        "- After subject_definitions, refer to these subjects only by their label and pronouns. Do not restate "
        "their appearance, build, hair, clothing or voice anywhere else (not even at their first appearance): the "
        "definition and the references carry it. Describe what they do, where they are, their expressions, "
        "interactions, and what they say.\n"
        + (f"- Speaker IDs: {speakers}.\n" if speakers else "")
        + "- Names the direction may use for them: "
        + ", ".join(f"{s['name']} = <Subject {n}>" for n, s in resolved)
        + ". Don't write these names into the prompt except inside dialogue.\n"
    )


# --------------------------------------------------------------------------- swapping

def _section_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    """(first body line, end) of a section, or None."""
    start = None
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if m and start is None and m.group(1).lower() == name:
            start = i + 1
        elif m and start is not None:
            return start, i
    return (start, len(lines)) if start is not None else None


def _owned_tokens(refs: list[dict[str, Any]], tags: list[str]) -> dict[int, dict[str, list[str]]]:
    """slot -> {"Picture": [...], "Video": [...], "Audio": [...]} tokens, in order."""
    out: dict[int, dict[str, list[str]]] = {}
    for ref, tag in zip(refs, tags):
        n = ref.get("subject")
        if not n:
            continue
        for tok in _tokens(tag):
            out.setdefault(int(n), {"Picture": [], "Video": [], "Audio": []})[tok[1:].split(" ")[0]].append(tok)
    return out


def _loose_tokens(refs: list[dict[str, Any]], tags: list[str]) -> list[tuple[tuple[str, str], list[str]]]:
    return [((r.get("kind"), r.get("file")), _tokens(t)) for r, t in zip(refs, tags) if not r.get("subject")]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'-]{3,}", (text or "").lower()) if w not in _STOP}


def rewrite(prompt: str, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Swap subjects in an existing prompt without regenerating it.

    before/after: {slots: [{n, id, variant}], refs: [...]} (refs carry `subject: n`).
    Returns {prompt, notes: [str], leftovers: {n: [words]}, tidy_request: str | None}."""
    text = str(prompt or "")
    b_refs, a_refs = before.get("refs") or [], after.get("refs") or []
    b_tags, a_tags = graph.reference_tags(b_refs), graph.reference_tags(a_refs)
    b_own, a_own = _owned_tokens(b_refs, b_tags), _owned_tokens(a_refs, a_tags)
    b_res = dict(_resolved_for(before.get("slots"), b_refs))
    a_res = dict(_resolved_for(after.get("slots"), a_refs))

    # 1. Old tokens -> new tokens. A slot's tokens map onto the same slot's new tokens of the
    #    same type, in order (Bob's first picture -> Steve's first picture); loose refs map by file.
    mapping: dict[str, str | None] = {}
    for n, kinds in b_own.items():
        for typ, toks in kinds.items():
            new = (a_own.get(n) or {}).get(typ) or []
            for i, tok in enumerate(toks):
                mapping[tok] = new[i] if i < len(new) else None
    a_loose = _loose_tokens(a_refs, a_tags)
    used: set[int] = set()
    for key, toks in _loose_tokens(b_refs, b_tags):
        j = next((j for j, (k, _) in enumerate(a_loose) if k == key and j not in used), None)
        if j is not None:
            used.add(j)
            new = a_loose[j][1]
        else:  # the file now comes with a subject: follow it there
            new = next((_tokens(t) for r, t in zip(a_refs, a_tags) if (r.get("kind"), r.get("file")) == key), [])
        for i, tok in enumerate(toks):
            mapping[tok] = new[i] if i < len(new) else None

    lines = text.split("\n")
    slot_ns = set(b_res) | set(a_res) | set(b_own) | set(a_own)
    old_slot_tokens = {t for kinds in b_own.values() for toks in kinds.values() for t in toks}

    # 2. Drop the slots' old lines from subject_definitions and retention_analysis.
    insert_at = None
    sd = _section_bounds(lines, "subject_definitions")
    rt = _section_bounds(lines, "retention_analysis")
    keep: list[str] = []
    for i, line in enumerate(lines):
        lead = re.match(r"^\s*[-*]?\s*(<Subject (\d+)>|<(?:Picture|Video|Audio) \d+>)", line)
        if lead and sd and sd[0] <= i < sd[1]:
            if (lead.group(2) and int(lead.group(2)) in slot_ns) or lead.group(1) in old_slot_tokens:
                if insert_at is None:
                    insert_at = len(keep)
                continue
        if lead and rt and rt[0] <= i < rt[1] and lead.group(1) in old_slot_tokens:
            continue
        keep.append(line)
    lines = keep

    # 3. Renumber every reference tag that moved (all at once, so nothing is renamed twice).
    def sub(m: re.Match) -> str:
        tok = m.group(0)
        if tok not in mapping:
            return tok
        return mapping[tok] or ""
    body = "\n".join(lines)
    body = _TOKEN.sub(sub, body)
    body = re.sub(r"\(\s*(?:and\s*)?\)", "", body)
    body = re.sub(r"[ \t]{2,}", " ", body)
    lines = body.split("\n")

    # 4. Write the slots' new lines (already in the new numbering).
    new_lines: list[str] = []
    for n in sorted(a_res):
        new_lines += definition_lines(a_res[n], n, a_refs, a_tags)
    sd = _section_bounds(lines, "subject_definitions")
    notes: list[str] = []
    if new_lines:
        if sd is None:
            lines = ["subject_definitions:", *new_lines, ""] + lines
            notes.append("Added a subject_definitions section at the top.")
        else:
            at = min(max(insert_at if insert_at is not None else sd[0], sd[0]), sd[1])
            lines[at:at] = new_lines
        # retention lines for the new slots' sounds
        rt = _section_bounds(lines, "retention_analysis")
        if rt:
            extra = []
            for n in sorted(a_res):
                for tok in (a_own.get(n) or {}).get("Audio") or []:
                    if not any(ln.strip().startswith(tok) for ln in lines[rt[0]:rt[1]]):
                        extra.append(f"{tok}: reference - <Subject {n}> follows {tok} without copying its signal.")
            pos = rt[1]
            while pos > rt[0] and not lines[pos - 1].strip():
                pos -= 1
            lines[pos:pos] = extra

    out = "\n".join(lines)
    # 5. What the old subjects left behind in the rest of the prompt.
    sd = _section_bounds(out.split("\n"), "subject_definitions")
    rest = "\n".join(ln for i, ln in enumerate(out.split("\n")) if not (sd and sd[0] - 1 <= i < sd[1])).lower()
    leftovers: dict[int, list[str]] = {}
    changes = []
    for n in sorted(slot_ns):
        old, new = b_res.get(n), a_res.get(n)
        if old and (not new or (old["id"], old["variant"]) != (new["id"], new["variant"])):
            changes.append((n, old, new))
            gone = sorted(w for w in _words(old["description"]) - _words((new or {}).get("description", ""))
                          if re.search(rf"\b{re.escape(w)}\b", rest))
            if gone:
                leftovers[n] = gone
        if not new and f"<subject {n}>" in rest:
            notes.append(f"<Subject {n}> was removed but the prompt still mentions it.")
    for n, words in leftovers.items():
        notes.append(f"<Subject {n}>: the prompt still mentions {', '.join(words[:8])} from {b_res[n]['label']}.")
    tidy = None
    if changes:
        parts = []
        for n, old, new in changes:
            if new:
                parts.append(f"<Subject {n}> is now {new['label']}: {_desc(new['kind'], new['description'])}. "
                             f"It used to be {old['label']}: {_desc(old['kind'], old['description'])}.")
            else:
                parts.append(f"<Subject {n}> ({old['label']}) was removed from the clip.")
        tidy = ("Subject swap. " + " ".join(parts) + " Update only what still describes the old subject (appearance, "
                "clothing, voice, name, pronouns) so it matches the new definition, or remove it where the definition "
                "already covers it. Keep subject_definitions, every label and tag, the shots, timings, actions and "
                "all other wording exactly as they are.")
    return {"prompt": out, "notes": notes, "leftovers": leftovers, "tidy_request": tidy}
