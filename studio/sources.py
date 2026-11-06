"""Install by link or id: CivitAI pages / download links / ids / AIR, Hugging Face, direct URLs.

`parse(text)` turns whatever was pasted into a source description without any
network call. `lookup(source)` asks the site what is actually there (model name,
version, base model, every file with its size and mode) so you can check it
before downloading. `install(source, kind, picks)` adds it to the catalog (LoRA)
or the base-model list (checkpoint) and queues the download; with check off,
`quick_install(text, kind)` does lookup + install in one go with the suggested
files (for Hugging Face file links and direct URLs it skips the lookup entirely).
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests

from . import civitai, loras

CIVITAI_KINDS = {"LORA": "lora", "LoCon": "lora", "DoRA": "lora", "Checkpoint": "checkpoint"}
BIG_BYTES = 4 * 1024 ** 3  # bigger than this is a diffusion model, not a LoRA


class SourceError(civitai.CivitaiError):
    pass


# --------------------------------------------------------------------------- parse

_AIR = re.compile(r"urn:air:[^:\s]+:([a-z]+):civitai:(\d+)(?:@(\d+))?", re.I)
_HF_FILE = re.compile(r"^/(?:(datasets|spaces)/)?([^/]+/[^/]+)/(?:blob|resolve)/([^/]+)/(.+)$")
_HF_REPO = re.compile(r"^/(?:(datasets|spaces)/)?([^/]+/[^/]+?)(?:/tree/([^/]+)(?:/(.*))?)?/?$")


def parse(text: str) -> dict[str, Any]:
    t = str(text or "").strip().strip("<>\"'")
    if not t:
        raise SourceError("Paste a link or an id first")
    m = _AIR.search(t)
    if m:
        kind = {"lora": "lora", "lycoris": "lora", "checkpoint": "checkpoint"}.get(m.group(1).lower())
        if m.group(3):
            return {"source": "civitai", "version_id": int(m.group(3)), "model_id": int(m.group(2)), "kind": kind}
        return {"source": "civitai", "model_id": int(m.group(2)), "kind": kind}
    m = re.fullmatch(r"(?:(m|model|v|version)\s*[:#]?\s*)?(\d{3,})", t, re.I)
    if m:
        key = "model_id" if (m.group(1) or "").lower() in ("m", "model") else "version_id"
        return {"source": "civitai", key: int(m.group(2))}
    if t.lower().startswith("hf:"):
        t = t[3:].strip().lstrip("/")
    if not re.match(r"https?://", t, re.I):
        # owner/repo or owner/repo/path/file.safetensors (Hugging Face shorthand)
        if re.fullmatch(r"[\w.-]+/[\w.-]+(/[^\s]+)?", t):
            parts = t.split("/")
            if len(parts) == 2:
                return {"source": "hf", "repo": t, "revision": "main", "path": None}
            return {"source": "hf", "repo": "/".join(parts[:2]), "revision": "main", "path": "/".join(parts[2:])}
        raise SourceError("That doesn't look like a link or an id. Paste a CivitAI or Hugging Face link, a CivitAI "
                          "version id, or a direct link to a .safetensors file.")
    u = urlparse(t)
    host = (u.hostname or "").lower()
    q = parse_qs(u.query)
    if "civitai" in host:
        m = re.search(r"/api/download/models/(\d+)", u.path)
        if m:
            out: dict[str, Any] = {"source": "civitai", "version_id": int(m.group(1))}
            if (q.get("fileId") or [""])[0].isdigit():
                out["file_id"] = int(q["fileId"][0])
            return out
        m = re.search(r"/(?:api/v1/)?model-versions/(\d+)", u.path)
        if m:
            return {"source": "civitai", "version_id": int(m.group(1))}
        m = re.search(r"/models/(\d+)", u.path)
        if m:
            vid = (q.get("modelVersionId") or [""])[0]
            if vid.isdigit():
                return {"source": "civitai", "version_id": int(vid), "model_id": int(m.group(1))}
            return {"source": "civitai", "model_id": int(m.group(1))}
        raise SourceError("That CivitAI link doesn't point at a model. Open the model's page and copy that link.")
    if host in ("huggingface.co", "www.huggingface.co", "hf.co"):
        path = unquote(u.path)
        m = _HF_FILE.match(path)
        if m:
            return {"source": "hf", "repo": (f"{m.group(1)}/" if m.group(1) else "") + m.group(2),
                    "revision": m.group(3), "path": m.group(4)}
        m = _HF_REPO.match(path)
        if m:
            return {"source": "hf", "repo": (f"{m.group(1)}/" if m.group(1) else "") + m.group(2),
                    "revision": m.group(3) or "main", "path": None, "folder": m.group(4) or ""}
        raise SourceError("That Hugging Face link doesn't point at a repo or file")
    return {"source": "url", "url": t}


# --------------------------------------------------------------------------- lookup

def _hf_url(src: dict[str, Any], path: str) -> str:
    return f"https://huggingface.co/{src['repo']}/resolve/{src.get('revision') or 'main'}/{path}"


def _guess_kind(name: str, size: int | None) -> str:
    n = name.lower()
    if "lora" in n or ("turbo" in n and size and size < BIG_BYTES):
        return "lora"
    if "diffusion_models" in n or "unet" in n or (size and size >= BIG_BYTES):
        return "checkpoint"
    return "lora"


def _file_rows(files: list[dict[str, Any]], version_name: str = "") -> list[dict[str, Any]]:
    rows = civitai.classify_files(files, version_name)
    for r in rows:
        if not str(r["name"]).lower().endswith(".safetensors"):
            r["unsupported"] = "Studio can only load .safetensors files"
            r["suggested"] = False
    return rows


def _headers(url: str) -> dict[str, str]:
    return {"User-Agent": "MMH3-Studio", **loras._auth_for(url)}


def _probe(url: str) -> dict[str, Any]:
    """Size and file name of a direct link, without downloading it."""
    try:
        r = requests.head(url, headers=_headers(url), allow_redirects=True, timeout=30)
        if r.status_code in (403, 405, 501) or (r.status_code == 200 and not r.headers.get("Content-Length")):
            r = requests.get(url, headers={**_headers(url), "Range": "bytes=0-0"}, stream=True, timeout=30)
            r.close()
    except requests.RequestException as exc:
        raise SourceError(f"Can't reach that link from the pod ({type(exc).__name__})") from None
    src = loras._source_of(url)
    if r.status_code in (401, 403):
        raise SourceError(loras.REFUSED.get(src, f"{src} refused the request (HTTP {r.status_code})"))
    if r.status_code == 404:
        raise SourceError(f"{src} says that file doesn't exist")
    if r.status_code >= 400:
        raise SourceError(f"{src} answered HTTP {r.status_code}")
    if r.headers.get("Content-Type", "").startswith("text/html"):
        raise SourceError("That link opens a web page, not a file. Use the file's download link.")
    size = None
    rng = r.headers.get("Content-Range", "")
    if "/" in rng and rng.rsplit("/", 1)[1].isdigit():
        size = int(rng.rsplit("/", 1)[1])
    elif r.headers.get("X-Linked-Size", "").isdigit():
        size = int(r.headers["X-Linked-Size"])
    elif r.headers.get("Content-Length", "").isdigit() and r.status_code == 200:
        size = int(r.headers["Content-Length"])
    name = None
    cd = r.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    if m:
        name = unquote(m.group(1)).strip()
    if not name:
        name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return {"size": size, "name": name}


def lookup(src: dict[str, Any]) -> dict[str, Any]:
    """What a source actually contains: title, kind, base model, files (with sizes and modes)."""
    kind = src["source"]
    if kind == "civitai":
        return _lookup_civitai(src)
    if kind == "hf":
        return _lookup_hf(src)
    info = _probe(src["url"])
    files = _file_rows([{"name": info["name"], "url": src["url"], "size_kb": (info["size"] or 0) / 1024 or None}])
    return {"source": "url", "title": info["name"], "subtitle": loras._source_of(src["url"]),
            "kind": src.get("kind") or _guess_kind(info["name"], info["size"]), "kind_known": False,
            "files": files, "url": src["url"]}


def _lookup_civitai(src: dict[str, Any]) -> dict[str, Any]:
    model: dict[str, Any] = {}
    vid = src.get("version_id")
    if src.get("model_id"):
        model = civitai._get(f"/api/v1/models/{int(src['model_id'])}")
    if not vid:
        versions = model.get("modelVersions") or []
        if not versions:
            raise SourceError("That CivitAI model has no versions")
        pick = next((v for v in versions if v.get("baseModel") == civitai.H3_BASE_MODEL), versions[0])
        vid = pick["id"]
    raw = civitai._get(f"/api/v1/model-versions/{int(vid)}")
    v = civitai.normalize_version(raw, model or None)
    if not model and v.get("model_id"):
        try:
            model = civitai._get(f"/api/v1/models/{int(v['model_id'])}")
        except civitai.CivitaiError:
            model = {}
    mtype = (raw.get("model") or {}).get("type") or model.get("type")
    files = v["files"]
    if src.get("file_id"):
        for f in files:
            f["suggested"] = f["id"] == src["file_id"]
    base = v.get("base_model")
    return {
        "source": "civitai", "version_id": v["id"], "model_id": v.get("model_id"),
        "title": (raw.get("model") or {}).get("name") or model.get("name") or f"Version {v['id']}",
        "subtitle": f"{v.get('name') or ''} · {base or 'unknown base model'}",
        "kind": src.get("kind") or CIVITAI_KINDS.get(mtype or "", "lora"), "kind_known": mtype in CIVITAI_KINDS,
        "civitai_type": mtype, "base_model": base, "image": v.get("image"), "trained_words": v.get("trained_words") or [],
        "warning": (f"This is a {base} model, not MiniMax H3. It won't work here." if base and base != civitai.H3_BASE_MODEL
                    else None),
        "versions": [{"id": x.get("id"), "name": x.get("name"), "base_model": x.get("baseModel")}
                     for x in (model.get("modelVersions") or [])][:30],
        "files": files,
    }


def _lookup_hf(src: dict[str, Any]) -> dict[str, Any]:
    if src.get("path"):
        url = _hf_url(src, src["path"])
        info = _probe(url)
        name = src["path"].split("/")[-1]
        files = _file_rows([{"name": name, "url": url, "size_kb": (info["size"] or 0) / 1024 or None,
                             "path": src["path"]}])
        return {"source": "hf", "title": name, "subtitle": src["repo"],
                "kind": src.get("kind") or _guess_kind(src["path"], info["size"]), "kind_known": False,
                "files": files, "repo": src["repo"]}
    api = f"https://huggingface.co/api/models/{src['repo']}/tree/{src.get('revision') or 'main'}"
    if src.get("folder"):
        api += "/" + src["folder"].strip("/")
    try:
        r = requests.get(api, params={"recursive": "true"}, headers=_headers("https://huggingface.co/"), timeout=30)
    except requests.RequestException as exc:
        raise SourceError(f"Can't reach Hugging Face from the pod ({type(exc).__name__})") from None
    if r.status_code in (401, 403):
        raise SourceError(loras.REFUSED["Hugging Face"])
    if r.status_code == 404:
        raise SourceError(f"Hugging Face has no repo called {src['repo']}")
    if r.status_code >= 400:
        raise SourceError(f"Hugging Face answered HTTP {r.status_code}")
    entries = [e for e in r.json() if e.get("type") == "file" and str(e.get("path", "")).lower().endswith(".safetensors")]
    if not entries:
        raise SourceError("That repo has no .safetensors files")
    files = _file_rows([{"name": e["path"].split("/")[-1], "path": e["path"], "url": _hf_url(src, e["path"]),
                         "size_kb": ((e.get("lfs") or {}).get("size") or e.get("size") or 0) / 1024 or None}
                        for e in entries[:200]])
    for f in files:  # don't pre-tick a whole repo: only the lone file, if there is just one
        f["suggested"] = len(files) == 1
    sizes = [f.get("size_kb") or 0 for f in files]
    return {"source": "hf", "title": src["repo"], "subtitle": f"{len(files)} .safetensors file{'s' if len(files) != 1 else ''}",
            "kind": src.get("kind") or ("checkpoint" if max(sizes) * 1024 >= BIG_BYTES and min(sizes) * 1024 >= BIG_BYTES
                                        else "lora"), "kind_known": False,
            "files": files, "repo": src["repo"]}


# --------------------------------------------------------------------------- install

def install(found: dict[str, Any], kind: str, picks: list[Any] | None = None,
            name: str | None = None) -> dict[str, Any]:
    """Install looked-up files. picks: CivitAI file ids, or file urls for Hugging Face / direct links."""
    if kind not in ("lora", "checkpoint"):
        raise SourceError("Choose LoRA or base model")
    files = [f for f in found.get("files") or [] if not f.get("unsupported")]
    if not files:
        bad = next((f for f in found.get("files") or [] if f.get("unsupported")), None)
        raise SourceError(f"{bad['name']}: {bad['unsupported']}" if bad else "There's no model file to install")
    if picks:
        chosen = [f for f in files if f.get("id") in picks or f.get("url") in picks]
    else:
        chosen = [f for f in files if f.get("suggested")] or files[:1]
    if not chosen:
        raise SourceError("Pick at least one file")
    if found["source"] == "civitai":
        vid = int(found["version_id"])
        if kind == "checkpoint":
            label = name or f"{found.get('title')} · {(found.get('subtitle') or '').split(' · ')[0]}".strip(" ·")
            return {"kind": kind, "item": loras.add_checkpoint(vid, chosen[0]["id"], label)}
        entry = loras.add_version(vid, [f["id"] for f in chosen], {"nickname": name} if name else None)
        return {"kind": kind, "item": entry}
    rows = [{"name": f.get("name"), "url": f["url"], "size_kb": f.get("size_kb"), "family": f.get("family"),
             "role": f.get("role")} for f in chosen]
    if kind == "checkpoint":
        return {"kind": kind, "item": loras.add_url_checkpoint(rows, name)}
    return {"kind": kind, "item": loras.add_url_lora(rows, name, source=found.get("url") or found.get("repo"))}


def quick_install(text: str, kind: str | None = None, name: str | None = None) -> dict[str, Any]:
    """Check off: install straight from what was pasted. A Hugging Face file link or a direct
    link needs no lookup at all; CivitAI still has to say what the version's files are."""
    src = parse(text)
    if kind in ("lora", "checkpoint"):
        src["kind"] = kind
    if src["source"] == "url" or (src["source"] == "hf" and src.get("path")):
        url = src["url"] if src["source"] == "url" else _hf_url(src, src["path"])
        fname = unquote(urlparse(url).path.rsplit("/", 1)[-1])
        found = {"source": src["source"], "url": url, "repo": src.get("repo"),
                 "files": _file_rows([{"name": fname, "url": url}])}
        for f in found["files"]:
            f["suggested"] = True
        return install(found, src.get("kind") or _guess_kind(url, None), None, name)
    if src["source"] == "hf":
        raise SourceError("A repo link needs a file. Link the file itself, or turn the check on to pick one.")
    found = lookup(src)
    return install(found, src.get("kind") or found["kind"], None, name)
