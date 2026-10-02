"use strict";
/* MMH3 Studio phone UI. No framework, no build step. */

// ------------------------------------------------------------------ helpers

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = (p) => String(p).split("/").map(encodeURIComponent).join("/");
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M7 5l12 7-12 7z"/></svg>',
  star: '<svg viewBox="0 0 24 24"><path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.8z"/></svg>',
  reuse: '<svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 0114-5.3M20 12a8 8 0 01-14 5.3M18 3v4h-4M6 21v-4h4"/></svg>',
  next: '<svg viewBox="0 0 24 24"><path d="M5 5l9 7-9 7zM17 5v14"/></svg>',
  ref: '<svg viewBox="0 0 24 24"><circle cx="9" cy="9" r="4"/><path d="M3.5 20c.8-3.2 3-5 5.5-5s4.7 1.8 5.5 5M16 8h5M18.5 5.5v5"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3.5 7.5a2 2 0 012-2h4l2 2h7a2 2 0 012 2v8a2 2 0 01-2 2h-13a2 2 0 01-2-2z"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  chev: '<svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>',
  image: '<svg viewBox="0 0 24 24"><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="M20.5 16l-5-5-8.5 8.5"/></svg>',
  video: '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10.5l5-3v9l-5-3"/></svg>',
  audio: '<svg viewBox="0 0 24 24"><path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/></svg>',
  up: '<svg viewBox="0 0 24 24"><path d="M12 20V9M7 14l5-5 5 5M5 4h14"/></svg>',
  spark: '<svg viewBox="0 0 24 24"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></svg>',
  arrowUp: '<svg viewBox="0 0 24 24"><path d="M12 19V5M6 11l6-6 6 6"/></svg>',
  cube: '<svg viewBox="0 0 24 24"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5"/></svg>',
  cpu: '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>',
  text: '<svg viewBox="0 0 24 24"><path d="M5 6h14M5 11h14M5 16h9"/></svg>',
  log: '<svg viewBox="0 0 24 24"><path d="M6 4h9l4 4v12H6zM14 4v5h5M9 13h7M9 17h5"/></svg>',
  globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.7 2.5 14.3 0 17M12 3.5c-2.5 2.7-2.5 14.3 0 17"/></svg>',
};
const KIND_ICON = { image: ICON.image, video: ICON.video, audio: ICON.audio };
const MODES = { t2v: "Text", i2v: "Image", r2v: "Reference" };
const FAMILY = { t2v: "fl2v", i2v: "fl2v", r2v: "ref2v" };
const qualityName = (mp) => (mp < 0.6 ? "draft" : mp < 0.85 ? "standard" : mp < 1.05 ? "high" : "max");

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers: {} };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  let r;
  try { r = await fetch(path, init); } catch (e) { throw new Error("Studio is unreachable. Is the pod running?"); }
  if (r.status === 401) { showLogin(); throw new Error("login required"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false; t.classList.toggle("err", err);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), err ? 6000 : 2600);
}
const fail = (e) => toast(e.message || String(e), true);

function bytes(n) { if (!n) return "0 B"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return `${n.toFixed(i > 1 ? 1 : 0)} ${u[i]}`; }
function clock(s) { s = Math.max(0, Math.round(s || 0)); const m = Math.floor(s / 60); return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; }
function ago(t) { const s = Date.now() / 1000 - t; if (s < 60) return "just now"; if (s < 3600) return `${Math.floor(s / 60)} min ago`; if (s < 86400) return `${Math.floor(s / 3600)} h ago`; return new Date(t * 1000).toLocaleDateString(); }
function resolution(aspect, mp) {
  const [w, h] = aspect.split(":").map(Number); const scale = Math.sqrt(mp * 1024 * 1024 / (w * h));
  return [Math.round(w * scale / 32) * 32, Math.round(h * scale / 32) * 32];
}
function frames(sec) { const n = Math.max(5, Math.round(sec * 24)); return n + ((5 - (n % 17)) % 17 + 17) % 17; }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
const thumbIn = (f) => `/media/input-thumb/${enc(f)}`;
const thumbOut = (f) => `/media/output-thumb/${enc(f)}`;
const mediaOut = (f) => `/media/output/${enc(f)}`;
const mediaIn = (f) => `/media/input/${enc(f)}`;
const nameOf = (f) => String(f || "").split("/").pop();

// ------------------------------------------------------------------ app state

const S = {
  boot: null, live: null, route: "create",
  assets: null, kits: [], loras: null, outputs: null,
  lib: { filter: "all", group: "" },
};

const DEFAULT_FORM = {
  mode: "t2v", auto: false, idea: "", prompt: "", drafted: null,
  aspect: "9:16", megapixels: 0.7, duration: 5, recipe: {}, adv: {}, checkpoint: "stock",
  seed: "", lockSeed: false, count: 1, loras: [], start_image: null, end_image: null, refs: [], updated: 0,
};
let F = loadLocalForm();

function loadLocalForm() {
  try { return { ...DEFAULT_FORM, ...JSON.parse(localStorage.getItem("mmh3.form") || "{}") }; } catch { return { ...DEFAULT_FORM }; }
}
const pushForm = debounce(() => api("/api/form", { method: "PUT", body: F }).catch(() => {}), 1500);
function saveForm() {
  F.updated = Date.now();
  try { localStorage.setItem("mmh3.form", JSON.stringify(F)); } catch {}
  pushForm();
}

// ------------------------------------------------------------------ sheet

let sheetOnClose = null;
let sheetKeys = null;  // extra key handling for the open sheet (the clip player's ← →)
function openSheet(title, html, onMount, onClose) {
  sheetKeys = null;
  $("#sheetTitle").textContent = title;
  $("#sheetBody").innerHTML = html;
  $("#sheet").hidden = false; $("#scrim").hidden = false;
  document.body.style.overflow = "hidden";
  sheetOnClose = onClose || null;
  if (onMount) onMount($("#sheetBody"));
}
function closeSheet() {
  $$("#sheetBody video").forEach((v) => v.pause());
  $("#sheet").hidden = true; $("#scrim").hidden = true;
  document.body.style.overflow = "";
  const cb = sheetOnClose; sheetOnClose = null; if (cb) cb();
}
$("#sheetClose").onclick = closeSheet;
$("#scrim").onclick = closeSheet;
document.addEventListener("keydown", (e) => {
  if ($("#sheet").hidden) return;
  if (e.key === "Escape") closeSheet();
  else if (sheetKeys && !e.target.matches("input, textarea, select")) sheetKeys(e);
});

// ------------------------------------------------------------------ routing

const VIEWS = {};
function route() {
  const name = (location.hash || "#create").slice(1).split("/")[0] || "create";
  S.route = VIEWS[name] ? name : "create";
  $$("#tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === S.route));
  window.scrollTo(0, 0);
  render();
}
function render() { VIEWS[S.route](); }
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ file picking / upload

function pickFiles(accept, multiple = true) {
  return new Promise((resolve) => {
    const input = $("#filePick");
    input.accept = accept; input.multiple = multiple; input.value = "";
    input.onchange = () => resolve([...input.files]);
    input.click();
  });
}
async function upload(files) {
  if (!files.length) return [];
  const fd = new FormData(); files.forEach((f) => fd.append("file", f, f.name));
  toast(files.length > 1 ? `Uploading ${files.length} files…` : "Uploading…");
  const res = await api("/api/upload", { body: fd });
  S.assets = null;
  return res.items;
}
const ACCEPT = { image: "image/*", video: "video/*", audio: "audio/*" };

async function loadAssets(force = false) {
  if (!S.assets || force) { const d = await api("/api/assets"); S.assets = d.items; S.kits = d.kits; }
  return S.assets;
}

/* Asset picker sheet. kinds: ["image"], multi: max count or 1. Resolves with chosen assets. */
function pickAsset({ title, kinds, max = 1 }) {
  return new Promise(async (resolve) => {
    let chosen = [];
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    const accept = kinds.map((k) => ACCEPT[k]).join(",");
    const draw = (body, items) => {
      const list = items.filter((a) => kinds.includes(a.kind));
      body.innerHTML = `
        <div class="row" style="margin-bottom:12px">
          <button class="btn grow" data-up>${ICON.up} Upload from phone</button>
        </div>
        ${list.length ? `<div class="assets">${list.map((a) => assetTile(a, chosen.some((c) => c.file === a.file))).join("")}</div>`
        : `<div class="empty"><h2>Nothing here yet</h2><p>Upload ${kinds.join(" / ")} files from your phone.</p></div>`}
        ${max > 1 ? `<div class="sheet-foot"><button class="btn primary" data-use>Use ${chosen.length || ""} selected</button></div>` : ""}`;
      $("[data-up]", body).onclick = async () => {
        try {
          const up = await upload(await pickFiles(accept, max > 1));
          if (!up.length) return;
          if (max === 1) { finish(up.slice(0, 1)); closeSheet(); return; }
          chosen = chosen.concat(up).slice(0, max);
          draw(body, await loadAssets(true));
        } catch (e) { fail(e); }
      };
      $$(".asset", body).forEach((el) => el.onclick = () => {
        const a = list.find((x) => x.file === el.dataset.file);
        if (max === 1) { finish([a]); closeSheet(); return; }
        const i = chosen.findIndex((c) => c.file === a.file);
        if (i >= 0) chosen.splice(i, 1); else if (chosen.length < max) chosen.push(a); else toast(`Up to ${max}`);
        draw(body, list);
      });
      const use = $("[data-use]", body); if (use) use.onclick = () => { finish(chosen); closeSheet(); };
    };
    openSheet(title, `<div class="empty">Loading…</div>`, async (body) => {
      try { draw(body, await loadAssets()); } catch (e) { fail(e); }
    }, () => finish([]));
  });
}
function assetTile(a, sel = false) {
  const img = a.kind === "audio" ? `<span class="kind">${ICON.audio}</span>` : `<img loading="lazy" src="${thumbIn(a.file)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'kind',innerHTML:'${a.kind === "video" ? "▶" : ""}'}))">`;
  return `<button class="asset${sel ? " sel" : ""}" data-file="${esc(a.file)}" title="${esc(a.nickname || nameOf(a.file))}">${img}<span class="check">${ICON.check}</span><span class="name">${esc(a.nickname || nameOf(a.file))}</span></button>`;
}

// ------------------------------------------------------------------ CREATE

/* The recipe Create will use: your pick if it's still offered (or is the audio/video
   recipe), else the family default. */
function recipeFor(mode) {
  const fam = S.boot.recipes[FAMILY[mode]];
  const id = F.recipe[FAMILY[mode]] || fam.default;
  const offered = (r) => !fam.hidden.includes(r.id) || r.id === fam.av_recipe;
  return fam.recipes.find((r) => r.id === id && offered(r)) || fam.recipes.find((r) => r.id === fam.default) || fam.recipes[0];
}
function visibleRecipes(famId, current) {
  const fam = S.boot.recipes[famId];
  const list = fam.recipes.filter((r) => !fam.hidden.includes(r.id));
  if (current && !list.some((r) => r.id === current.id)) list.push(current);
  return list;
}

/* R2V: when a video or audio reference appears, switch to the family's audio/video recipe
   (Legacy · Euler by default), and switch back when the last one goes. Only on changes,
   so picking another recipe by hand sticks. */
function syncAvRecipe() {
  if (F.mode !== "r2v") return;
  const now = F.refs.some((r) => r.kind === "video" || r.kind === "audio");
  if (F.avState === undefined || F.avState === null) { F.avState = now; return; }
  if (now === F.avState) return;
  F.avState = now;
  const fam = S.boot.recipes.ref2v;
  const av = fam.recipes.find((r) => r.id === fam.av_recipe);
  if (!av) return;
  const cur = recipeFor("r2v");
  if (now && cur.id !== av.id) {
    F.recipeBeforeAv = cur.id; F.recipe.ref2v = av.id; F.adv = {};
    toast(`Video/audio reference: using ${av.label}`);
  } else if (!now && F.recipeBeforeAv && cur.id === av.id) {
    const back = fam.recipes.find((r) => r.id === F.recipeBeforeAv);
    F.recipe.ref2v = F.recipeBeforeAv; F.recipeBeforeAv = null; F.adv = {};
    if (back) toast(`Back to ${back.label}`);
  }
  saveForm();
}

VIEWS.create = function () {
  const b = S.boot; if (!b) return;
  syncAvRecipe();
  const fam = FAMILY[F.mode];
  const famInfo = S.live && S.live.families ? S.live.families[fam] : null;
  const recipe = recipeFor(F.mode);
  const [w, h] = resolution(F.aspect, F.megapixels);
  const nf = frames(F.duration);
  const ck = b.checkpoints.find((c) => c.id === F.checkpoint) || b.checkpoints[0];
  const ckMissing = ck && ck.available && ck.available[fam] === false;
  const drafted = F.drafted && F.drafted.mode === F.mode && F.drafted.idea === F.idea;
  $("#view").innerHTML = `
  <div class="page">
    <div class="seg big" id="modeSeg">${Object.entries(MODES).map(([k, v]) => `<button data-mode="${k}" class="${F.mode === k ? "on" : ""}">${v}</button>`).join("")}</div>
    ${famInfo && !famInfo.ready ? `<p class="note warn">Models for ${MODES[F.mode]} are still downloading (${esc(famInfo.missing.join(", "))}). You can queue now; jobs start when they finish.</p>` : ""}

    <section class="block">
      <div class="block-head">
        <h3>${F.auto ? "Your idea" : "Prompt"}</h3>
        <div class="seg" style="flex:none">
          <button data-auto="0" class="${F.auto ? "" : "on"}">Write it</button>
          <button data-auto="1" class="${F.auto ? "on" : ""}">${ICON.spark} Auto</button>
        </div>
      </div>
      <textarea class="prompt" id="promptBox" placeholder="${F.auto ? "A short idea. Studio writes the full MiniMax prompt (shots, sound, music) when it's queued." : "integrated_multimodal_description: …\n\noverall_soundscape: …\n\nnon_diegetic_music: …"}">${esc(F.auto ? F.idea : F.prompt)}</textarea>
      ${F.auto ? `
        <div class="prompt-tools">
          <button class="btn small" id="draftBtn">${ICON.spark} ${drafted ? "Rewrite prompt" : "Preview the prompt"}</button>
          <span class="faint" style="font-size:12px">${b.prompting.key ? "" : "Needs an OpenRouter key (More → System)"}</span>
        </div>
        ${drafted ? `<div class="drafted"><div class="row"><span class="grow muted">This exact prompt will be used. Edit freely.</span><button class="btn small ghost" id="dropDraft">Discard</button></div><textarea id="draftBox">${esc(F.drafted.prompt)}</textarea>${tweakBox()}</div>` : ""}
      ` : `<div class="prompt-tools"><button class="btn small ghost" id="toAuto">${ICON.spark} Start from an idea instead</button></div>
        ${tweakBox({ hidden: !F.prompt.trim() })}`}
    </section>

    ${F.mode === "i2v" ? `
    <section class="block">
      <div class="block-head"><h3>Frames</h3><span class="hint">Start, end, or both</span></div>
      <div class="frames">
        ${frameSlot("start_image", "Start frame")}
        ${frameSlot("end_image", "End frame")}
      </div>
      <p class="note">${F.start_image && F.end_image ? "The clip travels from the start frame to the end frame." : F.end_image ? "The clip builds up to and lands on the end frame." : F.start_image ? "The clip starts on this frame. Add an end frame to choose where it lands." : "Pick a start frame, an end frame, or both."}</p>
    </section>` : ""}

    ${F.mode === "r2v" ? `
    <section class="block">
      <div class="block-head"><h3>References</h3><span class="hint">${refCounts()}</span></div>
      <div class="refs" id="refs">${refTiles()}</div>
      <div class="row" style="margin-top:10px">
        <button class="btn small" id="kitLoad">${ICON.folder} Load kit</button>
        <button class="btn small" id="kitSave" ${F.refs.length ? "" : "disabled"}>Save as kit</button>
        <span class="grow"></span>
        <button class="btn small ghost" id="refsClear" ${F.refs.length ? "" : "disabled"}>Clear</button>
      </div>
      <p class="note">Mention references in your prompt by their tag, like &lt;Picture 1&gt;. Auto does this for you.</p>
    </section>` : ""}

    <section class="block">
      <div class="block-head"><h3>Shape</h3><span class="hint num">${w}×${h} · ${(nf / 24).toFixed(2)} s · ${nf} frames</span></div>
      <div class="chips scroll" id="aspects">${b.aspects.map((a) => `<button class="chip ${F.aspect === a ? "on" : ""}" data-aspect="${a}">${a}</button>`).join("")}</div>
      <div style="margin-top:12px">
        <label class="field" for="dur">Length <span class="num">${F.duration} s</span></label>
        <input type="range" id="dur" min="${b.limits.duration[0]}" max="${b.limits.duration[1]}" step="0.5" value="${F.duration}">
      </div>
      <div style="margin-top:12px">
        <label class="field" for="mp">Quality <span class="num">${Number(F.megapixels).toFixed(2)} MP</span> <span class="faint">${qualityName(F.megapixels)}</span></label>
        <input type="range" id="mp" min="${b.limits.megapixels[0]}" max="${b.limits.megapixels[1]}" step="0.05" value="${F.megapixels}">
      </div>
    </section>

    <section class="block">
      <div class="block-head"><h3>Speed</h3><span class="hint">${recipePasses(recipe)} model passes</span></div>
      ${(() => {
        const vis = visibleRecipes(fam, recipe);
        return vis.length <= 3
          ? `<div class="seg" id="recipes">${vis.map((r) => `<button data-recipe="${esc(r.id)}" class="${r.id === recipe.id ? "on" : ""}">${esc(r.label)}</button>`).join("")}</div>`
          : `<div class="chips scroll" id="recipes">${vis.map((r) => `<button data-recipe="${esc(r.id)}" class="chip ${r.id === recipe.id ? "on" : ""}">${esc(r.label)}</button>`).join("")}</div>`;
      })()}
      <div class="row" style="align-items:flex-start;margin-top:6px"><p class="note grow" style="margin:0">${esc(recipe.note || "")}</p>
        <button class="btn small ghost" id="fullCfg" title="Every setting this clip will be sampled with">Full settings</button></div>
      ${advancedBlock(recipe)}
    </section>

    <section class="block">
      <div class="block-head"><h3>LoRAs</h3><button class="btn small" id="addLora">${ICON.plus} Add</button></div>
      <div id="loraChips">${loraChips()}</div>
      ${F.loras.length ? "" : `<p class="note">None. Your turbo LoRA is applied automatically.</p>`}
    </section>

    <details class="more block" ${F.moreOpen ? "open" : ""} id="moreBox">
      <summary>${ICON.chev} Seed, base model, variations</summary>
      <div class="stack">
        <div>
          <label class="field">Seed</label>
          <div class="row">
            <input type="number" id="seed" class="grow" placeholder="Random every time" value="${esc(F.seed)}" ${F.lockSeed ? "" : "disabled"}>
            <label class="switch" title="Keep this seed"><input type="checkbox" id="lockSeed" ${F.lockSeed ? "checked" : ""}><span></span></label>
          </div>
        </div>
        <div>
          <label class="field" for="ck">Base model</label>
          <select id="ck">${b.checkpoints.map((c) => `<option value="${esc(c.id)}" ${c.id === F.checkpoint ? "selected" : ""}>${esc(c.label)}${c.available[fam] === false ? " (not downloaded)" : c.available[fam] === undefined ? " (no model for this mode)" : ""}</option>`).join("")}</select>
          ${ckMissing ? `<p class="note warn">Not on disk yet. Download it in More → Models.</p>` : ""}
        </div>
        <div>
          <label class="field">Variations (different seeds)</label>
          <div class="seg" id="count">${[1, 2, 3, 4].map((n) => `<button data-count="${n}" class="${F.count === n ? "on" : ""}">${n}</button>`).join("")}</div>
        </div>
      </div>
    </details>
  </div>
  <div class="actionbar"><div class="actionbar-inner">
    ${S.boot.recipes[fam].compare.length >= 2 ? `<button class="btn" id="compareBtn" title="Same prompt and seed, one clip per recipe: ${esc(S.boot.recipes[fam].compare.map((id) => (S.boot.recipes[fam].recipes.find((r) => r.id === id) || {}).label || id).join(", "))}">Compare ${S.boot.recipes[fam].compare.length}</button>` : ""}
    <button class="btn primary" id="genBtn">${F.count > 1 ? `Generate ${F.count}` : "Generate"}</button>
  </div></div>`;
  bindCreate();
};

// ------------------------------------------------------------------ tweak (edit a prompt by asking)

const TWEAKS = ["More detail", "Tighter", "Add dialogue", "Slower camera", "One continuous shot", "No music"];

/* What the prompt writer needs to know about the clip: mode, shape, frames, references. */
function promptContext() {
  return { mode: F.mode, idea: F.idea, aspect: F.aspect, megapixels: F.megapixels, duration: F.duration,
    start_image: F.start_image, end_image: F.end_image, refs: F.refs };
}

function tweakBox({ hidden = false, canUndo } = {}) {
  const undo = canUndo ?? (F.tweakUndo || []).some((u) => u.auto === F.auto);
  return `<div class="tweak" id="tweak" ${hidden ? "hidden" : ""}>
    <form class="tweak-row" data-tweak-form>
      <span class="tweak-icon" aria-hidden="true">${ICON.spark}</span>
      <input type="text" data-tweak-in placeholder="Ask for a change, e.g. “make it night”" autocomplete="off" enterkeyhint="send" aria-label="Describe a change to the prompt">
      <button class="tweak-go" aria-label="Apply the change">${ICON.arrowUp}</button>
    </form>
    <div class="chips scroll tweak-quick">
      ${undo ? `<button type="button" class="chip" data-tweak-undo>↶ Undo</button>` : ""}
      ${TWEAKS.map((t) => `<button type="button" class="chip" data-tweak-chip>${esc(t)}</button>`).join("")}
    </div>
  </div>`;
}

/* Wire a tweakBox. o: { ctx() -> prompt context, get() -> current prompt, set(text),
   push(previous) and pop() -> previous, for undo }. */
function bindTweak(root, o) {
  if (!root) return;
  const input = $("[data-tweak-in]", root);
  const busy = (on) => {
    root.classList.toggle("busy", on);
    $$("button, input", root).forEach((el) => (el.disabled = on));
    input.placeholder = on ? "Rewriting the prompt…" : "Ask for a change, e.g. “make it night”";
  };
  const go = async (request) => {
    request = request.trim();
    if (!request) return input.focus();
    const before = o.get();
    if (!before.trim()) return toast("Write a prompt first", true);
    busy(true);
    try {
      const r = await api("/api/refine", { body: { ...o.ctx(), prompt: before, request } });
      o.push(before);
      o.set(r.prompt);
      toast(`Updated in ${r.seconds} s`);
    } catch (e) { fail(e); busy(false); }
  };
  $("[data-tweak-form]", root).onsubmit = (e) => { e.preventDefault(); go(input.value); };
  $$("[data-tweak-chip]", root).forEach((b) => b.onclick = () => go(b.textContent));
  const u = $("[data-tweak-undo]", root);
  if (u) u.onclick = () => { const prev = o.pop(); if (prev != null) { o.set(prev); toast("Change undone"); } };
}

// ------------------------------------------------------------------ recipes: effective settings, editor

/* The recipe with this form's Tune values applied, exactly as the server will resolve it. */
function effectiveRecipe() {
  const r = JSON.parse(JSON.stringify(recipeFor(F.mode)));
  const a = F.adv;
  for (const k of ["strength", "steps", "sampler", "scheduler", "ref_image_size"]) if (a[k] !== undefined) r[k] = a[k];
  if (a.shift === "off") delete r.shift; else if (Array.isArray(a.shift)) r.shift = a.shift.slice();
  if (a.extend === "on") r.extend = { steps: 2, start: 0.8, end: 0, spacing: "linear" };
  if (a.extend === "off") delete r.extend;
  return r;
}

const SCHED_DETAIL = { beta: "beta (alpha 0.6, beta 0.6)" };
const REF_SIZE_DETAIL = { max: "max: full detail, short edge up to 2048 px", match: "match: scaled to the clip's pixel area" };

function recipeSummary(r) {
  return [`${r.steps} steps`, `${r.sampler}/${r.scheduler}`, r.lora ? `turbo ${Number(r.strength).toFixed(2)}` : "no turbo",
    r.shift ? `shift ${r.shift.join("/")}` : "default shift", r.extend ? "extra passes" : ""].filter(Boolean).join(" · ");
}

/* Every value a clip is (or was) sampled with. o: {recipe, family, checkpoint, tuned[], size[w,h], frames} */
function settingsRows(o) {
  const r = o.recipe || {};
  const fam = S.boot.recipes[o.family] || { turbo: {} };
  const ck = S.boot.checkpoints.find((c) => c.id === o.checkpoint);
  const ckFile = ck && ck.files ? ck.files[o.family] : null;
  const turbo = r.lora ? fam.turbo[r.lora] : null;
  const passes = Number(r.steps) + (r.extend ? 2 * (r.extend.steps || 2) : 0);
  const tuned = (o.tuned || []).length ? ` <span class="status">tuned: ${esc(o.tuned.join(", "))}</span>` : "";
  const row = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
  return `<dl class="kv cfg">
    ${row("Recipe", `${esc(r.label || r.id || "—")}${tuned}`)}
    ${row("Base model", ck ? `${esc(ck.label)}${ckFile ? `<div class="faint">${esc(nameOf(ckFile))}</div>` : ""}` : esc(o.checkpoint || "stock"))}
    ${row("Turbo LoRA", r.lora ? `${esc(turbo ? turbo.label : r.lora)}${turbo ? `<div class="faint">${esc(nameOf(turbo.file))}</div>` : ""}` : "None (base model)")}
    ${r.lora ? row("Turbo strength", Number(r.strength).toFixed(2)) : ""}
    ${row("Steps", `${r.steps} <span class="faint">(${passes} model passes)</span>`)}
    ${row("Sampler", esc(r.sampler))}
    ${row("Scheduler", esc(SCHED_DETAIL[r.scheduler] || r.scheduler))}
    ${row("Sigma shift", r.shift ? `video ${r.shift[0]} · audio ${r.shift[1]}` : "model default (video 12 · audio 3)")}
    ${row("Extra low-noise passes", r.extend ? `${r.extend.steps} steps, sigma ${r.extend.start} → ${r.extend.end}, ${esc(r.extend.spacing)}` : "off")}
    ${o.family === "ref2v" ? row("Reference size", esc(REF_SIZE_DETAIL[r.ref_image_size] || r.ref_image_size || "max")) : ""}
    ${o.size ? row("Output", `${o.size[0]}×${o.size[1]} · ${o.frames} frames · ${(o.frames / 24).toFixed(2)} s`) : ""}
  </dl>`;
}
function settingsSheet(title, o) {
  openSheet(title, `${settingsRows(o)}<p class="note">These are the values sent to ComfyUI. Every output keeps its own copy (Library → open a clip → Settings used).</p>`);
}

async function loadSamplerLists() {
  if (!samplerLists) { try { samplerLists = await api("/api/choices"); } catch { /* ComfyUI starting */ } }
  return samplerLists || { samplers: [], schedulers: [] };
}

/* Create, view or edit a recipe. A built-in opens read-only with Duplicate. opts.select: use it in Create after saving. */
async function recipeEditor(famId, recipe, opts = {}) {
  const fam = S.boot.recipes[famId];
  const lists = await loadSamplerLists();
  const r = JSON.parse(JSON.stringify(recipe || { ...fam.recipes.find((x) => x.id === fam.default), id: null, builtin: false, label: "" }));
  const ro = !!r.builtin;
  const dis = ro ? "disabled" : "";
  const opt = (list, cur) => [...new Set([cur, ...list].filter(Boolean))].map((o) => `<option ${o === cur ? "selected" : ""}>${esc(o)}</option>`).join("");
  const ext = r.extend || { steps: 2, start: 0.8, end: 0, spacing: "linear" };
  const sh = r.shift || [12, 3];
  openSheet(ro ? r.label : r.id ? `Edit ${r.label}` : "New recipe", `
    ${ro ? `<p class="note" style="margin-top:0">Built-in recipe. Duplicate it to make your own version.</p>` : ""}
    <div class="stack">
      <div><label class="field">Name</label><input type="text" id="reLabel" value="${esc(r.label || "")}" maxlength="40" ${dis}></div>
      <div><label class="field">Note</label><input type="text" id="reNote" value="${esc(r.note || "")}" maxlength="200" placeholder="What it's for" ${dis}></div>
      <div><label class="field">Turbo LoRA</label><select id="reLora" ${dis}>${Object.entries(fam.turbo).map(([id, t]) => `<option value="${esc(id)}" ${r.lora === id ? "selected" : ""}>${esc(t.label)}</option>`).join("")}<option value="none" ${r.lora ? "" : "selected"}>None (base model, use ~20 steps)</option></select></div>
      <div class="grid2">
        <div><label class="field">Turbo strength</label><input type="number" id="reStrength" step="0.05" min="0" max="2" value="${esc(r.strength ?? 1)}" ${dis}></div>
        <div><label class="field">Steps</label><input type="number" id="reSteps" step="1" min="1" max="60" value="${esc(r.steps ?? 8)}" ${dis}></div>
        <div><label class="field">Sampler</label><select id="reSampler" ${dis}>${opt(lists.samplers, r.sampler || "euler")}</select></div>
        <div><label class="field">Scheduler</label><select id="reScheduler" ${dis}>${opt(lists.schedulers, r.scheduler || "simple")}</select></div>
        <div><label class="field">Sigma shift</label><select id="reShiftMode" ${dis}><option value="default" ${r.shift ? "" : "selected"}>Default 12 / 3</option><option value="custom" ${r.shift ? "selected" : ""}>Custom</option></select></div>
        <div class="re-shift"><label class="field">Video / audio</label><div class="row"><input type="number" id="reShiftV" step="0.5" value="${esc(sh[0])}" ${dis}><input type="number" id="reShiftA" step="0.5" value="${esc(sh[1])}" ${dis}></div></div>
        <div><label class="field">Extra low-noise passes</label><select id="reExtMode" ${dis}><option value="off" ${r.extend ? "" : "selected"}>Off</option><option value="on" ${r.extend ? "selected" : ""}>On</option></select></div>
        ${famId === "ref2v" ? `<div><label class="field">Reference size</label><select id="reRefSize" ${dis}>${["max", "match"].map((o) => `<option ${o === (r.ref_image_size || "max") ? "selected" : ""}>${o}</option>`).join("")}</select></div>` : "<div></div>"}
        <div class="re-ext"><label class="field">Pass steps</label><input type="number" id="reExtSteps" step="1" min="1" max="8" value="${esc(ext.steps)}" ${dis}></div>
        <div class="re-ext"><label class="field">Sigma from → to</label><div class="row"><input type="number" id="reExtStart" step="0.05" min="0" max="1" value="${esc(ext.start)}" ${dis}><input type="number" id="reExtEnd" step="0.05" min="0" max="1" value="${esc(ext.end)}" ${dis}></div></div>
      </div>
    </div>
    <div class="sheet-foot">
      ${ro ? `<button class="btn primary" id="reDup">Duplicate</button>`
        : `${r.id ? `<button class="btn danger" id="reDel" aria-label="Delete recipe">${ICON.trash}</button>` : ""}<button class="btn primary" id="reSave">Save recipe</button>`}
    </div>`, (body) => {
    const sync = () => {
      $$(".re-shift", body).forEach((el) => (el.hidden = $("#reShiftMode", body).value !== "custom"));
      $$(".re-ext", body).forEach((el) => (el.hidden = $("#reExtMode", body).value !== "on"));
    };
    sync();
    ["#reShiftMode", "#reExtMode"].forEach((id) => ($(id, body).onchange = sync));
    const dup = $("#reDup", body);
    if (dup) dup.onclick = () => recipeEditor(famId, { ...r, id: null, builtin: false, label: `${r.label} copy`.slice(0, 40) }, opts);
    const del = $("#reDel", body);
    if (del) del.onclick = async () => {
      if (!confirm(`Delete the recipe "${r.label}"?`)) return;
      try { await api(`/api/recipes/${famId}/${encodeURIComponent(r.id)}`, { method: "DELETE" }); S.boot.recipes = await api("/api/recipes"); toast("Deleted"); closeSheet(); render(); } catch (e) { fail(e); }
    };
    const save = $("#reSave", body);
    if (save) save.onclick = async () => {
      const num = (id) => Number($(id, body).value);
      const payload = {
        id: r.id || null, label: $("#reLabel", body).value, note: $("#reNote", body).value,
        lora: $("#reLora", body).value, strength: num("#reStrength"), steps: num("#reSteps"),
        sampler: $("#reSampler", body).value, scheduler: $("#reScheduler", body).value,
        shift: $("#reShiftMode", body).value === "custom" ? [num("#reShiftV"), num("#reShiftA")] : "default",
        extend: $("#reExtMode", body).value === "on" ? { steps: num("#reExtSteps"), start: num("#reExtStart"), end: num("#reExtEnd"), spacing: "linear" } : "off",
        ref_image_size: famId === "ref2v" ? $("#reRefSize", body).value : undefined,
      };
      try {
        const saved = await api(`/api/recipes/${famId}`, { body: payload });
        S.boot.recipes = await api("/api/recipes");
        if (opts.select) { F.recipe[famId] = saved.id; F.adv = {}; saveForm(); }
        toast(opts.select ? `Saved and selected ${saved.label}` : "Recipe saved");
        closeSheet(); render();
      } catch (e) { fail(e); }
    };
  });
}

function recipePasses(r) {
  const steps = Number(F.adv.steps ?? r.steps);
  const extend = F.adv.extend === "on" || (F.adv.extend !== "off" && r.extend);
  return steps + (extend ? 2 * ((r.extend && r.extend.steps) || 2) : 0);
}

function frameSlot(key, label) {
  const f = F[key];
  if (!f) return `<div class="frame-slot" data-slot="${key}">${ICON.image}<br>${label}</div>`;
  return `<div class="frame-slot filled" data-slot="${key}"><img src="${thumbIn(f)}" alt=""><button class="x" data-clear="${key}" aria-label="Remove">${ICON.x}</button><span class="cap">${label}</span></div>`;
}

function refCounts() {
  const c = { image: 0, video: 0, audio: 0 }; F.refs.forEach((r) => c[r.kind]++);
  const L = S.boot.limits.refs;
  return `${c.image}/${L.image} pictures · ${c.video}/${L.video} clips · ${c.audio}/${L.audio} audio`;
}
function refTags() {
  const tags = F.refs.map(() => ""); let p = 0, v = 0, a = 0;
  F.refs.forEach((r, i) => { if (r.kind === "image") tags[i] = `<Picture ${++p}>`; });
  F.refs.forEach((r, i) => { if (r.kind === "video") { tags[i] = `<Video ${++v}>`; if (r.use_soundtrack !== false) tags[i] += ` <Audio ${++a}>`; } });
  F.refs.forEach((r, i) => { if (r.kind === "audio") tags[i] = `<Audio ${++a}>`; });
  return tags;
}
function refTiles() {
  const tags = refTags();
  const tiles = F.refs.map((r, i) => `
    <div class="ref">
      ${r.kind === "audio" ? `<span class="kind">${ICON.audio}</span>` : `<img src="${thumbIn(r.file)}" alt="" loading="lazy">`}
      ${r.kind === "video" ? `<button class="snd ${r.use_soundtrack === false ? "off" : ""}" data-snd="${i}" title="Use this clip's sound">♪</button>` : ""}
      <button class="x" data-rmref="${i}" aria-label="Remove">${ICON.x}</button>
      <span class="tag">${esc(tags[i])}</span>
    </div>`).join("");
  return tiles + `<button class="ref add" id="addRef">${ICON.plus}<br>Add</button>`;
}

function advancedBlock(r) {
  const a = F.adv; const any = Object.keys(a).length > 0;
  const shiftMode = a.shift === undefined ? "recipe" : a.shift === "off" ? "off" : "custom";
  const sv = Array.isArray(a.shift) ? a.shift : r.shift || [12, 3];
  return `<details class="more" ${F.advOpen ? "open" : ""} id="advBox">
    <summary>${ICON.chev} Tune this recipe${any ? ` <span class="status">changed</span>` : ""}</summary>
    <div class="grid2">
      <div><label class="field">Turbo strength</label><input type="number" step="0.05" min="0" max="2" data-adv="strength" value="${esc(a.strength ?? r.strength)}"></div>
      <div><label class="field">Steps</label><input type="number" step="1" min="1" max="60" data-adv="steps" value="${esc(a.steps ?? r.steps)}"></div>
      <div><label class="field">Sampler</label><select data-adv="sampler" id="advSampler"><option>${esc(a.sampler ?? r.sampler)}</option></select></div>
      <div><label class="field">Scheduler</label><select data-adv="scheduler" id="advScheduler"><option>${esc(a.scheduler ?? r.scheduler)}</option></select></div>
      <div><label class="field">Sigma shift</label><select id="advShiftMode"><option value="recipe" ${shiftMode === "recipe" ? "selected" : ""}>Recipe (${r.shift ? r.shift.join(" / ") : "default 12 / 3"})</option><option value="custom" ${shiftMode === "custom" ? "selected" : ""}>Custom</option><option value="off" ${shiftMode === "off" ? "selected" : ""}>ComfyUI default</option></select></div>
      <div><label class="field">Extra low-noise passes</label><select data-adv="extend"><option value="" ${a.extend === undefined ? "selected" : ""}>Recipe (${r.extend ? "on" : "off"})</option><option value="on" ${a.extend === "on" ? "selected" : ""}>On</option><option value="off" ${a.extend === "off" ? "selected" : ""}>Off</option></select></div>
      ${shiftMode === "custom" ? `<div><label class="field">Shift video</label><input type="number" step="0.5" id="shiftV" value="${esc(sv[0])}"></div><div><label class="field">Shift audio</label><input type="number" step="0.5" id="shiftA" value="${esc(sv[1])}"></div>` : ""}
      ${FAMILY[F.mode] === "ref2v" ? `<div><label class="field">Reference size</label><select data-adv="ref_image_size">${["max", "match"].map((o) => `<option ${o === (a.ref_image_size ?? r.ref_image_size) ? "selected" : ""}>${o}</option>`).join("")}</select></div>` : ""}
    </div>
    <div class="row" style="margin-top:10px"><span class="grow note">Saved with every output, so you can compare later.</span><button class="btn small ghost" id="advSaveRecipe" title="Save these values as your own recipe">Save as recipe</button><button class="btn small ghost" id="advReset" ${any ? "" : "disabled"}>Reset</button></div>
  </details>`;
}

function loraParts(l, fam) {
  const act = (l.active || {})[fam] || [];
  return act.length > 1 ? act : [];
}

function loraChips() {
  const fam = FAMILY[F.mode];
  return F.loras.map((l, i) => {
    const fams = l.families || ["fl2v", "ref2v"];
    const warn = !fams.includes(fam) ? `<div class="warn">No ${fam === "ref2v" ? "R2V" : "T2V/I2V"} file. Skipped for this mode.</div>` : "";
    const parts = loraParts(l, fam);
    if (parts.length) {
      const rows = parts.map((p, j) => {
        const val = (l.parts || {})[p.name] ?? Number(l.strength) * p.scale;
        return `<div class="lora-part"><span class="faint">${p.role === "helper" ? "Helper" : "Main"}</span>
          <input type="range" min="0" max="2" step="0.05" value="${val}" data-lpart="${i}" data-lname="${esc(p.name)}" aria-label="${p.role} strength">
          <span class="val num" id="lpv${i}_${j}">${Number(val).toFixed(2)}</span></div>`;
      }).join("");
      return `<div class="lora-chip multi"><div class="row"><div class="grow" style="min-width:0"><div class="name">${esc(l.nickname || l.key)}</div>${warn}</div>
        <button class="icon-btn" data-lrm="${i}" aria-label="Remove">${ICON.x}</button></div>${rows}</div>`;
    }
    return `<div class="lora-chip"><div class="grow" style="min-width:0"><div class="name">${esc(l.nickname || l.key)}</div>${warn}</div>
      <input type="range" min="0" max="2" step="0.05" value="${l.strength}" data-lstr="${i}" aria-label="Strength">
      <span class="val num" id="lval${i}">${Number(l.strength).toFixed(2)}</span>
      <button class="icon-btn" data-lrm="${i}" aria-label="Remove">${ICON.x}</button></div>`;
  }).join("");
}

function bindCreate() {
  const v = $("#view");
  $$("[data-mode]", v).forEach((b) => b.onclick = () => { F.mode = b.dataset.mode; F.adv = {}; saveForm(); render(); });
  $$("[data-auto]", v).forEach((b) => b.onclick = () => { F.auto = b.dataset.auto === "1"; saveForm(); render(); });
  const pb = $("#promptBox");
  pb.oninput = () => {
    if (F.auto) F.idea = pb.value; else F.prompt = pb.value;
    saveForm();
    const tw = $("#tweak", v); if (tw && !F.auto) tw.hidden = !F.prompt.trim();
  };
  pb.onblur = () => { if (F.auto && F.drafted && F.drafted.idea !== F.idea) render(); };
  const toAuto = $("#toAuto"); if (toAuto) toAuto.onclick = () => { F.auto = true; saveForm(); render(); };
  const draftBtn = $("#draftBtn");
  if (draftBtn) draftBtn.onclick = async () => {
    if (!F.idea.trim()) return toast("Write an idea first", true);
    draftBtn.disabled = true; draftBtn.innerHTML = `${ICON.spark} Writing…`;
    try {
      const r = await api("/api/draft", { body: promptContext() });
      F.drafted = { mode: F.mode, idea: F.idea, prompt: r.prompt }; F.tweakUndo = []; saveForm(); render();
      toast(`Prompt written in ${r.seconds} s`);
    } catch (e) { fail(e); render(); }
  };
  const db = $("#draftBox"); if (db) db.oninput = () => { F.drafted.prompt = db.value; saveForm(); };
  const dd = $("#dropDraft"); if (dd) dd.onclick = () => { F.drafted = null; F.tweakUndo = []; saveForm(); render(); };
  bindTweak($("#tweak", v), {
    ctx: promptContext,
    get: () => (F.auto ? F.drafted.prompt : F.prompt),
    set: (text) => { if (F.auto) F.drafted.prompt = text; else F.prompt = text; saveForm(); render(); },
    push: (text) => { F.tweakUndo = [...(F.tweakUndo || []).filter((u) => u.auto === F.auto).slice(-9), { auto: F.auto, text }]; },
    pop: () => { const list = F.tweakUndo || []; const i = list.map((u) => u.auto).lastIndexOf(F.auto); return i < 0 ? null : list.splice(i, 1)[0].text; },
  });

  // frames
  $$("[data-slot]", v).forEach((el) => el.onclick = async (e) => {
    const key = el.dataset.slot;
    if (e.target.closest("[data-clear]")) { F[key] = null; saveForm(); render(); return; }
    const [a] = await pickAsset({ title: key === "start_image" ? "Start frame" : "End frame", kinds: ["image"] });
    if (a) { F[key] = a.file; saveForm(); render(); }
  });

  // references
  const addRef = $("#addRef");
  if (addRef) addRef.onclick = async () => {
    const L = S.boot.limits.refs; const room = (L.image + L.video + L.audio) - F.refs.length;
    const picked = await pickAsset({ title: "Add references", kinds: ["image", "video", "audio"], max: room });
    for (const a of picked) await addReference(a);
    render();
  };
  $$("[data-rmref]", v).forEach((b) => b.onclick = () => { F.refs.splice(+b.dataset.rmref, 1); saveForm(); render(); });
  $$("[data-snd]", v).forEach((b) => b.onclick = () => { const r = F.refs[+b.dataset.snd]; r.use_soundtrack = r.use_soundtrack === false; saveForm(); render(); });
  const kl = $("#kitLoad"); if (kl) kl.onclick = openKitPicker;
  const ks = $("#kitSave"); if (ks) ks.onclick = () => saveKitSheet(F.refs);
  const rc = $("#refsClear"); if (rc) rc.onclick = () => { F.refs = []; saveForm(); render(); };

  // shape
  $$("[data-aspect]", v).forEach((b) => b.onclick = () => { F.aspect = b.dataset.aspect; saveForm(); render(); });
  const mp = $("#mp", v);
  if (mp) {
    mp.oninput = () => { F.megapixels = Number(mp.value); saveForm(); const lab = mp.previousElementSibling; lab.innerHTML = `Quality <span class="num">${F.megapixels.toFixed(2)} MP</span> <span class="faint">${qualityName(F.megapixels)}</span>`; const [w, h] = resolution(F.aspect, F.megapixels); const hint = $(".block-head .hint.num", v); if (hint) hint.textContent = `${w}×${h} · ${(frames(F.duration) / 24).toFixed(2)} s · ${frames(F.duration)} frames`; };
  }
  const dur = $("#dur"); dur.oninput = () => { F.duration = +dur.value; $("label[for=dur] .num").textContent = `${F.duration} s`; };
  dur.onchange = () => { saveForm(); render(); };

  // recipes
  $$("[data-recipe]", v).forEach((b) => b.onclick = () => { F.recipe[FAMILY[F.mode]] = b.dataset.recipe; F.adv = {}; saveForm(); render(); });
  const adv = $("#advBox"); adv.ontoggle = () => { F.advOpen = adv.open; saveForm(); if (adv.open) fillSamplerLists(); };
  if (adv.open) fillSamplerLists();
  $$("[data-adv]", v).forEach((el) => el.onchange = () => {
    const k = el.dataset.adv; const r = recipeFor(F.mode);
    let val = el.value;
    if (k === "strength" || k === "steps") val = Number(val);
    if (val === "" || val === r[k]) delete F.adv[k]; else F.adv[k] = val;
    saveForm(); render();
  });
  const sm = $("#advShiftMode");
  sm.onchange = () => { if (sm.value === "recipe") delete F.adv.shift; else if (sm.value === "off") F.adv.shift = "off"; else F.adv.shift = (recipeFor(F.mode).shift || [12, 3]).slice(); saveForm(); render(); };
  ["shiftV", "shiftA"].forEach((id, i) => { const el = $("#" + id); if (el) el.onchange = () => { F.adv.shift[i] = Number(el.value); saveForm(); }; });
  $("#advReset").onclick = () => { F.adv = {}; saveForm(); render(); };
  $("#advSaveRecipe").onclick = () => recipeEditor(FAMILY[F.mode], { ...effectiveRecipe(), id: null, builtin: false, label: `${recipeFor(F.mode).label} (tuned)` }, { select: true });
  $("#fullCfg").onclick = () => settingsSheet("Full settings", {
    recipe: effectiveRecipe(), family: FAMILY[F.mode], checkpoint: F.checkpoint, tuned: Object.keys(F.adv),
    size: resolution(F.aspect, F.megapixels), frames: frames(F.duration),
  });

  // loras
  $("#addLora").onclick = openLoraPicker;
  $$("[data-lpart]", v).forEach((el) => el.oninput = () => {
    const l = F.loras[+el.dataset.lpart]; l.parts = l.parts || {}; l.parts[el.dataset.lname] = Number(el.value);
    el.nextElementSibling.textContent = Number(el.value).toFixed(2); saveForm();
  });
  $$("[data-lstr]", v).forEach((el) => el.oninput = () => { const i = +el.dataset.lstr; F.loras[i].strength = Number(el.value); $("#lval" + i).textContent = Number(el.value).toFixed(2); saveForm(); });
  $$("[data-lrm]", v).forEach((b) => b.onclick = () => { F.loras.splice(+b.dataset.lrm, 1); saveForm(); render(); });

  // more
  const more = $("#moreBox"); more.ontoggle = () => { F.moreOpen = more.open; saveForm(); };
  const seed = $("#seed"); seed.onchange = () => { F.seed = seed.value; saveForm(); };
  const lock = $("#lockSeed"); lock.onchange = () => { F.lockSeed = lock.checked; if (lock.checked && !F.seed) F.seed = String(Math.floor(Math.random() * 1e12)); saveForm(); render(); };
  $("#ck").onchange = (e) => { F.checkpoint = e.target.value; saveForm(); render(); };
  $$("[data-count]", v).forEach((b) => b.onclick = () => { F.count = +b.dataset.count; saveForm(); render(); });

  $("#genBtn").onclick = () => submit(false);
  const cmp = $("#compareBtn"); if (cmp) cmp.onclick = () => submit(true);
}

let samplerLists = null;
async function fillSamplerLists() {
  try {
    if (!samplerLists) samplerLists = await api("/api/choices");
    const r = recipeFor(F.mode);
    const fill = (id, list, cur) => { const el = $("#" + id); if (!el) return; el.innerHTML = list.map((o) => `<option ${o === cur ? "selected" : ""}>${esc(o)}</option>`).join(""); };
    fill("advSampler", samplerLists.samplers, F.adv.sampler ?? r.sampler);
    fill("advScheduler", samplerLists.schedulers, F.adv.scheduler ?? r.scheduler);
  } catch { /* ComfyUI still starting: keep the recipe value only */ }
}

async function addReference(a) {
  const L = S.boot.limits.refs;
  if (F.refs.filter((r) => r.kind === a.kind).length >= L[a.kind]) { toast(`At most ${L[a.kind]} ${a.kind} references`, true); return; }
  const ref = { kind: a.kind, file: a.file };
  if (a.kind === "video") {
    let has = a.has_audio;
    if (has === undefined || has === null) { try { has = (await api(`/api/assets/soundtrack?file=${encodeURIComponent(a.file)}`)).has_audio; } catch { has = true; } }
    ref.use_soundtrack = !!has;
  }
  F.refs.push(ref); saveForm();
}

function overridesPayload() {
  const o = {}; const a = F.adv;
  if (a.ref_image_size === "original") delete a.ref_image_size;  // retired option; ComfyUI never accepted it
  for (const k of ["strength", "steps", "sampler", "scheduler", "ref_image_size"]) if (a[k] !== undefined) o[k] = a[k];
  if (a.shift !== undefined) o.shift = a.shift;
  if (a.extend === "on") o.extend = { steps: 2, start: 0.8, end: 0, spacing: "linear" };
  if (a.extend === "off") o.extend = "off";
  return o;
}

async function submit(compare) {
  const fam = FAMILY[F.mode];
  const body = {
    mode: F.mode, aspect: F.aspect, megapixels: F.megapixels, duration: F.duration, checkpoint: F.checkpoint,
    recipe_id: recipeFor(F.mode).id, overrides: overridesPayload(), compare, count: F.count,
    seed: F.lockSeed && F.seed !== "" ? Number(F.seed) : null,
    loras: F.loras.filter((l) => (l.families || ["fl2v", "ref2v"]).includes(fam)).map((l) => ({ key: l.key, file: l.file, strength: l.strength, parts: l.parts, nickname: l.nickname })),
  };
  if (F.mode === "i2v") { body.start_image = F.start_image; body.end_image = F.end_image; }
  if (F.mode === "r2v") body.refs = F.refs;
  if (F.auto) {
    body.prompt_mode = "auto"; body.idea = F.idea;
    if (F.drafted && F.drafted.mode === F.mode && F.drafted.idea === F.idea) body.prompt = F.drafted.prompt;
  } else body.prompt = F.prompt;
  const btn = compare ? $("#compareBtn") : $("#genBtn"); btn.disabled = true;
  try {
    const r = await api("/api/generate", { body });
    toast(r.jobs.length > 1 ? `Queued ${r.jobs.length} clips` : "Queued");
    pollSoon();
  } catch (e) { fail(e); } finally { btn.disabled = false; }
}

// LoRA picker (catalog, filtered to what's installed)
async function openLoraPicker() {
  openSheet("Add a LoRA", `<div class="empty">Loading…</div>`, async (body) => {
    try {
      const data = await loadLoras(true);
      const fam = FAMILY[F.mode];
      const items = data.items.filter((l) => l.installed && l.enabled !== false);
      const draw = (q = "") => {
        const list = items.filter((l) => loraMatches(l, q));
        const quick = S.loras.items.filter((l) => isQuick(l) && loraMatches(l, q));
        body.innerHTML = `<input type="search" id="lq" placeholder="Search your LoRAs" value="${esc(q)}">
          <div style="margin-top:8px">${list.map((l) => `
            <button class="lora item" data-key="${esc(l.key)}" style="width:100%;background:none;border:0;border-bottom:1px solid var(--line);text-align:left;color:inherit;cursor:pointer">
              <div class="lora-img">${l.image ? `<img src="${esc(l.image)}" alt="" loading="lazy">` : ICON.cube}</div>
              <div class="grow"><h3>${esc(l.nickname || l.key)}</h3>
                <div>${(l.families || []).map((f) => `<span class="fam ${f}">${f === "ref2v" ? "R2V" : "T2V/I2V"}</span>`).join("")}${(l.files || []).some((f) => f.role === "helper" && f.installed) ? `<span class="fam helper">+ helper</span>` : ""}</div>
                <div class="faint" style="font-size:12px">${esc((l.trigger_words || []).slice(0, 4).join(", "))}</div>
              </div>
              <span class="faint" style="font-size:12px">${(l.families || []).includes(fam) ? "" : "other mode"}</span>
            </button>`).join("") || `<div class="empty"><h2>No LoRAs installed</h2><p>Browse CivitAI in More → LoRAs.</p></div>`}</div>
          ${quick.length ? `<div class="section-title"><h2>Quick install</h2><span class="faint" style="font-size:12px">tap Get, then add it once it's downloaded</span></div>
            <div class="list quick">${quick.map(quickRow).join("")}</div>` : ""}`;
        const lq = $("#lq", body); lq.oninput = () => { const pos = lq.selectionStart; draw(lq.value); const n = $("#lq", body); n.focus(); n.setSelectionRange(pos, pos); };
        $$("[data-get]", body).forEach((b) => b.onclick = async () => {
          b.disabled = true;
          try { await api(`/api/loras/${encodeURIComponent(b.dataset.get)}/install`, { body: {} }); b.outerHTML = `<span class="faint" style="font-size:13px">Downloading…</span>`; }
          catch (e) { fail(e); b.disabled = false; }
        });
        $$("[data-key]", body).forEach((b) => b.onclick = () => {
          const l = items.find((x) => x.key === b.dataset.key);
          if (F.loras.some((x) => x.key === l.key)) return toast("Already added");
          F.loras.push({ key: l.key, file: l.untracked ? l.filename : undefined, nickname: l.nickname || l.key, strength: Number(l.recommended_strength || 1), families: l.families, active: l.active });
          saveForm(); closeSheet(); render();
          if ((l.trigger_words || []).length) toast(`Triggers: ${l.trigger_words.slice(0, 3).join(", ")}`);
        });
      };
      draw();
    } catch (e) { fail(e); }
  });
}
async function loadLoras(force = false) { if (!S.loras || force) S.loras = await api("/api/loras"); return S.loras; }

// kits
async function openKitPicker() {
  await loadAssets(true);
  openSheet("Load a kit", kitList(S.kits, true), (body) => {
    $$("[data-kit]", body).forEach((b) => b.onclick = () => {
      const k = S.kits.find((x) => x.id === b.dataset.kit);
      F.refs = k.refs.map((r) => ({ ...r })); saveForm(); closeSheet(); if (S.route !== "create") location.hash = "#create"; else render();
      toast(`Loaded ${k.name}`);
    });
  });
}
function kitList(kits, pick) {
  if (!kits.length) return `<div class="empty"><h2>No kits yet</h2><p>Add references in Create, then Save as kit. Kits are reusable reference sets.</p></div>`;
  return `<div class="kits">${kits.map((k) => `
    <button class="kit" data-kit="${esc(k.id)}" style="text-align:left;color:inherit;cursor:pointer">
      <div class="kit-strip">${k.refs.slice(0, 4).map((r) => r.kind === "audio" ? `<span>${ICON.audio}</span>` : `<img src="${thumbIn(r.file)}" alt="">`).join("")}</div>
      <div class="grow"><h3>${esc(k.name)}</h3><div class="faint" style="font-size:12px">${k.refs.length} reference${k.refs.length === 1 ? "" : "s"}</div></div>
      ${pick ? "" : ICON.chev}
    </button>`).join("")}</div>`;
}
function saveKitSheet(refs, kit) {
  openSheet(kit ? "Edit kit" : "Save as kit", `
    <label class="field" for="kitName">Name</label>
    <input type="text" id="kitName" value="${esc(kit ? kit.name : "")}" placeholder="e.g. Maya, laundromat, voice">
    <p class="note">${refs.length} reference${refs.length === 1 ? "" : "s"} · the files stay where they are.</p>
    <div class="sheet-foot"><button class="btn primary" id="kitGo">Save kit</button></div>`, (body) => {
    $("#kitName", body).focus();
    $("#kitGo", body).onclick = async () => {
      try { await api("/api/kits", { body: { id: kit && kit.id, name: $("#kitName", body).value, refs } }); S.assets = null; toast("Kit saved"); closeSheet(); if (S.route === "refs") render(); }
      catch (e) { fail(e); }
    };
  });
}

// ------------------------------------------------------------------ QUEUE

function jobTitle(j) {
  let src = j.prompt_mode === "auto" && j.idea ? j.idea : j.prompt || j.idea || "";
  const shot = src.search(/\[Shot 1\]/i);
  if (shot >= 0) src = src.slice(shot);
  // Show the story, not MiniMax's field labels: drop "field_name:" prefixes and [Shot N] markers.
  const line = src.split("\n").map((l) => l.replace(/^\s*[a-z_]+:\s*/i, "").replace(/\[Shot \d+\]\s*/gi, "").replace(/^For the target video,[^.]*\.\s*/i, "").trim()).find(Boolean);
  return line || MODES[j.mode] || "Clip";
}
function jobLabel(j) { return `${MODES[j.mode]} · ${esc(j.label || j.recipe_id)} · ${j.duration}s${j.group ? " · group" : ""}`; }

VIEWS.queue = function () {
  const L = S.live;
  if (!L) { $("#view").innerHTML = `<div class="page"><div class="empty">Loading…</div></div>`; return; }
  const running = L.pending.find((j) => ["running", "unloading", "drafting", "waiting"].includes(j.status));
  const waiting = L.pending.filter((j) => j !== running);
  $("#view").innerHTML = `
  <div class="page">
    <div class="page-head"><h1>Queue</h1><span class="spacer"></span>
      <button class="btn small" id="pauseBtn">${L.paused ? `${ICON.play} Resume` : `${ICON.pause} Pause`}</button></div>
    ${L.paused ? `<p class="note warn">Paused. Queued jobs wait until you resume.</p>` : ""}
    ${running ? nowCard(running) : `<div class="now"><div class="now-media"><div class="placeholder">${L.pending.length ? "Starting…" : "Nothing rendering. Queue something from Create."}</div></div></div>`}
    ${waiting.length ? `<div class="section-title"><h2>Up next</h2><span class="faint">${waiting.length}</span></div>
      <ul class="jobs">${waiting.map((j, i) => jobRow(j, i, waiting.length)).join("")}</ul>` : ""}
    ${L.finished.length ? `<div class="section-title"><h2>Finished</h2><button class="btn small ghost" id="clearBtn">Clear</button></div>
      <ul class="jobs">${L.finished.map((j) => jobRow(j)).join("")}</ul>` : ""}
  </div>`;
  $("#pauseBtn").onclick = () => api("/api/queue/pause", { body: { paused: !L.paused } }).then(pollSoon).catch(fail);
  const cb = $("#clearBtn"); if (cb) cb.onclick = () => api("/api/queue/clear", { body: {} }).then(pollSoon).catch(fail);
  $$("[data-job]").forEach((el) => el.onclick = (e) => {
    const j = [...L.pending, ...L.finished].find((x) => x.id === el.dataset.job);
    if (e.target.closest("[data-mv]")) { const d = +e.target.closest("[data-mv]").dataset.mv; api(`/api/jobs/${j.id}/move`, { body: { direction: d } }).then(pollSoon).catch(fail); return; }
    jobSheet(j);
  });
  const cancelNow = $("#cancelNow"); if (cancelNow) cancelNow.onclick = () => api(`/api/jobs/${running.id}/cancel`, { body: {} }).then(() => toast("Cancelling…")).catch(fail);
  const nowPrompt = $("#nowPrompt"); if (nowPrompt) nowPrompt.onclick = () => jobSheet(running);
};

function progressOf(j) {
  if (j.status !== "running") return 0;
  if (j.stage === "sampling" && j.total_steps) return 0.08 + 0.8 * (j.step / j.total_steps);
  if (/decod|saving/.test(j.stage || "")) return 0.92;
  return 0.04;
}
function nowCard(j) {
  const pct = Math.round(progressOf(j) * 100);
  const hasPreview = j.preview_seq > 0;
  const stage = j.status === "running" ? (j.stage === "sampling" && j.total_steps ? (j.step ? `Sampling · step ${j.step} of ${j.total_steps}` : "Starting to sample") : cap(j.stage || "starting")) : cap(j.stage || j.status);
  const eta = j.eta != null ? clock(j.eta) : j.started ? clock(Date.now() / 1000 - j.started) : "";
  return `<div class="now">
    <div class="now-media">
      ${hasPreview ? `<img id="nowImg" src="/api/jobs/${j.id}/preview?s=${j.preview_seq}" alt="Live preview">` : `<div class="placeholder">${esc(stage)}…</div>`}
      <span class="now-rec"><span class="tally-dot"></span>${j.status === "running" ? "Rendering" : cap(j.status)}</span>
    </div>
    <div class="now-body">
      <div class="now-stage"><span class="what">${esc(stage)}</span><span class="eta" title="${j.eta != null ? "Estimated time left" : "Elapsed"}">${eta}</span></div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      ${j.note ? `<p class="note">${esc(j.note)}</p>` : ""}
      <button class="now-prompt" id="nowPrompt" style="background:none;border:0;padding:0;text-align:left;cursor:pointer">${esc(jobTitle(j))}</button>
      <div class="row" style="margin-top:12px"><span class="grow faint" style="font-size:12px">${jobLabel(j)} · seed ${j.seed}</span><button class="btn small danger" id="cancelNow">Cancel</button></div>
    </div></div>`;
}
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");
function jobRow(j, i, n) {
  const thumb = j.output ? `<img src="${thumbOut(j.output.file)}" alt="" loading="lazy">` : j.mode.toUpperCase();
  const sub = j.status === "failed" ? `<div class="job-sub err">${esc(j.error)}</div>`
    : `<div class="job-sub">${jobLabel(j)}${j.timings && j.timings.total_s ? ` · took ${clock(j.timings.total_s)}` : ""}${j.finished ? ` · ${ago(j.finished)}` : ""}</div>`;
  const mv = j.status === "queued" && n > 1 ? `<button class="icon-btn" data-mv="-1" aria-label="Move up" ${i === 0 ? "disabled" : ""}>${ICON.arrowUp}</button>` : "";
  return `<li class="job" data-job="${j.id}"><div class="job-thumb">${thumb}</div>
    <div class="job-main"><div class="job-title">${esc(jobTitle(j))}</div>${sub}</div>
    ${mv}<span class="status ${j.status}">${j.status === "queued" && !j.prompt && j.prompt_mode === "auto" ? "auto" : j.status}</span></li>`;
}
function jobSheet(j) {
  const pending = ["queued", "waiting", "drafting", "unloading", "running"].includes(j.status);
  openSheet(jobTitle(j).slice(0, 60), `
    ${j.output ? `<div class="player"><video src="${mediaOut(j.output.file)}" controls playsinline loop autoplay></video></div>` : ""}
    <dl class="kv">
      <dt>Status</dt><dd>${esc(j.status)}${j.error ? ` · ${esc(j.error)}` : ""}</dd>
      <dt>Mode</dt><dd>${jobLabel(j)}</dd><dt>Seed</dt><dd class="num">${j.seed}</dd>
      <dt>Shape</dt><dd>${j.aspect} · ${j.megapixels} MP</dd>
      ${j.loras && j.loras.length ? `<dt>LoRAs</dt><dd>${j.loras.map((l) => `${esc(l.nickname || l.file)} ${l.strength}`).join("<br>")}</dd>` : ""}
    </dl>
    ${j.recipe ? `<details class="more cfg-box"><summary>${ICON.chev} Settings ${pending ? "it will use" : "used"}</summary>${settingsRows({
      recipe: { ...j.recipe, label: j.label || j.recipe.label }, family: j.recipe.family || FAMILY[j.mode], checkpoint: j.checkpoint,
      tuned: Object.keys(j.overrides || {}), size: resolution(j.aspect, j.megapixels), frames: frames(j.duration) })}</details>` : ""}
    ${j.idea ? `<h3>Idea</h3><div class="prompt-text">${esc(j.idea)}</div>` : ""}
    <h3 style="margin-top:12px">Prompt</h3>
    ${j.status === "queued" ? `<textarea id="jp" style="min-height:200px">${esc(j.prompt || "")}</textarea><div id="jTweak">${tweakBox({ hidden: !j.prompt, canUndo: false })}</div><p class="note">${j.prompt ? "You can still edit it, or ask for a change." : "Will be written when it's this job's turn (or sooner, in the background)."}</p>`
      : `<div class="prompt-text">${esc(j.prompt || "—")}</div>`}
    <div class="sheet-foot">
      ${j.status === "queued" ? `<button class="btn" id="jSave">Save prompt</button>` : ""}
      ${pending ? `<button class="btn danger" id="jCancel">Cancel</button>` : `<button class="btn" id="jRetry">${ICON.reuse} Run again</button><button class="btn" id="jEdit">Edit in Create</button>`}
    </div>`, (body) => {
    if (j.output) markSeen(j.output.file);
    const s = $("#jSave", body); if (s) s.onclick = () => api(`/api/jobs/${j.id}/prompt`, { body: { prompt: $("#jp", body).value } }).then(() => { toast("Saved"); closeSheet(); pollSoon(); }).catch(fail);
    const jp = $("#jp", body);
    if (jp) {
      const stack = [];
      const mount = () => {
        const holder = $("#jTweak", body);
        holder.innerHTML = tweakBox({ hidden: !jp.value.trim(), canUndo: stack.length > 0 });
        bindTweak($("#tweak", holder), {
          ctx: () => ({ mode: j.mode, idea: j.idea, aspect: j.aspect, megapixels: j.megapixels, duration: j.duration,
            start_image: j.start_image, end_image: j.end_image, refs: j.refs }),
          get: () => jp.value,
          set: (text) => { jp.value = text; j.prompt = text; mount(); api(`/api/jobs/${j.id}/prompt`, { body: { prompt: text } }).then(pollSoon).catch(fail); },
          push: (prev) => stack.push(prev),
          pop: () => (stack.length ? stack.pop() : null),
        });
      };
      mount();
      jp.oninput = () => { const tw = $("#tweak", body); if (tw) tw.hidden = !jp.value.trim(); };
    }
    const c = $("#jCancel", body); if (c) c.onclick = () => api(`/api/jobs/${j.id}/cancel`, { body: {} }).then(() => { closeSheet(); pollSoon(); }).catch(fail);
    const r = $("#jRetry", body); if (r) r.onclick = () => api(`/api/jobs/${j.id}/retry`, { body: {} }).then(() => { toast("Queued again"); closeSheet(); pollSoon(); }).catch(fail);
    const ed = $("#jEdit", body); if (ed) ed.onclick = () => { applyRecord(jobToRecord(j)); closeSheet(); location.hash = "#create"; };
  });
}
function jobToRecord(j) {
  return { mode: j.mode, prompt_mode: j.prompt_mode, idea: j.idea, prompt: j.prompt, aspect: j.aspect, megapixels: j.megapixels,
    duration: j.duration, seed: j.seed, start_image: j.start_image, end_image: j.end_image, refs: j.refs,
    loras: j.lora_picks || [], checkpoint: j.checkpoint, recipe_id: j.recipe_id, overrides: j.overrides };
}

// ------------------------------------------------------------------ LIBRARY

VIEWS.library = async function () {
  const v = $("#view");
  if (!S.outputs) v.innerHTML = `<div class="page"><div class="page-head"><h1>Library</h1></div><div class="empty">Loading…</div></div>`;
  try { S.outputs = await api("/api/outputs"); } catch (e) { fail(e); return; }
  updateLibBadge();
  if (S.route !== "library") return;
  const { items, groups } = S.outputs;
  const f = S.lib.filter;
  const fresh = items.filter((i) => i.new).length;
  let list = items;
  if (f === "new") list = list.filter((i) => i.new);
  else if (f === "fav") list = list.filter((i) => i.favorite);
  else if (["t2v", "i2v", "r2v"].includes(f)) list = list.filter((i) => i.mode === f);
  else if (f === "group") list = list.filter((i) => i.group === S.lib.group);
  else if (f === "compare") list = list.filter((i) => i.meta && i.meta.group && i.meta.compare);
  S.lib.list = list;
  const emptyMsg = f === "new" ? ["All caught up", "Every clip has been watched."]
    : items.length ? ["Nothing matches", "Try another filter."] : ["No videos yet", "Finished clips land here."];
  v.innerHTML = `<div class="page">
    <div class="page-head"><h1>Library</h1><span class="faint num">${items.length}</span><span class="spacer"></span>
      ${fresh ? `<button class="btn small ghost" id="seenAll">Mark all watched</button>` : ""}<button class="btn small ghost" id="grpBtn">Groups</button></div>
    <div class="chips scroll" style="margin-bottom:14px">
      ${[["all", "All"], ["new", fresh ? `<span class="new-dot" aria-hidden="true"></span>New ${fresh}` : "New"], ["fav", "★ Favorites"], ["t2v", "Text"], ["i2v", "Image"], ["r2v", "Reference"], ["compare", "Comparisons"]].map(([k, n]) => `<button class="chip ${f === k ? "on" : ""}" data-f="${k}">${n}</button>`).join("")}
      ${groups.map((g) => `<button class="chip ${f === "group" && S.lib.group === g ? "on" : ""}" data-g="${esc(g)}">${esc(g)}</button>`).join("")}
    </div>
    ${f === "compare" ? compareGroups(list) : list.length ? `<div class="contact">${list.map(shot).join("")}</div>`
      : `<div class="empty"><h2>${emptyMsg[0]}</h2><p>${emptyMsg[1]}</p></div>`}
  </div>`;
  $$("[data-f]").forEach((b) => b.onclick = () => { S.lib.filter = b.dataset.f; render(); });
  $$("[data-g]").forEach((b) => b.onclick = () => { S.lib.filter = "group"; S.lib.group = b.dataset.g; render(); });
  $$("[data-out]").forEach((b) => b.onclick = () => openOutput(items.find((i) => i.file === b.dataset.out), S.lib.list));
  $$("[data-cmp]").forEach((b) => b.onclick = () => openCompare(items.filter((i) => i.meta && i.meta.group === b.dataset.cmp)));
  $("#grpBtn").onclick = groupsSheet;
  const sa = $("#seenAll"); if (sa) sa.onclick = () => api("/api/outputs/seen", { body: { all: true } }).then(() => { toast("All marked as watched"); render(); }).catch(fail);
};

/* "T2V_00042" from "MMH3/T2V_00042-audio.mp4" */
const clipName = (f) => nameOf(f).replace(/\.mp4$/i, "").replace(/-audio$/i, "");

function shot(i) {
  const d = i.meta && (i.meta.duration || i.meta.frames / 24);
  const label = i.meta && i.meta.label && i.meta.compare ? ` · ${esc(i.meta.label)}` : "";
  return `<button class="shot${i.new ? " is-new" : ""}" data-out="${esc(i.file)}" title="${esc(i.file)}"><img loading="lazy" src="${thumbOut(i.file)}" alt="">
    <span class="tl">${i.new ? `<span class="new-pill">New</span>` : ""}${i.group ? `<span class="grp">${esc(i.group)}</span>` : ""}</span>
    ${i.favorite ? `<span class="fav">${ICON.star}</span>` : ""}
    <span class="meta"><span class="fn">${esc(clipName(i.file))}${label}</span><span class="d">${d ? `${Number(d).toFixed(1)}s` : ""}</span></span></button>`;
}

function updateLibBadge() {
  const n = S.outputs ? S.outputs.items.filter((i) => i.new).length : 0;
  const b = $("#libBadge"); b.hidden = !n; b.textContent = n > 99 ? "99+" : n;
}

/* Mark an output watched (opened in a player). */
function markSeen(file) {
  const it = S.outputs && S.outputs.items.find((x) => x.file === file);
  if (it && !it.new) return;
  if (it) it.new = false;
  updateLibBadge();
  api("/api/outputs/seen", { body: { files: [file] } }).catch(() => {});
}

function refreshOutputs() {
  if (S.route === "library") { render(); return; }
  api("/api/outputs").then((o) => { S.outputs = o; updateLibBadge(); }).catch(() => {});
}

function compareGroups(list) {
  const groups = {};
  list.forEach((i) => (groups[i.meta.group] = groups[i.meta.group] || []).push(i));
  const keys = Object.keys(groups);
  if (!keys.length) return `<div class="empty"><h2>No comparisons yet</h2><p>Use Compare in Create to render one clip per recipe on the same seed.</p></div>`;
  return keys.map((k) => `<button class="kit" data-cmp="${esc(k)}" style="width:100%;margin-bottom:10px;color:inherit;text-align:left;cursor:pointer">
    <div class="kit-strip">${groups[k].slice(0, 4).map((i) => `<img src="${thumbOut(i.file)}" alt="">`).join("")}</div>
    <div class="grow"><h3>${esc(jobTitle(groups[k][0].meta).slice(0, 50))}</h3><div class="faint" style="font-size:12px">${groups[k].some((i) => i.new) ? `<span class="new-dot" aria-label="new"></span>` : ""}${groups[k].map((i) => esc(i.meta.label || i.meta.recipe_id)).join(" vs ")}</div></div>${ICON.chev}</button>`).join("");
}
function openCompare(items) {
  items.sort((a, b) => (a.meta.label || "").localeCompare(b.meta.label || ""));
  items.forEach((i) => markSeen(i.file));
  openSheet("Compare", `
    <div class="compare-grid">${items.map((i) => `<figure><video src="${mediaOut(i.file)}" playsinline loop muted preload="auto"></video><figcaption>${esc(i.meta.label || i.meta.recipe_id)} <span class="faint num">${i.meta.timings && i.meta.timings.total_s ? clock(i.meta.timings.total_s) : ""}</span></figcaption></figure>`).join("")}</div>
    <div class="row" style="margin-top:12px"><button class="btn primary grow" id="cmpPlay">${ICON.play} Play together</button><button class="btn" id="cmpSound">Sound: off</button></div>
    <p class="note">Same prompt and seed; only the recipe differs. Tap a label to open that clip.</p>`, (body) => {
    const vids = $$("video", body);
    let soundIdx = -1;
    $("#cmpPlay", body).onclick = () => { vids.forEach((v) => { v.currentTime = 0; v.play(); }); };
    $("#cmpSound", body).onclick = (e) => { soundIdx = (soundIdx + 2) % (vids.length + 1) - 1; vids.forEach((v, i) => (v.muted = i !== soundIdx)); e.target.textContent = soundIdx < 0 ? "Sound: off" : `Sound: ${items[soundIdx].meta.label}`; };
    $$("figcaption", body).forEach((c, i) => c.onclick = () => openOutput(items[i], items));
  }, () => { if (S.route === "library") render(); });
}

function outputTitle(i) {
  const m = i.meta || {};
  return jobTitle(m.studio ? m : { prompt: m.actual_prompt || m.prompt, idea: m.prompt_idea, prompt_mode: m.prompt_mode, mode: i.mode });
}

/* The clip player. `list` is what the Library was showing, so ‹ › (and swipes, and the
   arrow keys) step through the same clips in the same order. */
function openOutput(start, list) {
  list = (list && list.length ? list : [start]).slice();
  let idx = Math.max(0, list.findIndex((x) => x.file === start.file));
  let changed = false;
  const draw = (body) => {
    const i = list[idx];
    const m = i.meta || {};
    $("#sheetTitle").textContent = outputTitle(i).slice(0, 60);
    if (i.new) { markSeen(i.file); changed = true; }
    const prompt = m.prompt || m.actual_prompt || "";
    body.innerHTML = `
    <div class="player"><video src="${mediaOut(i.file)}" controls playsinline loop autoplay></video></div>
    <div class="clip-line">
      ${list.length > 1 ? `<button class="nav prev" data-nav="-1" aria-label="Previous clip" ${idx === 0 ? "disabled" : ""}>${ICON.chev}</button>` : ""}
      <div class="grow"><div class="fn">${esc(clipName(i.file))}</div>${list.length > 1 ? `<div class="faint num">${idx + 1} of ${list.length} · swipe the video to step</div>` : ""}</div>
      ${list.length > 1 ? `<button class="nav next" data-nav="1" aria-label="Next clip" ${idx === list.length - 1 ? "disabled" : ""}>${ICON.chev}</button>` : ""}
    </div>
    <div class="actions">
      <button class="btn${i.favorite ? " on" : ""}" data-a="fav">${ICON.star} ${i.favorite ? "Favorited" : "Favorite"}</button>
      <button class="btn" data-a="reuse">${ICON.reuse} Reuse</button>
      <button class="btn" data-a="continue">${ICON.next} Continue</button>
      <button class="btn" data-a="ref">${ICON.ref} As reference</button>
      <button class="btn" data-a="group">${ICON.folder} ${i.group ? esc(i.group) : "Group"}</button>
      <a class="btn" href="${mediaOut(i.file)}" download>${ICON.down} Download</a>
    </div>
    <dl class="kv">
      <dt>Made</dt><dd>${new Date(i.mtime * 1000).toLocaleString()} · ${bytes(i.size)}</dd>
      ${m.label ? `<dt>Recipe</dt><dd>${esc(m.label)}${m.overrides && Object.keys(m.overrides).length ? ` (tuned: ${esc(Object.keys(m.overrides).join(", "))})` : ""}</dd>` : m.generation_profile ? `<dt>Profile</dt><dd>${esc(m.generation_profile)}</dd>` : ""}
      ${m.recipe ? `<dt>Sampling</dt><dd>${m.recipe.steps} steps · ${esc(m.recipe.sampler)}/${esc(m.recipe.scheduler)} · turbo ${m.recipe.strength}${m.recipe.shift ? ` · shift ${m.recipe.shift.join("/")}` : ""}${m.recipe.extend ? " · extend" : ""}</dd>` : ""}
      ${m.seed ? `<dt>Seed</dt><dd class="num">${m.seed}</dd>` : ""}
      ${m.width ? `<dt>Size</dt><dd>${m.width}×${m.height} · ${m.frames} frames</dd>` : ""}
      ${m.timings && m.timings.total_s ? `<dt>Render</dt><dd>${clock(m.timings.total_s)}${m.timings.sampling_s ? ` (sampling ${clock(m.timings.sampling_s)})` : ""}</dd>` : ""}
      ${(m.loras || []).length ? `<dt>LoRAs</dt><dd>${m.loras.map((l) => `${esc(l.nickname || l.file || l.filename)} ${l.strength}`).join("<br>")}</dd>` : ""}
      <dt>File</dt><dd class="faint">${esc(i.file)}</dd>
    </dl>
    ${m.recipe ? `<details class="more cfg-box"><summary>${ICON.chev} Settings used</summary>${settingsRows({
      recipe: { ...m.recipe, label: m.label || m.recipe.label }, family: m.recipe.family || FAMILY[m.mode], checkpoint: m.checkpoint,
      tuned: Object.keys(m.overrides || {}), size: m.width ? [m.width, m.height] : null, frames: m.frames })}</details>` : ""}
    ${(m.idea || m.prompt_idea) ? `<h3>Idea</h3><div class="prompt-text">${esc(m.idea || m.prompt_idea)}</div>` : ""}
    <div class="row" style="margin-top:12px"><h3 class="grow">Prompt</h3>${prompt ? `<button class="btn small ghost" data-a="copy">Copy</button>` : ""}</div>
    <div class="prompt-text">${esc(prompt || "No record for this file.")}</div>
    <button class="btn danger" data-a="del" style="width:100%;margin-top:18px">${ICON.trash} Delete this video</button>`;
    body.scrollTop = 0;
    $$("[data-nav]", body).forEach((b) => b.onclick = () => go(+b.dataset.nav));
    bindSwipe($(".player", body));
    $$("[data-a]", body).forEach((b) => b.onclick = async () => {
      const a = b.dataset.a;
      try {
        if (a === "fav") {
          await api("/api/outputs", { method: "PATCH", body: { file: i.file, favorite: !i.favorite } });
          i.favorite = !i.favorite; changed = true;
          b.classList.toggle("on", i.favorite); b.innerHTML = `${ICON.star} ${i.favorite ? "Favorited" : "Favorite"}`;
        }
        if (a === "copy") { await copyText(prompt); toast("Prompt copied"); }
        if (a === "reuse") { const rec = await api(`/api/outputs/reuse?file=${encodeURIComponent(i.file)}`); applyRecord(rec); closeSheet(); location.hash = "#create"; toast("Loaded into Create"); }
        if (a === "continue") { const r = await api("/api/outputs/continue", { body: { file: i.file } }); const rec = await api(`/api/outputs/reuse?file=${encodeURIComponent(i.file)}`); applyRecord({ ...rec, mode: "i2v", start_image: r.start_image, end_image: null, seed: null }); F.lockSeed = false; F.drafted = null; F.prompt = ""; F.auto = true; F.idea = ""; saveForm(); closeSheet(); location.hash = "#create"; toast("Last frame set as the start. Describe what happens next."); }
        if (a === "ref") { const r = await api("/api/outputs/to-input", { body: { file: i.file } }); F.mode = "r2v"; await addReference({ kind: "video", file: r.file, has_audio: true }); saveForm(); closeSheet(); location.hash = "#create"; toast("Added as a reference"); }
        if (a === "group") groupPicker(i, () => openOutput(i, list));
        if (a === "del") {
          if (!confirm("Delete this video? This can't be undone.")) return;
          await api(`/api/outputs?file=${encodeURIComponent(i.file)}`, { method: "DELETE" });
          toast("Deleted"); changed = true;
          if (S.outputs) S.outputs.items = S.outputs.items.filter((x) => x.file !== i.file);
          list.splice(idx, 1);
          if (!list.length) { closeSheet(); return; }
          idx = Math.min(idx, list.length - 1); draw(body);
        }
      } catch (e) { fail(e); }
    });
  };
  const go = (d) => { const n = idx + d; if (n < 0 || n >= list.length) return; idx = n; draw($("#sheetBody")); };
  openSheet(outputTitle(start).slice(0, 60), "", draw, () => { sheetKeys = null; if (changed && S.route === "library") render(); });
  sheetKeys = (e) => { if (e.key === "ArrowLeft") go(-1); if (e.key === "ArrowRight") go(1); };
}

/* Horizontal swipe on the player steps clips. Touches on the control bar are left alone. */
function bindSwipe(el) {
  if (!el) return;
  let x0 = null, y0 = 0;
  el.addEventListener("touchstart", (e) => {
    const t = e.touches[0]; const r = el.getBoundingClientRect();
    x0 = t.clientY > r.bottom - 64 ? null : t.clientX; y0 = t.clientY;
  }, { passive: true });
  el.addEventListener("touchend", (e) => {
    if (x0 === null) return;
    const t = e.changedTouches[0]; const dx = t.clientX - x0, dy = t.clientY - y0; x0 = null;
    if (Math.abs(dx) > 60 && Math.abs(dy) < 50) { const b = $(`[data-nav="${dx < 0 ? 1 : -1}"]`, el.parentElement); if (b && !b.disabled) b.click(); }
  }, { passive: true });
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return; } catch { /* not a secure context: fall back */ }
  const ta = Object.assign(document.createElement("textarea"), { value: text });
  ta.style.cssText = "position:fixed;opacity:0"; document.body.append(ta); ta.select();
  document.execCommand("copy"); ta.remove();
}
function applyRecord(rec) {
  if (!rec || !rec.mode) return toast("No settings were saved with this clip", true);
  F.mode = rec.mode;
  F.auto = rec.prompt_mode === "auto" && !!rec.idea;
  F.idea = rec.idea || ""; F.prompt = rec.prompt || "";
  F.drafted = F.auto && rec.prompt ? { mode: rec.mode, idea: F.idea, prompt: rec.prompt } : null;
  if (rec.aspect) F.aspect = rec.aspect;
  if (rec.megapixels) F.megapixels = Number(rec.megapixels);
  if (rec.duration) F.duration = Number(rec.duration);
  F.start_image = rec.start_image || null; F.end_image = rec.end_image || null;
  F.refs = rec.refs || [];
  F.avState = F.refs.some((r) => r.kind === "video" || r.kind === "audio"); F.recipeBeforeAv = null;  // a reused clip keeps its own recipe
  F.checkpoint = rec.checkpoint || "stock";
  if (rec.recipe_id) F.recipe[FAMILY[rec.mode]] = rec.recipe_id;
  F.adv = {};
  Object.entries(rec.overrides || {}).forEach(([k, v]) => { F.adv[k] = k === "extend" ? (v && v !== "off" ? "on" : "off") : v; });
  if (rec.seed) { F.seed = String(rec.seed); F.lockSeed = true; }
  const known = S.loras ? S.loras.items : [];
  F.loras = [];
  for (const l of rec.loras || []) {
    const k = known.find((x) => x.key === String(l.key) || x.filename === l.file);
    const key = k ? k.key : String(l.key || l.file);
    let e = F.loras.find((x) => x.key === key);
    if (!e) { e = { key, file: k ? undefined : l.file, nickname: (k && k.nickname) || l.nickname || l.file || l.key, strength: Number(l.strength ?? 1), families: k ? k.families : undefined, active: k ? k.active : undefined, parts: {} }; F.loras.push(e); }
    if (k) { e.parts[l.file] = Number(l.strength ?? 1); if (l.role !== "helper") e.strength = Number(l.strength ?? 1); }
  }
  saveForm();
}
function groupPicker(i, onDone) {
  const groups = S.outputs.groups;
  openSheet("Put in a group", `
    <div class="chips">${groups.map((g) => `<button class="chip ${i.group === g ? "on" : ""}" data-g="${esc(g)}">${esc(g)}</button>`).join("")}
      ${i.group ? `<button class="chip" data-g="">No group</button>` : ""}</div>
    <div class="row" style="margin-top:14px"><input type="text" id="ng" class="grow" placeholder="New group"><button class="btn" id="ngAdd">Add</button></div>`, (body) => {
    const set = async (g) => {
      await api("/api/outputs", { method: "PATCH", body: { file: i.file, group: g } });
      i.group = g; render();
      if (onDone) onDone(); else closeSheet();
    };
    $$("[data-g]", body).forEach((b) => b.onclick = () => set(b.dataset.g).catch(fail));
    $("#ngAdd", body).onclick = () => { const g = $("#ng", body).value.trim(); if (g) set(g).catch(fail); };
  });
}
function groupsSheet() {
  const groups = [...S.outputs.groups];
  const draw = (body) => {
    body.innerHTML = `${groups.length ? `<div class="list">${groups.map((g, i) => `<div><span class="grow">${esc(g)}</span><button class="icon-btn" data-rm="${i}" aria-label="Delete group">${ICON.trash}</button></div>`).join("")}</div>` : `<p class="muted">No groups yet.</p>`}
      <div class="row" style="margin-top:14px"><input type="text" id="ng" class="grow" placeholder="New group"><button class="btn" id="ngAdd">Add</button></div>
      <p class="note">Deleting a group keeps its videos.</p>`;
    $$("[data-rm]", body).forEach((b) => b.onclick = () => { groups.splice(+b.dataset.rm, 1); save(body); });
    $("#ngAdd", body).onclick = () => { const g = $("#ng", body).value.trim(); if (g && !groups.includes(g)) { groups.push(g); save(body); } };
  };
  const save = (body) => api("/api/groups", { method: "PUT", body: { groups } }).then(() => { S.outputs.groups = groups; draw(body); }).catch(fail);
  openSheet("Groups", "", draw, () => render());
}

// ------------------------------------------------------------------ REFERENCES

VIEWS.refs = async function () {
  const v = $("#view");
  if (!S.assets) v.innerHTML = `<div class="page"><div class="page-head"><h1>References</h1></div><div class="empty">Loading…</div></div>`;
  try { await loadAssets(true); } catch (e) { fail(e); return; }
  if (S.route !== "refs") return;
  const kindF = S.refKind || "all";
  const list = S.assets.filter((a) => kindF === "all" || a.kind === kindF);
  v.innerHTML = `<div class="page">
    <div class="page-head"><h1>References</h1><span class="spacer"></span><button class="btn small primary" id="up">${ICON.up} Upload</button></div>
    <div class="section-title" style="margin-top:0"><h2>Kits</h2><span class="faint">reusable reference sets</span></div>
    ${kitList(S.kits, false)}
    <div class="section-title"><h2>Files</h2></div>
    <div class="chips" style="margin-bottom:12px">${[["all", "All"], ["image", "Pictures"], ["video", "Clips"], ["audio", "Audio"]].map(([k, n]) => `<button class="chip ${kindF === k ? "on" : ""}" data-k="${k}">${n}</button>`).join("")}</div>
    ${list.length ? `<div class="assets">${list.map((a) => assetTile(a)).join("")}</div>` : `<div class="empty"><h2>No files</h2><p>Upload pictures, clips or audio from your phone. They're kept on your volume.</p></div>`}
  </div>`;
  $("#up").onclick = async () => { try { await upload(await pickFiles("image/*,video/*,audio/*")); toast("Uploaded"); render(); } catch (e) { fail(e); } };
  $$("[data-k]").forEach((b) => b.onclick = () => { S.refKind = b.dataset.k; render(); });
  $$("[data-kit]").forEach((b) => b.onclick = () => kitSheet(S.kits.find((k) => k.id === b.dataset.kit)));
  $$(".asset").forEach((el) => el.onclick = () => assetSheet(S.assets.find((a) => a.file === el.dataset.file)));
};
function kitSheet(k) {
  openSheet(k.name, `
    <div class="refs">${k.refs.map((r) => `<div class="ref">${r.kind === "audio" ? `<span class="kind">${ICON.audio}</span>` : `<img src="${thumbIn(r.file)}" alt="">`}</div>`).join("")}</div>
    <div class="sheet-foot">
      <button class="btn danger" id="kDel">${ICON.trash}</button>
      <button class="btn" id="kRen">Rename</button>
      <button class="btn primary" id="kUse">Use in Create</button>
    </div>`, (body) => {
    $("#kUse", body).onclick = () => { F.mode = "r2v"; F.refs = k.refs.map((r) => ({ ...r })); saveForm(); closeSheet(); location.hash = "#create"; };
    $("#kRen", body).onclick = () => saveKitSheet(k.refs, k);
    $("#kDel", body).onclick = async () => { if (!confirm(`Delete kit "${k.name}"? Its files stay.`)) return; await api(`/api/kits/${k.id}`, { method: "DELETE" }).catch(fail); closeSheet(); render(); };
  });
}
function assetSheet(a) {
  const media = a.kind === "image" ? `<img src="${mediaIn(a.file)}" alt="" style="max-height:52vh;margin:0 auto;border-radius:12px">`
    : a.kind === "video" ? `<video src="${mediaIn(a.file)}" controls playsinline style="max-height:52vh;width:100%;border-radius:12px;background:#000"></video>`
      : `<audio src="${mediaIn(a.file)}" controls style="width:100%"></audio>`;
  openSheet(a.nickname || nameOf(a.file), `${media}
    <label class="field" for="nick" style="margin-top:14px">Nickname</label>
    <div class="row"><input type="text" id="nick" class="grow" value="${esc(a.nickname)}" placeholder="${esc(nameOf(a.file))}"><button class="btn" id="nickSave">Save</button></div>
    <p class="note faint">${esc(a.file)} · ${bytes(a.size)}${a.duration ? ` · ${a.duration}s` : ""}</p>
    <div class="sheet-foot">
      <button class="btn danger" id="aDel">${ICON.trash}</button>
      ${a.kind === "image" ? `<button class="btn" id="aStart">Start frame</button>` : ""}
      <button class="btn primary" id="aRef">Add as reference</button>
    </div>`, (body) => {
    $("#nickSave", body).onclick = () => api("/api/assets", { method: "PATCH", body: { file: a.file, nickname: $("#nick", body).value } }).then(() => { toast("Saved"); S.assets = null; closeSheet(); render(); }).catch(fail);
    $("#aDel", body).onclick = async () => { if (!confirm("Delete this file?")) return; try { await api(`/api/assets?file=${encodeURIComponent(a.file)}`, { method: "DELETE" }); closeSheet(); render(); } catch (e) { fail(e); } };
    const st = $("#aStart", body); if (st) st.onclick = () => { F.mode = "i2v"; F.start_image = a.file; saveForm(); closeSheet(); location.hash = "#create"; };
    $("#aRef", body).onclick = async () => { F.mode = "r2v"; await addReference(a); closeSheet(); location.hash = "#create"; };
  });
}

// ------------------------------------------------------------------ MORE

VIEWS.more = function () {
  const sub = (location.hash.split("/")[1] || "");
  if (sub === "loras") return viewLoras();
  if (sub === "system") return viewSystem();
  if (sub === "recipes") return viewRecipes();
  const L = S.live;
  const mem = L ? L.memory : null;
  $("#view").innerHTML = `<div class="page">
    <div class="page-head"><h1>More</h1></div>
    <div class="list">
      <a href="#more/loras">${ICON.cube}<div class="grow"><h3>LoRAs &amp; models</h3><div class="sub">Your catalog, CivitAI search, bookmarks and collections</div></div>${ICON.chev}</a>
      <a href="#more/recipes">${ICON.spark}<div class="grow"><h3>Recipes</h3><div class="sub">Speed presets: make your own, pick what Create shows and what Compare runs</div></div>${ICON.chev}</a>
      <a href="#more/system">${ICON.cpu}<div class="grow"><h3>System</h3><div class="sub">${mem ? `RAM ${mem.used_gb} of ${mem.limit_gb} GB` : "Memory, downloads, services"}</div></div>${ICON.chev}</a>
      <button class="item" id="spBtn">${ICON.text}<div class="grow"><h3>Auto prompt instructions</h3><div class="sub">The system prompts behind Auto and Tweak</div></div>${ICON.chev}</button>
      <button class="item" id="logBtn">${ICON.log}<div class="grow"><h3>Logs</h3><div class="sub">ComfyUI, Studio, downloads</div></div>${ICON.chev}</button>
      <a href="${location.origin.replace(/-7860\./, "-8188.")}" target="_blank" rel="noopener">${ICON.globe}<div class="grow"><h3>Open ComfyUI</h3><div class="sub">The full node editor, on port 8188</div></div>${ICON.chev}</a>
    </div>
    <p class="note faint" style="margin-top:18px">MMH3 Studio ${esc(S.boot.version)}</p>
  </div>`;
  $("#spBtn").onclick = promptsSheet;
  $("#logBtn").onclick = () => logsSheet("comfyui");
};

/* Catalog entries that are not downloaded on startup and not on disk yet: one tap to get. */
const isQuick = (l) => !l.installed && l.auto_install === false && !l.untracked && l.enabled !== false && l.version_id;
const loraMatches = (l, q) => !q || `${l.nickname} ${l.key} ${(l.tags || []).join(" ")} ${(l.trigger_words || []).join(" ")}`.toLowerCase().includes(q.toLowerCase());

function loraProgress(l) {
  const files = l.files || [];
  const busy = files.find((f) => f.state === "downloading");
  const failed = files.find((f) => f.state === "failed");
  const queued = files.find((f) => f.state === "queued" && f.install !== false);
  return { busy, failed, queued,
    pct: busy && busy.total ? Math.round(100 * (busy.done || 0) / busy.total) : null };
}

async function viewLoras() {
  const v = $("#view");
  if (!S.loras) v.innerHTML = `<div class="page"><div class="page-head"><a class="icon-btn" href="#more" aria-label="Back" style="transform:scaleX(-1)">${ICON.chev}</a><h1>LoRAs</h1></div><div class="empty">Loading…</div></div>`;
  let d; try { d = await loadLoras(true); } catch (e) { fail(e); return; }
  if (!location.hash.startsWith("#more/loras")) return;
  v.innerHTML = `<div class="page">
    <div class="page-head"><a class="icon-btn" href="#more" aria-label="Back" style="transform:scaleX(-1)">${ICON.chev}</a><h1>LoRAs</h1><span class="spacer"></span>
      <button class="btn small primary" id="browse">${ICON.globe} Browse CivitAI</button></div>
    ${d.has_token ? "" : `<p class="note warn">Set CIVITAI_TOKEN on the pod for downloads, bookmarks and collections.</p>`}
    <div class="row" style="margin-bottom:6px"><input type="search" id="lq" class="grow" placeholder="Search your catalog" value="${esc(S.loraQ || "")}"><button class="btn small" id="sync" title="Download anything set to download on startup that's missing">Sync</button></div>
    <div id="loraLists"></div>
    <div class="section-title"><h2>Base models</h2></div>
    <div class="list">${d.checkpoints.map((c) => `<div><div class="grow"><h3>${esc(c.label)}</h3><div class="sub">${Object.entries(c.available).map(([f, ok]) => `${f === "ref2v" ? "R2V" : "T2V/I2V"} ${ok ? "ready" : "missing"}`).join(" · ")}</div></div>
      ${c.builtin ? (Object.values(c.available).some((x) => !x) && c.id === "eros" ? `<button class="btn small" data-eros>Download</button>` : "") : `<button class="icon-btn" data-rmck="${esc(c.id)}" aria-label="Remove">${ICON.trash}</button>`}</div>`).join("")}</div>
  </div>`;
  drawLoraLists();
  const lq = $("#lq"); lq.oninput = debounce(() => { S.loraQ = lq.value; drawLoraLists(); }, 200);
  $("#sync").onclick = () => api("/api/loras/sync", { body: {} }).then(() => { toast("Checking for missing files…"); watchLoraDownloads(true); }).catch(fail);
  $("#browse").onclick = () => civitaiBrowser();
  const eros = $("[data-eros]"); if (eros) eros.onclick = () => api("/api/system/settings", { method: "PUT", body: { eros_enabled: true } }).then(() => toast("Downloading Eros…")).catch(fail);
  $$("[data-rmck]").forEach((b) => b.onclick = async () => { if (!confirm("Remove this base model? Delete its file too?")) return; await api(`/api/checkpoints/${b.dataset.rmck}?file=1`, { method: "DELETE" }).catch(fail); S.boot = await api("/api/boot"); viewLoras(); });
  watchLoraDownloads();
}

/* Redraws only the lists, so typing in the search box keeps focus (and the keyboard up). */
function drawLoraLists() {
  const box = $("#loraLists"); if (!box || !S.loras) return;
  const q = S.loraQ || "";
  const all = S.loras.items.filter((l) => loraMatches(l, q));
  const quick = all.filter(isQuick);
  const mine = all.filter((l) => !isQuick(l));
  box.innerHTML = `
    ${quick.length ? `<details class="more quick-box" id="quickBox" ${q || S.quickOpen ? "open" : ""}>
      <summary>${ICON.chev}<span class="grow">Quick install <span class="faint num">${quick.length}</span></span><span class="faint" style="font-size:12px;font-weight:500">not downloaded on startup</span></summary>
      <div class="list quick">${quick.map(quickRow).join("")}</div></details>` : ""}
    <div class="section-title" style="margin-top:${quick.length ? 12 : 10}px"><h2>Your LoRAs</h2><span class="faint num">${mine.length}</span></div>
    <div>${mine.map(loraRow).join("") || `<div class="empty"><p>${q ? "No matches." : "Nothing installed yet. Tap Get above, or browse CivitAI."}</p></div>`}</div>`;
  $$("[data-lkey]", box).forEach((el) => el.onclick = (e) => {
    if (e.target.closest("[data-get]")) return;
    loraSheet(S.loras.items.find((l) => l.key === el.dataset.lkey));
  });
  $$("[data-get]", box).forEach((b) => b.onclick = () => getLora(b.dataset.get));
  const qb = $("#quickBox", box); if (qb) qb.ontoggle = () => { if (!S.loraQ) S.quickOpen = qb.open; };
}

function quickRow(l) {
  const p = loraProgress(l);
  const action = p.busy ? `<span class="faint num" style="font-size:13px">${p.pct != null ? `${p.pct}%` : "…"}</span>`
    : p.queued && S.loras.busy ? `<span class="faint" style="font-size:13px">Queued</span>`
      : `<button class="btn small ${p.failed ? "" : "primary"}" data-get="${esc(l.key)}">${p.failed ? "Retry" : `${ICON.down} Get`}</button>`;
  return `<div class="qrow" data-lkey="${esc(l.key)}">
    <div class="lora-img sm">${l.image ? `<img src="${esc(l.image)}" alt="" loading="lazy">` : ICON.cube}</div>
    <div class="grow"><h3>${esc(l.nickname || l.key)}</h3>
      <div class="sub">${p.failed ? `<span class="err">${esc((p.failed.error || "download failed").slice(0, 80))}</span>` : esc([...(l.tags || []).slice(0, 3), Number(l.recommended_strength || 1).toFixed(2)].join(" · "))}</div>
      ${p.busy ? `<div class="bar amber" style="margin:6px 0 0"><i style="width:${p.pct || 3}%"></i></div>` : ""}
    </div>${action}</div>`;
}

async function getLora(key) {
  try {
    await api(`/api/loras/${encodeURIComponent(key)}/install`, { body: {} });
    toast("Downloading…");
    watchLoraDownloads(true);
  } catch (e) { fail(e); }
}

/* While the download worker is busy, refresh the LoRA screen every few seconds. */
let loraWatch = null;
function watchLoraDownloads(soon = false) {
  clearTimeout(loraWatch);
  if (!soon && !(S.loras && S.loras.busy)) return;
  loraWatch = setTimeout(async () => {
    if (!location.hash.startsWith("#more/loras")) return;
    try { await loadLoras(true); } catch { return; }
    if (!location.hash.startsWith("#more/loras") || !$("#loraLists")) return;
    drawLoraLists();
    watchLoraDownloads();
  }, soon ? 900 : 2500);
}

function loraRow(l) {
  const p = loraProgress(l);
  const status = p.busy ? `Downloading ${p.pct != null ? p.pct + "%" : "…"}`
    : p.failed ? `Couldn't download: ${(p.failed.error || "").slice(0, 90)}` : l.installed ? "" : l.enabled === false ? "Disabled" : "Not downloaded yet";
  return `<button class="lora" data-lkey="${esc(l.key)}" style="width:100%;background:none;border:0;border-bottom:1px solid color-mix(in srgb,var(--line) 55%,transparent);text-align:left;color:inherit;cursor:pointer">
    <div class="lora-img">${l.image ? `<img src="${esc(l.image)}" alt="" loading="lazy">` : ICON.cube}</div>
    <div class="grow"><h3>${esc(l.nickname || l.key)}</h3>
      <div>${(l.families || []).map((f) => `<span class="fam ${f}">${f === "ref2v" ? "R2V" : "T2V/I2V"}</span>`).join("")}${(l.files || []).length > 1 ? `<span class="fam">${l.files.length} files</span>` : ""}${l.untracked ? `<span class="fam">local file</span>` : ""}${l.auto_install === false && !l.untracked ? `<span class="fam">quick install</span>` : ""}</div>
      ${status ? `<div class="${p.failed ? "job-sub err" : "faint"}" style="font-size:12px">${esc(status)}</div>` : ""}
    </div><span class="faint num" style="font-size:12px">${Number(l.recommended_strength || 1).toFixed(2)}</span></button>`;
}
function loraSheet(l) {
  const files = l.files || [];
  const civ = !!l.version_id && !l.untracked;
  openSheet(l.nickname || l.key, `
    <div class="stack">
      <div><label class="field">Nickname</label><input type="text" id="lnick" value="${esc(l.nickname || "")}"></div>
      <div class="grid2">
        <div><label class="field">Default strength</label><input type="number" step="0.05" id="lstr" value="${esc(l.recommended_strength ?? 1)}"></div>
        <div><label class="field">Enabled</label><label class="switch"><input type="checkbox" id="len" ${l.enabled !== false ? "checked" : ""}><span></span></label></div>
      </div>
      ${civ ? `<div class="row"><div class="grow"><label class="field" style="margin:0">Download on startup</label><div class="faint" style="font-size:12px">Off: stays under Quick install until you tap Get.</div></div><label class="switch"><input type="checkbox" id="lauto" ${l.auto_install !== false ? "checked" : ""}><span></span></label></div>` : ""}
      <div><label class="field">Trigger words (comma separated)</label><input type="text" id="ltrig" value="${esc((l.trigger_words || []).join(", "))}"></div>
      <div><label class="field">Tags</label><input type="text" id="ltags" value="${esc((l.tags || []).join(", "))}"></div>
      <div><h3>Files</h3><p class="note">Which file loads for which mode. Helpers load alongside the main file.</p>
        ${files.map((f, i) => `<div class="file-row"><label class="switch" title="Install"><input type="checkbox" data-inst="${i}" ${f.install !== false ? "checked" : ""} ${l.untracked ? "disabled" : ""}><span></span></label>
          <div class="grow">${esc(f.name)}<div class="faint">${f.installed ? "on disk" : f.state === "available" ? "not downloaded" : esc(f.state || "not downloaded")}${f.error ? ` · ${esc(f.error)}` : ""}</div></div>
          <select data-ffam="${i}">${[["any", "All modes"], ["fl2v", "T2V/I2V"], ["ref2v", "R2V"]].map(([k, n]) => `<option value="${k}" ${f.family === k ? "selected" : ""}>${n}</option>`).join("")}</select>
          <select data-frole="${i}">${[["main", "Main"], ["helper", "Helper"]].map(([k, n]) => `<option value="${k}" ${f.role === k ? "selected" : ""}>${n}</option>`).join("")}</select></div>`).join("")}
      </div>
      ${l.version_id ? `<p class="note faint">CivitAI version ${esc(l.version_id)}${l.base_model ? ` · ${esc(l.base_model)}` : ""}</p>` : ""}
    </div>
    <div class="sheet-foot"><button class="btn danger" id="lDel" title="Remove from the catalog and delete its files">${ICON.trash}</button>
      ${civ && l.installed ? `<button class="btn" id="lUninst" title="Delete the files but keep it under Quick install">Uninstall</button>` : ""}
      ${civ && !l.installed ? `<button class="btn" id="lGet">${ICON.down} Get now</button>` : ""}
      <button class="btn primary" id="lSave">Save</button></div>`, (body) => {
    $("#lSave", body).onclick = async () => {
      const nf = files.map((f, i) => ({ id: f.id ?? null, name: f.name, family: $(`[data-ffam="${i}"]`, body).value, role: $(`[data-frole="${i}"]`, body).value, install: $(`[data-inst="${i}"]`, body).checked }));
      const split = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
      const auto = $("#lauto", body);
      try {
        await api("/api/loras", { body: { key: l.key, version_id: l.version_id, filename: l.untracked ? l.filename : undefined, nickname: $("#lnick", body).value, recommended_strength: Number($("#lstr", body).value), enabled: $("#len", body).checked, trigger_words: split($("#ltrig", body).value), tags: split($("#ltags", body).value), files: l.untracked ? undefined : nf, auto_install: auto ? auto.checked : undefined } });
        toast("Saved"); closeSheet(); viewLoras();
      } catch (e) { fail(e); }
    };
    const un = $("#lUninst", body); if (un) un.onclick = async () => {
      if (!confirm("Delete this LoRA's files? It stays under Quick install, one tap to get it back.")) return;
      try { await api(`/api/loras/${encodeURIComponent(l.key)}/uninstall`, { body: {} }); toast("Uninstalled"); closeSheet(); viewLoras(); } catch (e) { fail(e); }
    };
    const get = $("#lGet", body); if (get) get.onclick = () => { closeSheet(); getLora(l.key).then(() => viewLoras()); };
    $("#lDel", body).onclick = async () => {
      if (!confirm("Remove from the catalog and delete its files?")) return;
      await api(`/api/loras/${encodeURIComponent(l.key)}?file=1`, { method: "DELETE" }).catch(fail); closeSheet(); viewLoras();
    };
  });
}

// CivitAI browser
function civitaiBrowser() {
  const st = { source: "search", kind: "lora", q: "", h3: true, items: [], next: null, collection: null, collections: null, loading: false, error: "" };
  const load = async (body, more = false) => {
    st.loading = true; st.error = ""; if (!more) st.items = []; draw(body);
    try {
      let r;
      if (st.source === "collections" && !st.collection) { st.collections = (await api("/api/civitai/collections")).items; r = { items: [], next: null }; }
      else if (st.source === "collections") r = await api(`/api/civitai/collections/${st.collection.id}?kind=${st.kind}&h3=${st.h3 ? 1 : 0}${more && st.next ? `&cursor=${encodeURIComponent(st.next)}` : ""}`);
      else r = await api(`/api/civitai/search?source=${st.source}&kind=${st.kind}&h3=${st.h3 ? 1 : 0}&q=${encodeURIComponent(st.q)}${more && st.next ? `&cursor=${encodeURIComponent(st.next)}` : ""}`);
      st.items = more ? st.items.concat(r.items) : r.items; st.next = r.next;
    } catch (e) { st.error = e.message; }
    st.loading = false; draw(body);
  };
  const draw = (body) => {
    body.innerHTML = `
      <div class="seg" style="margin-bottom:10px">${[["search", "Search"], ["bookmarks", "Bookmarks"], ["collections", "Collections"]].map(([k, n]) => `<button data-src="${k}" class="${st.source === k ? "on" : ""}">${n}</button>`).join("")}</div>
      <div class="row" style="margin-bottom:10px">
        <div class="chips grow">${[["lora", "LoRAs"], ["checkpoint", "Checkpoints"]].map(([k, n]) => `<button class="chip ${st.kind === k ? "on" : ""}" data-kind="${k}">${n}</button>`).join("")}
          <button class="chip ${st.h3 ? "on" : ""}" data-h3>MiniMax H3 only</button></div></div>
      ${st.source === "search" ? `<form id="sf" class="row" style="margin-bottom:6px"><input type="search" id="sq" class="grow" placeholder="Search CivitAI" value="${esc(st.q)}"><button class="btn">Search</button></form>` : ""}
      ${st.source === "collections" && st.collection ? `<div class="row" style="margin-bottom:6px"><button class="btn small ghost" data-back style="padding-left:0"><span style="transform:scaleX(-1);display:inline-flex">${ICON.chev}</span> Collections</button><h3 class="grow">${esc(st.collection.name)}</h3></div>` : ""}
      ${st.error ? `<p class="note warn">${esc(st.error)}</p>` : ""}
      ${st.source === "collections" && !st.collection ? (st.collections ? (st.collections.length ? `<div class="list">${st.collections.map((c) => `<button class="item" data-col="${c.id}"><div class="grow"><h3>${esc(c.name)}</h3><div class="sub">${c.count != null ? `${c.count} items` : ""}</div></div>${ICON.chev}</button>`).join("")}</div>` : `<p class="muted">No model collections found on your account.</p>`) : st.loading ? `<div class="empty">Loading your collections…</div>` : "")
        : `${st.items.map(civItem).join("")}${st.loading ? `<div class="empty">Loading…</div>` : !st.items.length && !st.error ? `<div class="empty"><p>No results.</p></div>` : ""}
           ${st.next && !st.loading ? `<button class="btn" data-more style="width:100%;margin-top:12px">Load more</button>` : ""}`}`;
    $$("[data-src]", body).forEach((b) => b.onclick = () => { st.source = b.dataset.src; st.collection = null; load(body); });
    $$("[data-kind]", body).forEach((b) => b.onclick = () => { st.kind = b.dataset.kind; load(body); });
    $("[data-h3]", body).onclick = () => { st.h3 = !st.h3; load(body); };
    const sf = $("#sf", body); if (sf) sf.onsubmit = (e) => { e.preventDefault(); st.q = $("#sq", body).value; load(body); };
    const bk = $("[data-back]", body); if (bk) bk.onclick = () => { st.collection = null; st.items = []; draw(body); };
    $$("[data-col]", body).forEach((b) => b.onclick = () => { st.collection = st.collections.find((c) => String(c.id) === b.dataset.col); load(body); });
    const mo = $("[data-more]", body); if (mo) mo.onclick = () => load(body, true);
    $$("[data-ver]", body).forEach((b) => b.onclick = () => {
      const [mid, vid] = b.dataset.ver.split(":");
      const m = st.items.find((x) => String(x.id) === mid); const ver = m.versions.find((x) => String(x.id) === vid);
      installSheet(m, ver, () => { ver.installed = true; });
    });
  };
  openSheet("CivitAI", "", (body) => load(body), () => { if (location.hash.startsWith("#more/loras")) viewLoras(); });
}
function civItem(m) {
  return `<div class="civ-item">${m.image ? `<img src="${esc(m.image)}" alt="" loading="lazy">` : `<span class="ph"></span>`}
    <div class="grow"><h3>${esc(m.name)}</h3><div class="faint" style="font-size:12px">${esc(m.type)}${m.creator ? ` · ${esc(m.creator)}` : ""}</div>
      ${m.versions.slice(0, 4).map((v) => `<div class="civ-ver"><span class="v">${esc(v.name)} · ${esc(v.base_model || "")} · ${v.files.length} file${v.files.length === 1 ? "" : "s"}</span>
        <button class="btn small ${v.installed ? "" : "primary"}" data-ver="${m.id}:${v.id}">${v.installed ? "Installed" : "Install"}</button></div>`).join("")}
    </div></div>`;
}
function installSheet(m, v, onDone) {
  const isCk = m.type === "Checkpoint";
  openSheet(`${isCk ? "Base model" : "LoRA"}: ${m.name}`, `
    <p class="muted">${esc(v.name)} · ${esc(v.base_model || "")}</p>
    ${v.base_model && v.base_model !== "MiniMax H3" ? `<p class="note warn">This is a ${esc(v.base_model)} model, not MiniMax H3. It will not work here.</p>` : ""}
    <h3 style="margin-top:12px">Files</h3>
    ${v.files.map((f, i) => `<div class="file-row"><label class="switch"><input type="${isCk ? "radio" : "checkbox"}" name="pick" data-pick="${i}" ${isCk ? (i === 0 ? "checked" : "") : f.suggested ? "checked" : ""}><span></span></label>
      <div class="grow">${esc(f.name)}<div class="faint">${f.size_kb ? bytes(f.size_kb * 1024) : ""}${f.primary ? " · primary" : ""}</div></div>
      <span class="fam ${f.family}">${f.family === "ref2v" ? "R2V" : f.family === "fl2v" ? "T2V/I2V" : "All modes"}</span>${f.role === "helper" ? `<span class="fam helper">helper</span>` : ""}</div>`).join("")}
    <p class="note">${isCk ? "Installs into the base-model picker." : "Modes were guessed from file names; you can change them later. Unticked files look like alternate copies of the same LoRA. Only one main file is ever loaded per mode."}</p>
    ${(v.trained_words || []).length ? `<p class="note">Triggers: ${esc(v.trained_words.slice(0, 8).join(", "))}</p>` : ""}
    <div class="sheet-foot"><button class="btn primary" id="inst">${v.installed ? "Update install" : "Install"}</button></div>`, (body) => {
    $("#inst", body).onclick = async () => {
      const picked = $$("[data-pick]", body).filter((x) => x.checked).map((x) => v.files[+x.dataset.pick].id);
      if (!picked.length) return toast("Pick at least one file", true);
      try {
        if (isCk) await api("/api/checkpoints", { body: { version_id: v.id, file_id: picked[0], label: `${m.name} · ${v.name}` } });
        else await api("/api/loras", { body: { version_id: v.id, file_ids: picked } });
        toast("Downloading in the background"); onDone && onDone(); closeSheet();
        S.boot = await api("/api/boot");
      } catch (e) { fail(e); }
    };
  });
}

// ------------------------------------------------------------------ MORE → RECIPES

function viewRecipes() {
  const famId = S.recipeFam || "ref2v";
  const fam = S.boot.recipes[famId];
  const v = $("#view");
  const cmpN = fam.compare.length;
  v.innerHTML = `<div class="page">
    <div class="page-head"><a class="icon-btn" href="#more" aria-label="Back" style="transform:scaleX(-1)">${ICON.chev}</a><h1>Recipes</h1><span class="spacer"></span>
      <button class="btn small primary" id="rNew">${ICON.plus} New</button></div>
    <div class="seg" style="margin-bottom:12px">${[["fl2v", "Text & Image"], ["ref2v", "Reference"]].map(([k, n]) => `<button data-rf="${k}" class="${famId === k ? "on" : ""}">${n}</button>`).join("")}</div>
    <p class="note" style="margin-top:0"><b>Create</b> shows the recipe in Speed. <b>Compare</b> renders every ticked recipe on one seed (${cmpN < 2 ? "tick at least two" : `${cmpN} ticked`}). Tap a recipe to see all its values, duplicate it, or edit your own.</p>
    <div class="list recipes">${fam.recipes.map((r) => {
      const shown = !fam.hidden.includes(r.id);
      return `<div class="rrow">
        <button class="rinfo" data-redit="${esc(r.id)}"><h3>${esc(r.label)}${r.id === fam.default ? ` <span class="status done">default</span>` : ""}${r.id === fam.av_recipe ? ` <span class="status">video/audio</span>` : ""}${r.builtin ? "" : ` <span class="status">yours</span>`}</h3>
          <div class="sub">${esc(recipeSummary(r))}</div></button>
        <div class="rtoggles">
          <label class="rt"><span>Create</span><span class="switch"><input type="checkbox" data-vis="${esc(r.id)}" ${shown ? "checked" : ""} ${r.id === fam.default ? "disabled" : ""}><span></span></span></label>
          <label class="rt"><span>Compare</span><span class="switch"><input type="checkbox" data-cmp="${esc(r.id)}" ${fam.compare.includes(r.id) ? "checked" : ""}><span></span></span></label>
        </div></div>`;
    }).join("")}</div>
    <div class="section-title"><h2>Choices</h2></div>
    <div class="list">
      <div><div class="grow">Default<div class="sub">What Create starts on</div></div>
        <select id="rDefault" style="width:auto">${fam.recipes.filter((r) => !fam.hidden.includes(r.id)).map((r) => `<option value="${esc(r.id)}" ${r.id === fam.default ? "selected" : ""}>${esc(r.label)}</option>`).join("")}</select></div>
      ${famId === "ref2v" ? `<div><div class="grow">With video or audio references<div class="sub">Create switches to this recipe when you add one, and back when you remove it</div></div>
        <select id="rAv" style="width:auto"><option value="">Don't switch</option>${fam.recipes.map((r) => `<option value="${esc(r.id)}" ${r.id === fam.av_recipe ? "selected" : ""}>${esc(r.label)}</option>`).join("")}</select></div>` : ""}
    </div>
  </div>`;
  const choose = async (patch) => {
    try { S.boot.recipes[famId] = await api(`/api/recipes/${famId}/choices`, { method: "PUT", body: patch }); viewRecipes(); }
    catch (e) { fail(e); viewRecipes(); }
  };
  $$("[data-rf]").forEach((b) => b.onclick = () => { S.recipeFam = b.dataset.rf; viewRecipes(); });
  $("#rNew").onclick = () => recipeEditor(famId, null);
  $$("[data-redit]").forEach((b) => b.onclick = () => recipeEditor(famId, fam.recipes.find((r) => r.id === b.dataset.redit)));
  $$("[data-vis]").forEach((el) => el.onchange = () => {
    const hidden = fam.hidden.filter((h) => h !== el.dataset.vis);
    if (!el.checked) hidden.push(el.dataset.vis);
    choose({ hidden });
  });
  $$("[data-cmp]").forEach((el) => el.onchange = () => {
    const compare = fam.recipes.map((r) => r.id).filter((id) => (id === el.dataset.cmp ? el.checked : fam.compare.includes(id)));
    choose({ compare });
  });
  $("#rDefault").onchange = (e) => choose({ default: e.target.value });
  const av = $("#rAv"); if (av) av.onchange = (e) => choose({ av_recipe: e.target.value || null });
}

async function viewSystem() {
  const v = $("#view");
  let d; try { d = await api("/api/system"); } catch (e) { fail(e); return; }
  if (!location.hash.startsWith("#more/system")) return;
  const mem = d.memory; const frac = mem.fraction;
  const models = Object.values(d.models);
  const st = d.settings;
  v.innerHTML = `<div class="page">
    <div class="page-head"><a class="icon-btn" href="#more" aria-label="Back" style="transform:scaleX(-1)">${ICON.chev}</a><h1>System</h1></div>
    <div class="section-title" style="margin-top:0"><h2>Memory</h2><span class="faint num">${mem.used_gb} / ${mem.limit_gb} GB</span></div>
    <div class="meter ${frac > 0.9 ? "crit" : frac > 0.75 ? "hi" : ""}"><i style="width:${Math.round(frac * 100)}%"></i></div>
    <p class="note">${mem.limited ? "Container limit (what the OOM killer uses)." : "No container limit found; showing host memory."} ${d.gpu.name ? `GPU ${esc(d.gpu.name)}: ${d.gpu.vram_used_gb} of ${d.gpu.vram_total_gb} GB VRAM.` : ""}</p>
    <div class="row" style="margin-top:8px"><button class="btn small" data-act="free" data-unload="0">Drop cache</button><button class="btn small" data-act="free" data-unload="1">Unload models</button><button class="btn small" data-act="restart-comfy">Restart ComfyUI</button></div>

    <div class="section-title"><h2>Models</h2><button class="btn small ghost" data-act="provision">Check again</button></div>
    <div class="list">${models.map((m) => `<div><span class="dot ${m.present ? "ok" : m.state === "failed" ? "bad" : m.state === "downloading" ? "warn" : ""}"></span>
      <div class="grow"><div style="font-size:14px;overflow-wrap:anywhere">${esc(m.file.split("/").pop())}</div><div class="sub">${m.present ? "ready" : m.state === "downloading" ? `downloading ${m.total ? Math.round(100 * (m.done || 0) / m.total) + "%" : ""}` : m.state === "failed" ? `failed: ${esc(m.error || "")}` : m.group === "eros" ? "optional" : esc(m.state)}</div></div></div>`).join("")}</div>

    <div class="section-title"><h2>Services</h2></div>
    <div class="list">${Object.entries(d.services).filter(([, s]) => s && typeof s === "object").map(([k, s]) => `<div><span class="dot ${s.alive ? "ok" : "bad"}"></span><div class="grow">${esc(k)}</div><span class="sub">${s.restarts ? `${s.restarts} restarts` : ""}</span></div>`).join("")}
      <div><span class="dot ${d.services.sage ? "ok" : ""}"></span><div class="grow">SageAttention</div><span class="sub">${d.services.sage ? "on" : "off"}</span></div></div>
    <p class="note faint" style="overflow-wrap:anywhere">ComfyUI flags: ${esc(d.comfy_args.join(" "))}</p>

    <div class="section-title"><h2>Keys</h2></div>
    <div class="list">
      <div><span class="dot ${d.prompting.key ? "ok" : "bad"}"></span><div class="grow">OpenRouter (Auto prompts)<div class="sub">${d.prompting.key ? esc(d.prompting.key_source) : "not set"}</div></div><button class="btn small" id="keyBtn">${d.prompting.key ? "Change" : "Add"}</button></div>
      <div><span class="dot ${d.civitai_token ? "ok" : "bad"}"></span><div class="grow">CivitAI<div class="sub">${d.civitai_token ? "CIVITAI_TOKEN set" : "set CIVITAI_TOKEN on the pod"} · ${esc(d.civitai_domain)}</div></div></div>
      <div><span class="dot ${d.password ? "ok" : "warn"}"></span><div class="grow">Studio password<div class="sub">${d.password ? "on" : "off. Anyone with the pod URL can use Studio. Set MMH3_PASSWORD on the pod."}</div></div></div>
    </div>

    <div class="section-title"><h2>Behaviour</h2></div>
    <div class="list">
      <div><div class="grow">Unload when switching model family<div class="sub">T2V/I2V ↔ R2V or base model. Keeps RAM flat.</div></div><label class="switch"><input type="checkbox" data-set="memory.unload_on_family_switch" ${st["memory.unload_on_family_switch"] ? "checked" : ""}><span></span></label></div>
      <div><div class="grow">Drop cache after a job above<div class="sub">fraction of the container's RAM</div></div><input type="number" step="0.05" min="0.3" max="0.95" style="width:86px" data-set="memory.trim_after_job_above" value="${st["memory.trim_after_job_above"]}"></div>
      <div><div class="grow">Delete VHS's silent duplicate<div class="sub">keeps only the mp4 with sound</div></div><label class="switch"><input type="checkbox" data-set="output.delete_silent_twin" ${st["output.delete_silent_twin"] ? "checked" : ""}><span></span></label></div>
      <div><div class="grow">Video quality (CRF)<div class="sub">lower = better and bigger</div></div><input type="number" min="8" max="30" style="width:86px" data-set="output.crf" value="${st["output.crf"]}"></div>
      <div><div class="grow">Auto prompt &amp; Tweak model<div class="sub">any OpenRouter model id</div></div><input type="text" style="width:170px" data-set="prompting.model" value="${esc(st["prompting.model"] || "")}"></div>
      <div><div class="grow">CivitAI site</div><select style="width:150px" data-set="civitai.domain">${["civitai.red", "civitai.com"].map((x) => `<option ${d.civitai_domain.endsWith(x) ? "selected" : ""}>${x}</option>`).join("")}</select></div>
    </div>
  </div>`;
  $$("[data-act]").forEach((b) => b.onclick = async () => {
    const a = b.dataset.act;
    try {
      if (a === "free") await api("/api/system/free", { body: { unload: b.dataset.unload === "1" } });
      if (a === "restart-comfy") { if (!confirm("Restart ComfyUI? A running render will be lost.")) return; await api("/api/system/restart-comfy", { body: {} }); }
      if (a === "provision") await api("/api/system/provision", { body: { groups: ["core"] } });
      toast("Done"); setTimeout(viewSystem, 1500);
    } catch (e) { fail(e); }
  });
  $$("[data-set]").forEach((el) => el.onchange = () => {
    const val = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
    api("/api/system/settings", { method: "PUT", body: { [el.dataset.set]: val } }).then(() => toast("Saved")).catch(fail);
  });
  $("#keyBtn").onclick = () => openSheet("OpenRouter key", `<p class="note">Stored on your volume (readable only by the pod). A pod secret named OPENROUTER_API_KEY takes priority.</p>
    <input type="password" id="k" placeholder="sk-or-…" autocomplete="off"><div class="sheet-foot"><button class="btn primary" id="kSave">Save key</button></div>`, (body) => {
    $("#kSave", body).onclick = () => api("/api/system/key", { body: { key: $("#k", body).value } }).then(async () => { toast("Key saved"); S.boot = await api("/api/boot"); closeSheet(); viewSystem(); }).catch(fail);
  });
}

async function promptsSheet() {
  let d; try { d = await api("/api/system/prompts"); } catch (e) { return fail(e); }
  let cur = "t2v_auto";
  const names = { t2v_auto: "Text", i2v_auto: "Image · start", fl2v_auto: "Image · start + end", l2v_auto: "Image · end", r2v_auto: "Reference", refine: "Tweak" };
  const about = {
    t2v_auto: "Auto in Text mode (MiniMax T2VA).",
    i2v_auto: "Auto in Image mode with a start frame only (I2VA).",
    fl2v_auto: "Auto in Image mode with start and end frames (FL2VA).",
    l2v_auto: "Auto in Image mode with an end frame only (L2VA).",
    r2v_auto: "Auto in Reference mode (full-reference format).",
    refine: "Tweak: edits a prompt from a short request. The mode's own instructions are appended as its rules.",
  };
  const draw = (body) => {
    const stale = d.stale[cur];
    body.innerHTML = `<div class="chips scroll" style="margin-bottom:10px">${Object.entries(names).map(([k, n]) => `<button data-p="${k}" class="chip ${cur === k ? "on" : ""}">${n}</button>`).join("")}</div>
      <p class="note" style="margin:0 0 8px">${about[cur]}</p>
      ${stale ? `<div class="note warn stale">Your earlier edit of this prompt was made for an older version. The updated default, written from MiniMax's prompt guide, is in use now. <button class="btn small" id="pOld">Load my old version</button></div>` : ""}
      <textarea class="code" id="pt">${esc(d.prompts[cur] || "")}</textarea>
      <div class="sheet-foot"><button class="btn" id="pReset">Restore default</button><button class="btn primary" id="pSave">Save</button></div>`;
    $$("[data-p]", body).forEach((b) => b.onclick = () => { cur = b.dataset.p; draw(body); });
    $("#pSave", body).onclick = () => api("/api/system/prompts", { method: "PUT", body: { name: cur, text: $("#pt", body).value } }).then(() => { d.prompts[cur] = $("#pt", body).value; delete d.stale[cur]; toast("Saved"); draw(body); }).catch(fail);
    $("#pReset", body).onclick = () => { $("#pt", body).value = d.defaults[cur] || ""; toast("Default loaded. Save to use it."); };
    const old = $("#pOld", body); if (old) old.onclick = () => { $("#pt", body).value = stale; toast("Old version loaded. Save to use it."); };
  };
  openSheet("Auto prompt instructions", "", draw);
}
function logsSheet(name) {
  const draw = async (body) => {
    body.innerHTML = `<div class="chips" style="margin-bottom:10px">${["comfyui", "studio", "provision", "supervisor"].map((n) => `<button class="chip ${n === name ? "on" : ""}" data-l="${n}">${n}</button>`).join("")}</div><pre class="logbox" id="lb">Loading…</pre>`;
    $$("[data-l]", body).forEach((b) => b.onclick = () => { name = b.dataset.l; draw(body); });
    try { const t = await api(`/api/logs/${name}?lines=400`); const lb = $("#lb", body); lb.textContent = t || "(empty)"; lb.scrollTop = lb.scrollHeight; } catch (e) { fail(e); }
  };
  openSheet("Logs", "", draw);
}

// ------------------------------------------------------------------ live state polling

let pollTimer = null;
let lastRunningId = null;
function pollSoon() { clearTimeout(pollTimer); pollTimer = setTimeout(poll, 150); }
async function poll() {
  clearTimeout(pollTimer);
  try {
    const L = await api("/api/state");
    const prev = S.live; S.live = L;
    updateChrome(L);
    const running = L.pending.find((j) => j.status === "running");
    if (lastRunningId && (!running || running.id !== lastRunningId)) {
      const fin = L.finished.find((j) => j.id === lastRunningId);
      if (fin && fin.status === "done") { toast("Clip finished"); refreshOutputs(); }
      if (fin && fin.status === "failed") toast(`Failed: ${fin.error}`, true);
    }
    lastRunningId = running ? running.id : null;
    if (S.route === "queue") VIEWS.queue();
    if (S.route === "create" && prev && JSON.stringify(prev.families) !== JSON.stringify(L.families) && !document.activeElement.matches("textarea,input")) render();
  } catch { $("#conn").classList.remove("on"); }
  const busy = S.live && S.live.pending.length;
  pollTimer = setTimeout(poll, document.hidden ? 8000 : busy ? 1500 : 4000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollSoon(); });

function updateChrome(L) {
  $("#conn").classList.toggle("on", !!L.comfy);
  $("#conn").title = L.comfy ? "ComfyUI connected" : "ComfyUI not connected (starting?)";
  const active = L.pending.find((j) => ["running", "unloading", "drafting", "waiting"].includes(j.status));
  const queued = L.pending.length;
  const b = $("#queueBadge"); b.hidden = !queued; b.textContent = queued;
  const t = $("#tally");
  if (!active) { t.hidden = true; return; }
  t.hidden = false;
  t.classList.toggle("idle", active.status !== "running");
  const img = $("#tallyImg");
  if (active.preview_seq > 0) { const src = `/api/jobs/${active.id}/preview?s=${active.preview_seq}`; if (img.getAttribute("src") !== src) img.src = src; } else img.removeAttribute("src");
  $("#tallyText").textContent = active.status === "running"
    ? (active.eta != null ? `${clock(active.eta)} left` : active.stage === "sampling" && active.total_steps ? `${active.step}/${active.total_steps}` : cap(active.stage || "starting"))
    : cap(active.stage || active.status);
  t.onclick = () => (location.hash = "#queue");
}

// ------------------------------------------------------------------ boot

function showLogin() {
  $("#view").innerHTML = `<div class="login"><h1>MMH3 Studio</h1><p class="muted">Enter the Studio password set on the pod.</p>
    <form id="lf" class="stack"><input type="password" id="pw" autocomplete="current-password" placeholder="Password"><button class="btn primary" style="width:100%">Open Studio</button></form></div>`;
  $("#tabs").hidden = true;
  $("#lf").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/login", { body: { password: $("#pw").value } }); location.reload(); } catch (err) { fail(err); } };
}

async function start() {
  try {
    S.boot = await api("/api/boot");
  } catch (e) {
    if (e.message !== "login required") $("#view").innerHTML = `<div class="page"><div class="empty"><h2>Studio isn't answering</h2><p>${esc(e.message)}</p></div></div>`;
    return;
  }
  try {
    const remote = await api("/api/form");
    if (remote && remote.updated && remote.updated > (F.updated || 0)) F = { ...DEFAULT_FORM, ...remote };
  } catch {}
  loadLoras().catch(() => {});
  route();
  if (S.route !== "library") refreshOutputs();
  poll();
}
start();
