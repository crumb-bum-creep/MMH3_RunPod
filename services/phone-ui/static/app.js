"use strict";

const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];

const state = {
  tab: "generate",
  mode: "t2v",
  promptMode: "auto",
  refs: [],
  startingImage: null,
  selectedLoras: new Map(),
  loraCatalog: [],
  assetCatalog: [],
  templates: {},
  outputs: [],
  info: null,
  openOutputs: new Set(),
  outputsSignature: "",
  refKind: "image",
  uiProfiles: {},
  uiUpdatedAt: 0,
  draftTimer: null,
  restoringDraft: false,
  assetPickerMode: null,
  assetPickerKind: "all",
  studioProjects: [],
  studioProject: null,
  studioMeta: {subject_types:[],camera_motions:[],framing_presets:[]},
  studioSavedSubjects: [],
  studioSaveTimer: null,
  studioLoading: false,
  studioBlockingShotId: null,
  studioBlockingSelectedId: null,
};

function esc(v=""){
  return String(v).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
function bytes(n){
  n=Number(n||0);
  if(!n) return "0 B";
  const u=["B","KB","MB","GB","TB"]; let i=0;
  while(n>=1024&&i<u.length-1){n/=1024;i++;}
  return n.toFixed(i<2?0:1)+" "+u[i];
}
function pct(n){ return Math.round(Number(n||0))+"%"; }
function mediaUrl(file){ return "/media/output/"+file.split("/").map(encodeURIComponent).join("/"); }
function toast(msg){
  const el=$("#toast"); el.textContent=msg; el.classList.add("show");
  clearTimeout(toast.t); toast.t=setTimeout(()=>el.classList.remove("show"),2200);
}
function msg(el,text,type=""){ el.textContent=text||""; el.className="message"+(type?" "+type:""); }

function profileKey(mode=state.mode,promptMode=state.promptMode){ return mode+":"+promptMode; }
function assetByFile(file){ return state.assetCatalog.find(x=>x.file===file)||null; }
function assetName(file){
  const a=assetByFile(file);
  return a?.display_name||a?.nickname||file?.split("/").pop()||file||"";
}
function assetThumb(file){
  const a=assetByFile(file);
  return a?.thumb||null;
}
function assetKind(file){
  const a=assetByFile(file);
  if(a?.kind)return a.kind;
  const ext=String(file||"").split(".").pop().toLowerCase();
  return ["png","jpg","jpeg","webp","bmp"].includes(ext)?"image":
    ["mp4","mov","mkv","webm","avi"].includes(ext)?"video":"audio";
}
function refTag(index){
  const ref=state.refs[index], letter={image:"P",video:"V",audio:"A"}[ref?.kind]||"?";
  const ordinal=state.refs.slice(0,index+1).filter(x=>x.kind===ref.kind).length;
  return "<"+letter+ordinal+">";
}
function captureDraft(){
  return {
    prompt:$("#prompt")?.value||"",
    aspect_ratio:$("#aspect")?.value||"9:16 (Portrait Widescreen)",
    megapixels:Number($("#mp")?.value||0.7),
    duration:Number($("#duration")?.value||5),
    seed:Number($("#seed")?.value||1),
    randomize_seed:$("#randomSeed")?.checked!==false,
    starting_image:state.startingImage||null,
    refs:structuredClone(state.refs||[]),
    loras:selectedLoraArray()
  };
}
function mirrorUiState(){
  const payload={
    active:{mode:state.mode,prompt_mode:state.promptMode},
    profiles:state.uiProfiles,
    updated_at:Date.now()/1000
  };
  state.uiUpdatedAt=payload.updated_at;
  try{localStorage.setItem("mmh3.uiState.v2",JSON.stringify(payload));}catch{}
  return payload;
}
function stashCurrentDraft(){
  if(state.restoringDraft)return;
  state.uiProfiles[profileKey()]=captureDraft();
  mirrorUiState();
  scheduleDraftSave();
}
function scheduleDraftSave(){
  clearTimeout(state.draftTimer);
  state.draftTimer=setTimeout(async()=>{
    try{
      const payload=mirrorUiState();
      const d=await api("/api/ui-state",{method:"PUT",body:payload});
      state.uiUpdatedAt=Number(d.updated_at||state.uiUpdatedAt);
    }catch{}
  },450);
}
function applyDraft(v={}){
  state.restoringDraft=true;
  $("#prompt").value=v.prompt||v.prompt_idea||"";
  $("#aspect").value=v.aspect_ratio||"9:16 (Portrait Widescreen)";
  $("#mp").value=v.megapixels!=null?v.megapixels:0.7;
  $("#duration").value=v.duration!=null?v.duration:5;
  $("#seed").value=v.seed!=null?v.seed:1;
  $("#randomSeed").checked=v.randomize_seed!==false;
  state.startingImage=v.starting_image||null;
  state.refs=Array.isArray(v.refs)?structuredClone(v.refs):[];
  state.selectedLoras.clear();
  (v.loras||[]).forEach(x=>{if(x.filename&&!isCoreWorkflowLora(x))state.selectedLoras.set(x.filename,{...x});});
  renderStartingImage();
  renderRefs();
  renderSelectedLoras();
  state.restoringDraft=false;
}
function updateDraftLabel(){
  const el=$("#draftLabel");
  if(el)el.textContent=state.mode.toUpperCase()+" "+(state.promptMode==="auto"?"Auto":"Custom")+" draft · autosaved";
}
function activateProfile(mode,promptMode){
  if(!state.restoringDraft)stashCurrentDraft();
  state.mode=mode;
  state.promptMode=promptMode;
  setSegment($("#modeSeg"),mode);
  setSegment($("#promptModeSeg"),promptMode);
  $("#i2vBlock").hidden=mode!=="i2v";
  $("#r2vBlock").hidden=mode!=="r2v";
  $("#promptLabel").textContent=promptMode==="auto"?"Prompt idea":"Custom prompt";
  applyDraft(state.uiProfiles[profileKey()]||{});
  updateDraftLabel();
  refreshInputOptions();
  mirrorUiState();
  scheduleDraftSave();
}
async function loadUiState(){
  let remote={active:{mode:"t2v",prompt_mode:"auto"},profiles:{},updated_at:0};
  try{ remote=await api("/api/ui-state"); }catch{}
  let local=null;
  try{local=JSON.parse(localStorage.getItem("mmh3.uiState.v2")||"null");}catch{}
  const source=local&&Number(local.updated_at||0)>Number(remote.updated_at||0)?local:remote;
  state.uiProfiles=source.profiles&&typeof source.profiles==="object"?source.profiles:{};
  state.uiUpdatedAt=Number(source.updated_at||0);
  const mode=["t2v","i2v","r2v"].includes(source.active?.mode)?source.active.mode:"t2v";
  const pm=["auto","custom"].includes(source.active?.prompt_mode)?source.active.prompt_mode:"auto";
  state.restoringDraft=true;
  state.mode=mode; state.promptMode=pm;
  setSegment($("#modeSeg"),mode); setSegment($("#promptModeSeg"),pm);
  $("#i2vBlock").hidden=mode!=="i2v"; $("#r2vBlock").hidden=mode!=="r2v";
  $("#promptLabel").textContent=pm==="auto"?"Prompt idea":"Custom prompt";
  applyDraft(state.uiProfiles[profileKey()]||{});
  state.restoringDraft=false;
  updateDraftLabel();
  mirrorUiState();
  scheduleDraftSave();
}

async function api(path, options={}){
  const opts={...options, headers:{...(options.headers||{})}};
  if(opts.body && !(opts.body instanceof FormData) && typeof opts.body!=="string"){
    opts.headers["Content-Type"]="application/json";
    opts.body=JSON.stringify(opts.body);
  }
  const r=await fetch(path,{cache:"no-store",...opts});
  const text=await r.text();
  const contentType=(r.headers.get("content-type")||"").toLowerCase();
  if(contentType.includes("text/html") || /^\s*<!doctype html/i.test(text)){
    throw new Error("RunPod proxy could not reach the MMH3 service. Retry in a few seconds; if it persists, check the Phone UI and Comfy logs.");
  }
  let data=null;
  try{ data=text?JSON.parse(text):{}; }catch{ data={message:text}; }
  if(!r.ok){
    const detail=(data&&((data.error&&data.error.message)||data.message))||text||r.statusText;
    throw new Error(detail);
  }
  return data;
}

function switchTab(name){
  state.tab=name;
  $$("#tabs button").forEach(b=>b.classList.toggle("active",b.dataset.tab===name));
  $$(".tab").forEach(s=>s.classList.toggle("active",s.id==="tab-"+name));
  if(name==="outputs") refreshOutputs(true);
  if(name==="loras") loadLoras();
  if(name==="assets") loadAssets();
  if(name==="studio"){ loadAssets(false).then(()=>loadStudioIndex()).catch(()=>loadStudioIndex()); }
  if(name==="system"){ refreshInfo(); loadPrompts(); }
}
function setSegment(host, value){
  $$("button",host).forEach(b=>b.classList.toggle("active",b.dataset.value===value));
}
function setMode(mode){ activateProfile(mode,state.promptMode); }
function setPromptMode(mode){ activateProfile(state.mode,mode); }

async function uploadFile(file){
  const fd=new FormData(); fd.append("file",file,file.name||"upload.bin");
  return api("/api/upload",{method:"POST",body:fd});
}

async function refreshInputOptions(){
  return loadAssets(false);
}

async function renameAsset(file){
  if(!file)return;
  const current=assetByFile(file);
  const next=prompt("Asset nickname",current?.nickname||"");
  if(next===null)return;
  try{
    await api("/api/assets/meta",{method:"PUT",body:{file,nickname:next}});
    await loadAssets(false);
    toast(next.trim()?"Asset nickname saved":"Asset nickname cleared");
  }catch(e){toast(e.message);}
}

function renderStartingImage(){
  const host=$("#startSelected");
  if(!host)return;
  const file=state.startingImage;
  if(!file){
    host.className="selected-asset-slot muted";
    host.innerHTML="No starting image selected.";
    return;
  }
  const thumb=assetThumb(file);
  host.className="selected-asset-slot";
  host.innerHTML=`<div class="selected-asset-inner">
    <div class="asset-mini">
      ${thumb?`<img src="${esc(thumb)}" alt="">`:'<div class="asset-icon">IMG</div>'}
      <div class="asset-mini-text"><strong>${esc(assetName(file))}</strong><div class="muted">${esc(file)}</div></div>
    </div>
    <button class="ghost small rename-start-asset" type="button">Name</button>
  </div>`;
  $(".rename-start-asset",host).onclick=()=>renameAsset(file);
}

function moveRefWithinKind(index,delta){
  const ref=state.refs[index];
  if(!ref)return;
  const same=state.refs.map((x,i)=>x.kind===ref.kind?i:-1).filter(i=>i>=0);
  const pos=same.indexOf(index), next=same[pos+delta];
  if(next==null)return;
  [state.refs[index],state.refs[next]]=[state.refs[next],state.refs[index]];
  renderRefs(); stashCurrentDraft();
}

function renderRefs(){
  const host=$("#refs");
  if(!host)return;
  if(!state.refs.length){host.innerHTML='<div class="muted">No references selected.</div>';return;}
  host.innerHTML=state.refs.map((r,i)=>{
    const thumb=r.kind==="image"?assetThumb(r.file):null;
    const same=state.refs.map((x,j)=>x.kind===r.kind?j:-1).filter(j=>j>=0);
    const pos=same.indexOf(i);
    return `<div class="ref-card ref-rich" data-i="${i}">
      <div class="asset-mini">
        ${thumb?`<img src="${esc(thumb)}" alt="">`:`<div class="asset-icon">${esc(({image:"IMG",video:"VID",audio:"AUD"}[r.kind]||"?"))}</div>`}
        <div class="asset-mini-text">
          <div class="ref-title"><span class="ref-slot">${esc(refTag(i))}</span><strong>${esc(assetName(r.file))}</strong></div>
          <div class="muted filename-line">${esc(r.file)}</div>
          ${r.kind==="video"?`<label class="check compact-check"><input class="soundtrack" type="checkbox" ${r.use_soundtrack!==false?"checked":""}> Use soundtrack</label>`:""}
        </div>
      </div>
      <div class="ref-actions">
        <button class="ghost small rename-ref" type="button" aria-label="Nickname asset">✎</button>
        <button class="ghost small move-up" type="button" ${pos===0?"disabled":""} aria-label="Move up">↑</button>
        <button class="ghost small move-down" type="button" ${pos===same.length-1?"disabled":""} aria-label="Move down">↓</button>
        <button class="danger ghost small remove-ref" type="button" aria-label="Remove">×</button>
      </div>
    </div>`;
  }).join("");
  $$(".ref-card",host).forEach(row=>{
    const i=Number(row.dataset.i);
    $(".remove-ref",row).onclick=()=>{state.refs.splice(i,1);renderRefs();stashCurrentDraft();};
    $(".rename-ref",row).onclick=()=>renameAsset(state.refs[i].file);
    $(".move-up",row).onclick=()=>moveRefWithinKind(i,-1);
    $(".move-down",row).onclick=()=>moveRefWithinKind(i,1);
    const cb=$(".soundtrack",row);
    if(cb)cb.onchange=()=>{state.refs[i].use_soundtrack=cb.checked;stashCurrentDraft();};
  });
}

function canAddRef(kind){
  const cap={image:9,video:3,audio:3}[kind];
  return state.refs.filter(x=>x.kind===kind).length<cap;
}
function addRef(kind,file,useSoundtrack=true){
  if(!canAddRef(kind)){toast("Reference limit reached for "+kind);return false;}
  state.refs.push({kind,file,...(kind==="video"?{use_soundtrack:useSoundtrack}:{})});
  renderRefs(); stashCurrentDraft(); return true;
}
async function addUploadedReference(kind,file){
  try{
    const d=await uploadFile(file);
    await loadAssets(false);
    addRef(kind,d.file,true);
  }catch(e){toast("Upload failed: "+e.message);}
}

function isCoreWorkflowLora(item){
  const file=String(item?.filename||"").toLowerCase();
  return file.includes("minimax_h3_fl2v_lightx2v_turbo_4step")||file.includes("minimax_h3_ref2v_turbo_4step");
}
function sortedLoraCatalog(){
  return state.loraCatalog.filter(x=>!isCoreWorkflowLora(x)).sort((a,b)=>
    String(a.nickname||a.model_name||a.version_name||a.filename).localeCompare(
      String(b.nickname||b.model_name||b.version_name||b.filename),undefined,{sensitivity:"base"}
    )
  );
}
function selectedLoraArray(){
  return [...state.selectedLoras.values()].map(x=>({
    filename:x.filename,
    strength:Number(x.strength ?? x.recommended_strength ?? 1),
    nickname:x.nickname||x.model_name||x.version_name||x.filename
  }));
}
function loraOptions(current){
  const items=sortedLoraCatalog();
  const missing=current&&!items.some(x=>x.filename===current)
    ?`<option value="${esc(current)}" selected>Missing · ${esc(current)}</option>`:"";
  return missing+items.map(x=>{
    const name=x.nickname||x.model_name||x.version_name||x.filename;
    return `<option value="${esc(x.filename)}" ${x.filename===current?"selected":""}>${esc(name)}</option>`;
  }).join("");
}
function renderSelectedLoras(){
  const host=$("#selectedLoras"), arr=selectedLoraArray();
  if(!host)return;
  $("#loraCount").textContent=String(arr.length);
  const add=$("#addGenerationLora");
  if(add){
    const available=sortedLoraCatalog().filter(x=>!state.selectedLoras.has(x.filename));
    add.innerHTML='<option value="">+ Add LoRA…</option>'+available.map(x=>`<option value="${esc(x.filename)}">${esc(x.nickname||x.model_name||x.version_name||x.filename)}</option>`).join("");
    add.value="";
  }
  if(!arr.length){host.innerHTML='<div class="muted">No custom LoRAs selected.</div>';return;}
  host.innerHTML=arr.map(x=>`<div class="gen-lora-row" data-file="${esc(x.filename)}">
    <select class="gen-lora-select" aria-label="LoRA">${loraOptions(x.filename)}</select>
    <input class="gen-lora-strength" aria-label="Strength" type="number" step=".05" min="-3" max="3" value="${esc(Number(x.strength).toFixed(2))}">
    <button class="danger ghost remove-gen-lora" type="button" aria-label="Remove LoRA">×</button>
  </div>`).join("");
  $$(".gen-lora-row",host).forEach(row=>{
    const oldFile=row.dataset.file;
    $(".gen-lora-select",row).onchange=e=>{
      const newFile=e.target.value;
      if(newFile!==oldFile && state.selectedLoras.has(newFile)){
        toast("That LoRA is already selected");
        e.target.value=oldFile;
        return;
      }
      const old=state.selectedLoras.get(oldFile)||{};
      const item=state.loraCatalog.find(x=>x.filename===newFile)||old;
      state.selectedLoras.delete(oldFile);
      state.selectedLoras.set(newFile,{...item,strength:Number(item.recommended_strength??1)});
      renderSelectedLoras(); stashCurrentDraft();
    };
    $(".gen-lora-strength",row).onchange=e=>{
      const item=state.selectedLoras.get(oldFile);
      if(item){item.strength=Number(e.target.value||item.recommended_strength||1);stashCurrentDraft();}
    };
    $(".remove-gen-lora",row).onclick=()=>{state.selectedLoras.delete(oldFile);renderSelectedLoras();stashCurrentDraft();};
  });
}
function addGenerationLora(file){
  const next=sortedLoraCatalog().find(x=>x.filename===file);
  if(!next)return;
  if(state.selectedLoras.has(file)){toast("That LoRA is already selected");return;}
  state.selectedLoras.set(next.filename,{...next,strength:Number(next.recommended_strength??1)});
  renderSelectedLoras(); stashCurrentDraft();
}

function renderLoras(){
  const host=$("#loraLibrary");
  if(!host)return;
  const q=($("#loraSearch")?.value||"").trim().toLowerCase();
  const sort=$("#loraSort")?.value||"alpha";
  let items=state.loraCatalog.filter(x=>!isCoreWorkflowLora(x)).filter(x=>!q||[
    x.nickname,x.model_name,x.version_name,x.filename,...(x.tags||[]),...(x.trigger_words||[])
  ].join(" ").toLowerCase().includes(q));
  if(sort==="alpha")items=[...items].sort((a,b)=>String(a.nickname||a.model_name||a.version_name||a.filename).localeCompare(String(b.nickname||b.model_name||b.version_name||b.filename),undefined,{sensitivity:"base"}));
  if(!items.length){host.innerHTML='<div class="muted">No matching LoRAs.</div>';return;}
  host.innerHTML=items.map(x=>{
    const name=x.nickname||x.model_name||x.version_name||x.filename;
    const tags=(x.tags||[]).map(t=>`<span class="tag-chip">${esc(t)}</span>`).join("");
    const triggers=(x.trigger_words||[]).join(", ");
    return `<details class="lora-card compact-lora" data-file="${esc(x.filename)}">
      <summary class="lora-summary">
        <div class="lora-summary-name"><strong>${esc(name)}</strong><div class="lora-file">${esc(x.filename)}</div></div>
        <div class="lora-summary-tags">${tags||'<span class="muted">No tags</span>'}</div>
        <div class="lora-summary-strength"><span class="muted">Rec.</span><strong>${Number(x.recommended_strength??1).toFixed(2)}</strong></div>
        <div class="lora-summary-trigger"><span class="muted">Triggers</span><span>${esc(triggers||"—")}</span></div>
      </summary>
      <div class="meta-editor">
        ${x.managed?`
        <label>Nickname</label><input class="meta-nickname" value="${esc(x.nickname||"")}">
        <label>Recommended strength</label><input class="meta-strength" type="number" step=".05" value="${esc(x.recommended_strength??1)}">
        <label>Trigger words <span class="muted">(comma-separated)</span></label><input class="meta-triggers" value="${esc((x.trigger_words||[]).join(", "))}">
        <label>Tags <span class="muted">(comma-separated)</span></label><input class="meta-tags" value="${esc((x.tags||[]).join(", "))}">
        <label>Personal notes <span class="muted">(one per line)</span></label><textarea class="meta-notes" rows="3">${esc((x.notes||[]).join("\n"))}</textarea>
        <button class="secondary save-meta" type="button">Save metadata</button>
        `:`<div class="muted">Local/core LoRA · metadata editing is available for managed CivitAI LoRAs.</div>`}
      </div>
    </details>`;
  }).join("");
  $$(".lora-card",host).forEach(card=>{
    const file=card.dataset.file;
    const item=state.loraCatalog.find(x=>x.filename===file);
    const save=$(".save-meta",card);
    if(save)save.onclick=async()=>{
      save.disabled=true; save.textContent="Saving…";
      try{
        await api("/api/loras/config",{method:"POST",body:{
          version_id:item.version_id,
          nickname:$(".meta-nickname",card).value,
          recommended_strength:Number($(".meta-strength",card).value||1),
          trigger_words:$(".meta-triggers",card).value,
          tags:$(".meta-tags",card).value,
          notes:$(".meta-notes",card).value
        }});
        await syncLoraCatalog(false);
        toast("LoRA metadata saved");
      }catch(e){toast(e.message);}
      finally{save.disabled=false;save.textContent="Save metadata";}
    };
  });
}
async function loadLoras(){
  try{
    const d=await api("/api/loras");
    state.loraCatalog=d.items||[];
    for(const [file,selected] of state.selectedLoras.entries()){
      const current=state.loraCatalog.find(x=>x.filename===file);
      if(current)state.selectedLoras.set(file,{...current,strength:selected.strength});
    }
    renderLoras(); renderSelectedLoras();
  }catch(e){
    const host=$("#loraLibrary");
    if(host)host.innerHTML='<div class="message error">'+esc(e.message)+'</div>';
  }
}
async function syncLoraCatalog(showToast=true){
  const b=$("#syncLoras");
  if(b){b.disabled=true;b.textContent="Syncing…";}
  try{
    await api("/api/loras/sync",{method:"POST"});
    await loadLoras();
    if(showToast)toast("LoRA catalog synced");
  }finally{
    if(b){b.disabled=false;b.textContent="Sync";}
  }
}

function assetCardMedia(item){
  if(item.kind==="image"&&item.thumb)return `<img class="asset-thumb" src="${esc(item.thumb)}" alt="">`;
  return `<div class="asset-icon asset-thumb">${esc(({video:"VID",audio:"AUD"}[item.kind]||"FILE"))}</div>`;
}
function renderAssetLibrary(){
  const host=$("#assetLibrary");
  if(!host)return;
  const q=($("#assetSearch")?.value||"").trim().toLowerCase();
  const sort=$("#assetSort")?.value||"alpha";
  let items=state.assetCatalog.filter(x=>!q||[x.display_name,x.nickname,x.file,x.kind].join(" ").toLowerCase().includes(q));
  if(sort==="alpha")items=[...items].sort((a,b)=>String(a.display_name).localeCompare(String(b.display_name),undefined,{sensitivity:"base"}));
  else items=[...items].sort((a,b)=>Number(b.mtime||0)-Number(a.mtime||0));
  if(!items.length){host.innerHTML='<div class="muted">No matching uploaded assets.</div>';return;}
  host.innerHTML=items.map(x=>`<div class="asset-card" data-file="${esc(x.file)}">
    ${assetCardMedia(x)}
    <div class="asset-card-main">
      <strong>${esc(x.display_name)}</strong>
      <div class="muted">${esc(x.kind.toUpperCase())} · ${esc(x.file)} · ${bytes(x.size)}</div>
      <div class="asset-nickname-row">
        <input class="asset-nickname" value="${esc(x.nickname||"")}" placeholder="Nickname (optional)">
        <button class="secondary small save-asset-name" type="button">Save</button>
      </div>
    </div>
  </div>`).join("");
  $$(".asset-card",host).forEach(card=>{
    $(".save-asset-name",card).onclick=async()=>{
      const file=card.dataset.file, input=$(".asset-nickname",card), button=$(".save-asset-name",card);
      button.disabled=true;
      try{
        await api("/api/assets/meta",{method:"PUT",body:{file,nickname:input.value}});
        await loadAssets(true);
        toast("Asset nickname saved");
      }catch(e){toast(e.message);}
      finally{button.disabled=false;}
    };
  });
}
function renderAssetPicker(){
  const host=$("#assetPickerList");
  if(!host)return;
  const q=($("#assetPickerSearch")?.value||"").trim().toLowerCase();
  const kind=state.assetPickerKind;
  let items=state.assetCatalog.filter(x=>(kind==="all"||x.kind===kind)&&(!q||[x.display_name,x.nickname,x.file,x.kind].join(" ").toLowerCase().includes(q)));
  items=[...items].sort((a,b)=>String(a.display_name).localeCompare(String(b.display_name),undefined,{sensitivity:"base"}));
  if(!items.length){host.innerHTML='<div class="muted">No matching assets.</div>';return;}
  host.innerHTML=items.map(x=>`<button class="asset-pick-row" data-file="${esc(x.file)}" data-kind="${esc(x.kind)}" type="button">
    ${assetCardMedia(x)}
    <span><strong>${esc(x.display_name)}</strong><small>${esc(x.kind.toUpperCase()+" · "+x.file)}</small></span>
  </button>`).join("");
  $$(".asset-pick-row",host).forEach(row=>row.onclick=()=>{
    const file=row.dataset.file, kind=row.dataset.kind;
    if(state.assetPickerMode==="start"){
      state.startingImage=file; renderStartingImage(); stashCurrentDraft();
    }else{
      addRef(kind,file,true);
    }
    closeAssetPicker();
  });
}
function openAssetPicker(mode,kind="all"){
  state.assetPickerMode=mode; state.assetPickerKind=kind;
  $("#assetPickerTitle").textContent=mode==="start"?"Choose starting image":"Add R2V reference";
  $("#assetPickerHint").textContent=mode==="start"?"Images only":"Images, videos, or audio";
  $("#assetPickerSearch").value="";
  renderAssetPicker();
  const dialog=$("#assetPicker");
  if(dialog.showModal)dialog.showModal(); else dialog.setAttribute("open","");
}
function closeAssetPicker(){
  const dialog=$("#assetPicker");
  if(dialog.close&&dialog.open)dialog.close(); else dialog.removeAttribute("open");
}
async function loadAssets(render=true){
  try{
    const d=await api("/api/inputs?kind=all&sort=recent");
    state.assetCatalog=d.items||[];
    renderStartingImage(); renderRefs();
    if(render)renderAssetLibrary();
    if($("#assetPicker")?.open)renderAssetPicker();
  }catch(e){
    const host=$("#assetLibrary");
    if(host&&render)host.innerHTML='<div class="message error">'+esc(e.message)+'</div>';
  }
}

function formSnapshot(){
  const draft=captureDraft();
  return {mode:state.mode,prompt_mode:state.promptMode,...draft};
}
function applySnapshot(v){
  if(!v)return;
  const mode=v.mode||"t2v", pm=v.prompt_mode||"auto";
  if(!state.restoringDraft)stashCurrentDraft();
  state.mode=mode; state.promptMode=pm;
  setSegment($("#modeSeg"),mode); setSegment($("#promptModeSeg"),pm);
  $("#i2vBlock").hidden=mode!=="i2v"; $("#r2vBlock").hidden=mode!=="r2v";
  $("#promptLabel").textContent=pm==="auto"?"Prompt idea":"Custom prompt";
  applyDraft(v);
  state.uiProfiles[profileKey()]=captureDraft();
  updateDraftLabel(); mirrorUiState(); scheduleDraftSave();
}

async function loadTemplates(){
  try{
    const d=await api("/api/templates"); state.templates=d.templates||{};
    const sel=$("#templateSelect"), cur=sel.value;
    sel.innerHTML='<option value="">Template…</option>'+Object.keys(state.templates).sort().map(k=>'<option value="'+esc(k)+'">'+esc(k)+'</option>').join("");
    if(state.templates[cur])sel.value=cur;
  }catch(e){}
}

async function generate(){
  const button=$("#generate"); button.disabled=true; msg($("#generateMsg"),"Queueing…");
  try{
    const snap=formSnapshot();
    const payload={...snap,
      prompt:snap.prompt_mode==="custom"?snap.prompt:"",
      prompt_idea:snap.prompt_mode==="auto"?snap.prompt:""
    };
    if(state.mode==="i2v"&&!payload.starting_image) throw new Error("Choose or upload a starting image.");
    const d=await api("/api/generate",{method:"POST",body:payload});
    $("#seed").value=d.seed;
    stashCurrentDraft();
    msg($("#generateMsg"),"Queued · seed "+d.seed,"ok");
    switchTab("queue"); await refreshQueue(); await refreshProgress();
  }catch(e){msg($("#generateMsg"),e.message,"error");}
  finally{button.disabled=false;}
}

async function refreshProgress(){
  try{
    const d=await api("/api/progress");
    $("#processName").textContent=d.process_name||"Ready";
    $("#nodeName").textContent=d.node_title||"";
    $("#processPct").textContent=pct(d.process_percent);
    $("#totalPct").textContent=pct(d.total_percent);
    $("#processBar").style.width=Math.max(0,Math.min(100,Number(d.process_percent||0)))+"%";
    $("#totalBar").style.width=Math.max(0,Math.min(100,Number(d.total_percent||0)))+"%";
    $("#progressStatus").textContent=(d.status||"idle").replace(/^./,c=>c.toUpperCase());
    $("#healthDot").classList.toggle("ok",d.ws_connected!==false);
  }catch(e){}
}
async function refreshQueue(){
  try{
    const d=await api("/api/queue"), host=$("#queueList");
    const items=d.items||[];
    if(!items.length){host.innerHTML='<div class="muted">Queue empty.</div>';return;}
    host.innerHTML=items.map(x=>{
      const rec=x.record||{};
      const label=x.label||(x.status==="running"?"RUNNING":"NEXT #"+(x.position||"?"));
      return `<div class="qitem ${x.status==="running"?"running":""}" data-pid="${esc(x.prompt_id)}">
        <div class="queue-badge ${x.status==="running"?"active":""}">${esc(label)}</div>
        <div class="queue-main">
          <strong>${esc((rec.mode||"").toUpperCase())} · ${esc((rec.prompt_mode||"").toUpperCase())}</strong>
          <div class="muted">${esc((rec.prompt_idea||rec.prompt||"").slice(0,160))}</div>
        </div>
        <button class="danger cancel" type="button">${x.status==="running"?"Stop":"Cancel"}</button>
      </div>`;
    }).join("");
    $$(".qitem",host).forEach(row=>$(".cancel",row).onclick=async()=>{
      const running=row.classList.contains("running");
      if(running&&!confirm("Stop the currently running generation?"))return;
      try{await api("/api/cancel/"+encodeURIComponent(row.dataset.pid),{method:"POST"});await refreshQueue();}catch(e){toast(e.message);}
    });
  }catch(e){}
}

function copyText(text){
  if(navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(()=>toast("Copied")).catch(()=>toast("Copy failed"));
  else toast("Clipboard unavailable");
}
async function refreshOutputs(force=false){
  const host=$("#outputs");
  // Never replace a live <video> element underneath active playback. The old
  // 15-second poll rebuilt the entire output DOM and reset mobile playback.
  const playing=$$("video",host).some(v=>!v.paused&&!v.ended);
  if(!force && playing) return;

  try{
    state.openOutputs=new Set($$("details.output[open]",host).map(x=>x.dataset.file));
    const d=await api("/api/outputs");
    const next=d.items||[];
    state.outputs=next;

    // Polling is cheap, DOM replacement is not. If nothing user-visible changed,
    // leave the existing video elements untouched so currentTime/buffer/state survive.
    const signature=JSON.stringify(next.map(x=>[
      x.file, Number(x.size||0), Number(x.mtime||0),
      x.metadata?.prompt_id||"", Number(x.metadata?.completed_at||0)
    ]));
    if(signature===state.outputsSignature) return;
    state.outputsSignature=signature;

    if(!next.length){
      host.innerHTML='<div class="muted">No completed videos with audio yet.</div>';
      return;
    }

    host.innerHTML=next.map(x=>{
      const m=x.metadata||{}, open=state.openOutputs.has(x.file)?" open":"";
      return `<details class="output" data-file="${esc(x.file)}"${open}>
        <summary><strong>${esc(x.file.split("/").pop())}</strong><div class="muted">${esc((m.mode||"").toUpperCase())} ${esc((m.prompt_mode||"").toUpperCase())} · ${bytes(x.size)}</div></summary>
        <video controls preload="metadata" src="${esc(mediaUrl(x.file))}"></video>
        <div class="actions">
          <button class="secondary copy-prompt">Copy prompt</button>
          <button class="secondary copy-seed">Copy seed</button>
          <button class="secondary copy-meta">Copy metadata</button>
          <button class="secondary use-r2v">Use as R2V ref</button>
          <button class="secondary reuse">Reuse setup</button>
          <button class="danger delete">Delete</button>
        </div>
        <div class="meta">${esc((m.prompt_idea||m.prompt||"").slice(0,600))}</div>
      </details>`;
    }).join("");

    $$(".output",host).forEach(el=>{
      const item=state.outputs.find(x=>x.file===el.dataset.file), m=item.metadata||{};
      $(".copy-prompt",el).onclick=()=>copyText(m.actual_prompt||m.prompt||m.prompt_idea||"");
      $(".copy-seed",el).onclick=()=>copyText(String(m.seed??""));
      $(".copy-meta",el).onclick=()=>copyText(JSON.stringify(m,null,2));
      $(".reuse",el).onclick=()=>{applySnapshot(m);switchTab("generate");toast("Setup loaded");};
      $(".use-r2v",el).onclick=async()=>{
        try{
          const d=await api("/api/output-to-input",{method:"POST",body:{file:item.file}});
          setMode("r2v"); addRef("video",d.file,true); switchTab("generate"); refreshInputOptions(); toast("Added as R2V video reference");
        }catch(e){toast(e.message);}
      };
      $(".delete",el).onclick=async()=>{
        if(!confirm("Delete this video?"))return;
        try{
          await api("/api/outputs/"+item.file.split("/").map(encodeURIComponent).join("/"),{method:"DELETE"});
          state.outputsSignature="";
          await refreshOutputs(true);
        }catch(e){toast(e.message);}
      };
    });
  }catch(e){host.innerHTML='<div class="message error">'+esc(e.message)+'</div>';}
}

async function refreshInfo(){
  try{
    const d=await api("/api/info"); state.info=d;
    const h=d.hardware||{}, m=d.memory||{}, p=d.provisioning||{}, lim=Number(m.limit_bytes||h.cgroup_memory_limit_bytes||0), cur=Number(m.current_bytes||h.cgroup_memory_current_bytes||0);
    const coreReady=!!p.core_ready;
    $("#subtitle").textContent=(h.gpu_name||"MMH3")+" · "+d.version+(coreReady?"":" · provisioning");
    $("#healthDot").classList.toggle("ok",coreReady);
    $("#healthDot").classList.toggle("warn",!coreReady);
    const gen=$("#generate");
    if(gen){
      gen.disabled=!coreReady;
      gen.textContent=coreReady?"Queue Generation":"Core models provisioning…";
    }
    $("#systemStats").innerHTML=`
      <div class="stat"><span class="muted">GPU</span><strong>${esc(h.gpu_name||"unknown")}</strong><span class="muted">${h.vram_mib?Math.round(h.vram_mib/1024)+" GB VRAM":""}</span></div>
      <div class="stat"><span class="muted">Container RAM</span><strong>${lim?(cur/2**30).toFixed(1)+" / "+(lim/2**30).toFixed(1)+" GB":"detecting…"}</strong><span class="muted">${lim?pct(cur/lim*100):""}</span></div>
      <div class="stat"><span class="muted">Provisioning</span><strong>${coreReady?"Core ready":esc(p.status||"starting")}</strong><span class="muted">${esc(p.message||p.stage||"")}</span></div>
      <div class="stat"><span class="muted">Memory guard</span><strong>${m.memory_hold?"Cleaning / hold":"Ready"}</strong><span class="muted">${m.free_bytes!=null?(Number(m.free_bytes)/2**30).toFixed(1)+" GB free":""}</span></div>
      <div class="stat"><span class="muted">Driver</span><strong>${esc(h.driver_version||"unknown")}</strong><span class="muted">cgroup-aware</span></div>`;
    const secrets=[
      ["OpenRouter",d.openrouter_configured],["Hugging Face",d.hf_configured],["CivitAI",d.civitai_configured]
    ];
    $("#secretStatus").innerHTML=secrets.map(([n,ok])=>'<div class="secret"><span>'+esc(n)+'</span><strong class="'+(ok?"yes":"no")+'">'+(ok?"Configured":"Not set")+'</strong></div>').join("");
  }catch(e){
    $("#healthDot").classList.remove("ok"); $("#healthDot").classList.add("warn");
    $("#subtitle").textContent="MMH3 · backend unavailable";
  }
}

async function loadPrompts(){
  try{
    const d=await api("/api/system-prompts"), p=d.prompts||{};
    $("#spT2V").value=p.t2v_auto||""; $("#spI2V").value=p.i2v_auto||""; $("#spR2V").value=p.r2v_auto||"";
  }catch(e){msg($("#systemMsg"),e.message,"error");}
}


function studioId(prefix){
  try{return prefix+"_"+crypto.randomUUID().replaceAll("-","").slice(0,10);}catch{return prefix+"_"+Date.now().toString(36)+Math.random().toString(36).slice(2,7);}
}
function studioProject(){ return state.studioProject; }
function studioScene(){ return state.studioProject?.scene||null; }
function studioSetSaveState(text){ const el=$("#studioSaveState"); if(el)el.textContent=text; }
function studioImageOptions(selected=""){
  const items=state.assetCatalog.filter(x=>x.kind==="image");
  return '<option value="">No image selected</option>'+items.map(x=>'<option value="'+esc(x.file)+'" '+(x.file===selected?"selected":"")+'>'+esc(x.display_name||x.file)+'</option>').join("");
}
function studioOptions(values,current){
  const arr=[...(values||[])];
  if(current&&!arr.includes(current))arr.unshift(current);
  return arr.map(x=>'<option value="'+esc(x)+'" '+(x===current?"selected":"")+'>'+esc(x)+'</option>').join("");
}
function studioAspectRatioValue(label){
  const m=String(label||"").match(/(\d+)\s*:\s*(\d+)/);
  return m?Number(m[1])/Number(m[2]):16/9;
}
function framingIcon(name){
  const n=String(name||"").toLowerCase();
  let h=50,w=20,y=24;
  if(n.includes("extreme wide")){h=24;w=9;y=40;}
  else if(n==="wide"){h=32;w=11;y=34;}
  else if(n==="full"){h=54;w=16;y=20;}
  else if(n.includes("medium full")){h=66;w=22;y=12;}
  else if(n==="medium"){h=78;w=28;y=5;}
  else if(n.includes("medium close")){h=92;w=38;y=-3;}
  else if(n==="close-up"){h=116;w=54;y=-14;}
  else if(n.includes("extreme close")){h=150;w=78;y=-30;}
  if(n.includes("two-shot"))return '<svg viewBox="0 0 100 70" aria-hidden="true"><rect x="1" y="1" width="98" height="68" rx="5"/><circle cx="35" cy="25" r="8"/><path d="M24 61 Q35 37 46 61"/><circle cx="65" cy="25" r="8"/><path d="M54 61 Q65 37 76 61"/></svg>';
  if(n.includes("over-the-shoulder"))return '<svg viewBox="0 0 100 70" aria-hidden="true"><rect x="1" y="1" width="98" height="68" rx="5"/><circle cx="28" cy="31" r="13"/><path d="M5 70 Q27 42 48 70"/><circle cx="67" cy="24" r="8"/><path d="M55 61 Q67 38 79 61"/></svg>';
  if(n==="pov")return '<svg viewBox="0 0 100 70" aria-hidden="true"><rect x="1" y="1" width="98" height="68" rx="5"/><path d="M7 64 Q22 46 36 56"/><path d="M93 64 Q78 46 64 56"/><circle cx="50" cy="33" r="4"/></svg>';
  if(n==="custom")return '<svg viewBox="0 0 100 70" aria-hidden="true"><rect x="1" y="1" width="98" height="68" rx="5"/><path d="M26 48 L43 31 L54 41 L72 20"/><circle cx="25" cy="48" r="4"/><circle cx="72" cy="20" r="4"/></svg>';
  const head=Math.max(5,w*.28), cx=50;
  return '<svg viewBox="0 0 100 70" aria-hidden="true"><rect x="1" y="1" width="98" height="68" rx="5"/><circle cx="'+cx+'" cy="'+(y+head)+'" r="'+head+'"/><path d="M'+(cx-w)+' 70 Q'+cx+' '+(y+h*.38)+' '+(cx+w)+' 70"/></svg>';
}
function studioBlockingSummary(shot,project){
  const blocks=shot.blocking||[];
  if(!blocks.length)return "No blocking";
  const names=new Map((project.scene.subjects||[]).map((s,i)=>[s.id,"S"+(i+1)+" "+(s.label||"")]));
  return blocks.map(b=>names.get(b.subject_id)||b.label||b.kind).join(" · ");
}

function studioToggleLock(list,key,on){
  const set=new Set(list||[]);
  if(on)set.add(key);else set.delete(key);
  return [...set];
}
function studioToggleLockGroup(list,keys,on){
  let out=[...(list||[])];
  keys.forEach(k=>{out=studioToggleLock(out,k,on);});
  return out;
}
function studioGroupLocked(list,keys){const set=new Set(list||[]);return keys.every(k=>set.has(k));}

async function loadStudioIndex(selectId=null){
  if(state.studioLoading)return;
  state.studioLoading=true;
  try{
    const d=await api("/api/prompt-studio/projects");
    state.studioProjects=d.projects||[];
    state.studioSavedSubjects=d.saved_subjects||[];
    state.studioMeta={
      subject_types:d.subject_types||[],
      camera_motions:d.camera_motions||[],
      framing_presets:d.framing_presets||[]
    };
    const sel=$("#studioProjectSelect");
    if(sel){
      sel.innerHTML='<option value="">Project…</option>'+state.studioProjects.map(x=>'<option value="'+esc(x.id)+'">'+esc(x.name)+' · '+esc(String(x.mode||"").toUpperCase())+'</option>').join("");
    }
    renderStudioSavedSubjects();
    const target=selectId||state.studioProject?.id||state.studioProjects[0]?.id;
    if(target&&state.studioProjects.some(x=>x.id===target))await loadStudioProject(target);
    else if(!state.studioProjects.length)await createStudioProject(false);
  }catch(e){msg($("#studioMsg"),e.message,"error");}
  finally{state.studioLoading=false;}
}
async function createStudioProject(askName=true){
  const defaultName="Untitled Prompt";
  const name=askName?prompt("Project name",defaultName):defaultName;
  if(name===null)return;
  const body={
    name:name||defaultName,
    mode:state.mode||"r2v",
    duration:Number($("#duration")?.value||10),
    aspect_ratio:$("#aspect")?.value||"9:16 (Portrait Widescreen)",
    concept:$("#prompt")?.value||"",
    model:"google/gemini-3-flash-preview"
  };
  const d=await api("/api/prompt-studio/projects",{method:"POST",body});
  state.studioProject=d.project;
  await loadStudioIndex(d.project.id);
  toast("Prompt project created");
}
async function loadStudioProject(id){
  if(!id)return;
  studioSetSaveState("Loading…");
  try{
    const d=await api("/api/prompt-studio/projects/"+encodeURIComponent(id));
    state.studioProject=d.project;
    renderStudioProject();
    studioSetSaveState("Saved");
  }catch(e){msg($("#studioMsg"),e.message,"error");studioSetSaveState("Load failed");}
}
function pullStudioStatic(){
  const p=studioProject(); if(!p)return;
  p.name=$("#studioName").value.trim()||"Untitled Prompt";
  p.mode=$("#studioMode").value;
  p.duration=Math.max(1,Number($("#studioDuration").value||10));
  p.aspect_ratio=$("#studioAspect").value;
  p.model=$("#studioModel").value.trim()||"google/gemini-3-flash-preview";
  p.scene.concept=$("#studioConcept").value;
  p.scene.environment=$("#studioEnvironment").value;
  p.scene.visual_style=$("#studioVisualStyle").value;
  p.scene.soundscape=$("#studioSoundscape").value;
  p.scene.music=$("#studioMusic").value;
  p.starting_image={
    asset_file:$("#studioStartingImage").value||"",
    analyze:$("#studioStartingAnalyze").checked
  };
}
function scheduleStudioSave(){
  if(!studioProject())return;
  studioSetSaveState("Unsaved…");
  clearTimeout(state.studioSaveTimer);
  state.studioSaveTimer=setTimeout(()=>saveStudioProject(false),650);
}
async function saveStudioProject(show=true){
  const p=studioProject();if(!p)return null;
  pullStudioStatic();
  clearTimeout(state.studioSaveTimer);
  studioSetSaveState("Saving…");
  try{
    const d=await api("/api/prompt-studio/projects/"+encodeURIComponent(p.id),{method:"PUT",body:{project:p}});
    state.studioProject=d.project;
    studioSetSaveState("Saved");
    if(show)toast("Prompt project saved");
    return d.project;
  }catch(e){studioSetSaveState("Save failed");msg($("#studioMsg"),e.message,"error");throw e;}
}
function renderStudioSavedSubjects(){
  const sel=$("#studioSavedSubjectSelect");if(!sel)return;
  sel.innerHTML='<option value="">Saved subject…</option>'+state.studioSavedSubjects.map(x=>'<option value="'+esc(x.saved_id||x.id)+'">'+esc(x.label||"Saved subject")+'</option>').join("");
}
function renderStudioProject(){
  const p=studioProject();if(!p)return;
  $("#studioProjectSelect").value=p.id;
  $("#studioName").value=p.name||"";
  $("#studioMode").value=p.mode||"r2v";
  $("#studioDuration").value=p.duration||10;
  $("#studioAspect").value=p.aspect_ratio||"9:16 (Portrait Widescreen)";
  $("#studioModel").value=p.model||"google/gemini-3-flash-preview";
  $("#studioConcept").value=p.scene?.concept||"";
  $("#studioEnvironment").value=p.scene?.environment||"";
  $("#studioVisualStyle").value=p.scene?.visual_style||"";
  $("#studioSoundscape").value=p.scene?.soundscape||"";
  $("#studioMusic").value=p.scene?.music||"";
  $("#studioI2VStartBlock").hidden=p.mode!=="i2v";
  $("#studioStartingImage").innerHTML=studioImageOptions(p.starting_image?.asset_file||"");
  $("#studioStartingAnalyze").checked=p.starting_image?.analyze!==false;
  $("[data-studio-scene-lock]").forEach(el=>el.checked=(p.scene?.locks||[]).includes(el.dataset.studioSceneLock));
  renderStudioSubjects();
  renderStudioTimeline();
  renderStudioShots();
  renderStudioCompiled();
  msg($("#studioMsg"),"");
}
function renderStudioSubjects(){
  const p=studioProject(),host=$("#studioSubjects");if(!p||!host)return;
  const subjects=p.scene.subjects||[];
  if(!subjects.length){host.innerHTML='<div class="muted">No subjects yet. Add one manually or let the planner create them.</div>';return;}
  host.innerHTML=subjects.map((s,index)=>{
    const ref=s.reference||{}, mode=ref.mode||"none";
    const descLocked=(s.locks||[]).includes("description");
    const perfLocked=(s.locks||[]).includes("performance_notes");
    return '<div class="studio-subject-card" data-studio-subject="'+esc(s.id)+'">'+
      '<div class="labelrow"><strong>&lt;Subject '+(index+1)+'&gt;</strong><div class="studio-mini-actions"><button class="ghost small studio-subject-ai" type="button">✨ Edit</button><button class="ghost small studio-subject-save" type="button">Save</button><button class="danger ghost small studio-subject-remove" type="button">×</button></div></div>'+
      '<div class="grid2"><div><label>Label</label><input class="studio-subject-label" value="'+esc(s.label||"")+'"></div><div><label>Type</label><select class="studio-subject-type">'+studioOptions(state.studioMeta.subject_types,s.type||"person")+'</select></div></div>'+
      '<div class="studio-field-head"><label>Description</label><label class="inline-lock"><input class="studio-subject-lock-desc" type="checkbox" '+(descLocked?"checked":"")+'> 🔒</label></div>'+
      '<textarea class="studio-subject-description" rows="4">'+esc(s.description||"")+'</textarea>'+
      '<div class="studio-field-head"><label>Performance / behavior notes</label><label class="inline-lock"><input class="studio-subject-lock-perf" type="checkbox" '+(perfLocked?"checked":"")+'> 🔒</label></div>'+
      '<textarea class="studio-subject-performance" rows="3">'+esc(s.performance_notes||"")+'</textarea>'+
      '<div class="grid2"><div><label>Reference mode</label><select class="studio-subject-refmode"><option value="none" '+(mode==="none"?"selected":"")+'>Text only</option><option value="asset" '+(mode==="asset"?"selected":"")+'>Actual asset</option><option value="picture_slot" '+(mode==="picture_slot"?"selected":"")+'>Opaque Picture slot</option></select></div>'+
      '<div '+(mode==="none"?'hidden':'')+'><label>Picture #</label><input class="studio-subject-picture" type="number" min="1" max="9" value="'+Number(ref.picture_number||index+1)+'"></div></div>'+
      (mode==="asset"?'<label>Asset</label><select class="studio-subject-asset">'+studioImageOptions(ref.asset_file||"")+'</select><label class="check compact-check"><input class="studio-subject-analyze" type="checkbox" '+(ref.analyze?"checked":"")+'> Let Gemini analyze this reference</label>':'')+
      (mode==="picture_slot"?'<div class="studio-opaque-note">Opaque binding: the planner knows only that this subject is established by &lt;Picture '+Number(ref.picture_number||index+1)+'&gt;. It will not receive or invent the image contents.</div>':'')+
      '</div>';
  }).join("");

  $(".studio-subject-card",host).forEach(card=>{
    const id=card.dataset.studioSubject;
    const s=subjects.find(x=>x.id===id);if(!s)return;
    $(".studio-subject-label",card).oninput=e=>{s.label=e.target.value;scheduleStudioSave();};
    $(".studio-subject-type",card).onchange=e=>{s.type=e.target.value;scheduleStudioSave();};
    $(".studio-subject-description",card).oninput=e=>{s.description=e.target.value;scheduleStudioSave();};
    $(".studio-subject-performance",card).oninput=e=>{s.performance_notes=e.target.value;scheduleStudioSave();};
    $(".studio-subject-lock-desc",card).onchange=e=>{s.locks=studioToggleLock(s.locks,"description",e.target.checked);scheduleStudioSave();};
    $(".studio-subject-lock-perf",card).onchange=e=>{s.locks=studioToggleLock(s.locks,"performance_notes",e.target.checked);scheduleStudioSave();};
    $(".studio-subject-refmode",card).onchange=e=>{
      s.reference=s.reference||{picture_number:1,asset_file:"",analyze:false};
      s.reference.mode=e.target.value;
      if(e.target.value==="none"){s.reference.asset_file="";s.reference.analyze=false;}
      if(e.target.value==="picture_slot"){s.reference.asset_file="";s.reference.analyze=false;}
      renderStudioSubjects();scheduleStudioSave();
    };
    const picture=$(".studio-subject-picture",card);if(picture)picture.oninput=e=>{s.reference.picture_number=Math.max(1,Math.min(9,Number(e.target.value||1)));scheduleStudioSave();};
    const asset=$(".studio-subject-asset",card);if(asset)asset.onchange=e=>{s.reference.asset_file=e.target.value;scheduleStudioSave();};
    const analyze=$(".studio-subject-analyze",card);if(analyze)analyze.onchange=e=>{s.reference.analyze=e.target.checked;scheduleStudioSave();};
    $(".studio-subject-ai",card).onclick=()=>studioPromptEdit("subject",id,"Describe the change to this subject only");
    $(".studio-subject-save",card).onclick=()=>saveStudioSubject(s);
    $(".studio-subject-remove",card).onclick=()=>{if(confirm("Remove this subject from the prompt project?")){p.scene.subjects=p.scene.subjects.filter(x=>x.id!==id);p.scene.shots.forEach(sh=>sh.subjects=(sh.subjects||[]).filter(x=>x!==id));renderStudioSubjects();renderStudioShots();scheduleStudioSave();}};
  });
}
function renderStudioTimeline(){
  const p=studioProject(),host=$("#studioTimeline");if(!p||!host)return;
  const shots=[...(p.scene.shots||[])].sort((a,b)=>Number(a.start_seconds)-Number(b.start_seconds));
  const duration=Math.max(.001,Number(p.duration||1));
  $("#studioShotCount").value=shots.length;
  const segments=shots.map((s,i)=>{
    const start=Number(s.start_seconds||0),end=i+1<shots.length?Number(shots[i+1].start_seconds||duration):duration;
    const width=Math.max(1,(end-start)/duration*100);
    return '<button class="studio-timeline-segment" data-shot="'+esc(s.id)+'" style="width:'+width+'%" title="Shot '+(i+1)+' · '+start.toFixed(1)+'–'+end.toFixed(1)+'s"><strong>S'+(i+1)+'</strong><span>'+Math.max(0,end-start).toFixed(1)+'s</span></button>';
  }).join("");
  const cuts=shots.slice(1).map((s,i)=>{
    const previous=shots[i],next=shots[i+2];
    const min=Number(previous.start_seconds||0)+.1;
    const max=(next?Number(next.start_seconds):duration)-.1;
    return '<div class="studio-cut-row"><span>Cut '+(i+2)+'</span><input type="range" min="'+min+'" max="'+Math.max(min,max)+'" step=".1" value="'+Number(s.start_seconds||0)+'" data-cut-shot="'+esc(s.id)+'"><strong>'+Number(s.start_seconds||0).toFixed(1)+'s</strong></div>';
  }).join("");
  host.innerHTML='<div class="studio-timeline-bar">'+segments+'</div>'+(cuts?'<div class="studio-cut-controls">'+cuts+'</div>':'');
  $$(".studio-timeline-segment",host).forEach(el=>el.onclick=()=>document.querySelector('[data-studio-shot="'+CSS.escape(el.dataset.shot)+'"]')?.scrollIntoView({behavior:"smooth",block:"center"}));
  $$("[data-cut-shot]",host).forEach(el=>el.oninput=e=>{
    const shot=p.scene.shots.find(x=>x.id===e.target.dataset.cutShot);if(!shot)return;
    shot.start_seconds=Number(e.target.value);
    const label=e.target.nextElementSibling;if(label)label.textContent=Number(e.target.value).toFixed(1)+"s";
    scheduleStudioSave();
  });
  $$("[data-cut-shot]",host).forEach(el=>el.onchange=()=>{renderStudioTimeline();renderStudioShots();});
}
function renderStudioShots(){
  const p=studioProject(),host=$("#studioShots");if(!p||!host)return;
  const shots=p.scene.shots||[];
  host.innerHTML=shots.map((s,index)=>{
    const cameraKeys=["framing","camera_motion","camera_custom","amplitude","speed"];
    const cameraLocked=studioGroupLocked(s.locks,cameraKeys), actionLocked=(s.locks||[]).includes("action"), dialogueLocked=(s.locks||[]).includes("dialogue"), timingLocked=(s.locks||[]).includes("start_seconds");
    const subjectChecks=(p.scene.subjects||[]).map((sub,si)=>'<label class="studio-chip"><input type="checkbox" data-shot-subject="'+esc(sub.id)+'" '+((s.subjects||[]).includes(sub.id)?"checked":"")+'> S'+(si+1)+' '+esc(sub.label||"")+'</label>').join("");
    const framingButtons=(state.studioMeta.framing_presets||[]).map(name=>'<button class="studio-framing-card '+(name===s.framing?"active":"")+'" type="button" data-framing="'+esc(name)+'">'+framingIcon(name)+'<span>'+esc(name)+'</span></button>').join("");
    return '<div class="studio-shot-card" data-studio-shot="'+esc(s.id)+'">'+
      '<div class="labelrow"><strong>Shot '+(index+1)+'</strong><div class="studio-mini-actions"><button class="ghost small studio-shot-ai" type="button">✨ Edit</button>'+(index?'<button class="danger ghost small studio-shot-remove" type="button">×</button>':'')+'</div></div>'+
      '<div class="grid2"><div><div class="studio-field-head"><label>Start (sec)</label><label class="inline-lock"><input class="studio-shot-lock-time" type="checkbox" '+(timingLocked?"checked":"")+'> 🔒</label></div><input class="studio-shot-start" type="number" min="0" max="'+Math.max(0,Number(p.duration)-.001)+'" step=".1" value="'+Number(s.start_seconds||0)+'" '+(index===0?"disabled":"")+'></div>'+
      '<div><div class="studio-field-head"><label>Framing</label><label class="inline-lock"><input class="studio-shot-lock-camera" type="checkbox" '+(cameraLocked?"checked":"")+'> 🔒 camera</label></div><select class="studio-shot-framing">'+studioOptions(state.studioMeta.framing_presets,s.framing||"Medium")+'</select></div></div>'+
      '<details class="studio-framing-picker"><summary>Visual framing presets</summary><div class="studio-framing-grid">'+framingButtons+'</div></details>'+
      '<div class="grid2"><div><label>Camera motion</label><select class="studio-shot-camera">'+studioOptions(state.studioMeta.camera_motions,s.camera_motion||"Static Shot")+'</select></div><div><label>Custom camera detail</label><input class="studio-shot-camera-custom" value="'+esc(s.camera_custom||"")+'" placeholder="optional"></div></div>'+
      '<div class="grid2"><div><label>Amplitude</label><select class="studio-shot-amplitude"><option value="">Default</option><option '+(s.amplitude==="with small amplitude"?"selected":"")+'>with small amplitude</option><option '+(s.amplitude==="with large amplitude"?"selected":"")+'>with large amplitude</option></select></div><div><label>Speed</label><select class="studio-shot-speed"><option value="">Default</option><option '+(s.speed==="at slow speed"?"selected":"")+'>at slow speed</option><option '+(s.speed==="at fast speed"?"selected":"")+'>at fast speed</option></select></div></div>'+
      '<label>Subjects in shot</label><div class="studio-chips">'+(subjectChecks||'<span class="muted">No defined subjects.</span>')+'</div>'+
      '<div class="studio-blocking-row"><div><strong>Blocking</strong><div class="muted">'+esc(studioBlockingSummary(s,p))+'</div></div><button class="secondary small studio-open-blocking" type="button">Block Shot</button></div>'+
      '<div class="studio-field-head"><label>Action / performance</label><label class="inline-lock"><input class="studio-shot-lock-action" type="checkbox" '+(actionLocked?"checked":"")+'> 🔒</label></div><textarea class="studio-shot-action" rows="4">'+esc(s.action||"")+'</textarea>'+
      '<div class="studio-field-head"><label>Dialogue</label><label class="inline-lock"><input class="studio-shot-lock-dialogue" type="checkbox" '+(dialogueLocked?"checked":"")+'> 🔒</label></div><textarea class="studio-shot-dialogue" rows="3">'+esc(s.dialogue||"")+'</textarea>'+
      '<label>Shot-specific sound</label><textarea class="studio-shot-sound" rows="2">'+esc(s.sound||"")+'</textarea>'+
      '</div>';
  }).join("");
  $$(".studio-shot-card",host).forEach((card,index)=>{
    const id=card.dataset.studioShot,s=shots.find(x=>x.id===id);if(!s)return;
    const cameraKeys=["framing","camera_motion","camera_custom","amplitude","speed"];
    const bind=(selector,key,event="input")=>{const el=$(selector,card);if(el)el.addEventListener(event,e=>{s[key]=e.target.value;if(key==="start_seconds")renderStudioTimeline();scheduleStudioSave();});};
    bind(".studio-shot-start","start_seconds");bind(".studio-shot-framing","framing","change");bind(".studio-shot-camera","camera_motion","change");bind(".studio-shot-camera-custom","camera_custom");bind(".studio-shot-amplitude","amplitude","change");bind(".studio-shot-speed","speed","change");bind(".studio-shot-action","action");bind(".studio-shot-dialogue","dialogue");bind(".studio-shot-sound","sound");
    $$(".studio-framing-card",card).forEach(el=>el.onclick=()=>{s.framing=el.dataset.framing;$(".studio-shot-framing",card).value=s.framing;renderStudioShots();scheduleStudioSave();});
    $$(".studio-chip input",card).forEach(el=>el.onchange=e=>{const sid=e.target.dataset.shotSubject,set=new Set(s.subjects||[]);e.target.checked?set.add(sid):set.delete(sid);s.subjects=[...set];scheduleStudioSave();});
    $(".studio-shot-lock-time",card).onchange=e=>{s.locks=studioToggleLock(s.locks,"start_seconds",e.target.checked);scheduleStudioSave();};
    $(".studio-shot-lock-camera",card).onchange=e=>{s.locks=studioToggleLockGroup(s.locks,cameraKeys,e.target.checked);scheduleStudioSave();};
    $(".studio-shot-lock-action",card).onchange=e=>{s.locks=studioToggleLock(s.locks,"action",e.target.checked);scheduleStudioSave();};
    $(".studio-shot-lock-dialogue",card).onchange=e=>{s.locks=studioToggleLock(s.locks,"dialogue",e.target.checked);scheduleStudioSave();};
    $(".studio-shot-ai",card).onclick=()=>studioPromptEdit("shot",id,"Describe the change to Shot "+(index+1)+" only");
    $(".studio-open-blocking",card).onclick=()=>openStudioBlocking(id);
    const rm=$(".studio-shot-remove",card);if(rm)rm.onclick=()=>{if(confirm("Remove Shot "+(index+1)+"?")){p.scene.shots=p.scene.shots.filter(x=>x.id!==id);renderStudioTimeline();renderStudioShots();scheduleStudioSave();}};
  });
}

function addStudioSubject(subject=null){
  const p=studioProject();if(!p)return;
  const index=(p.scene.subjects||[]).length+1;
  const s=subject?structuredClone(subject):{
    id:studioId("subject"),label:"Subject "+index,type:"person",description:"",performance_notes:"",
    reference:{mode:"none",picture_number:Math.min(index,9),asset_file:"",analyze:false},locks:[]
  };
  s.id=studioId("subject");delete s.saved_id;s.locks=[];
  s.reference=s.reference||{mode:"none",picture_number:Math.min(index,9),asset_file:"",analyze:false};
  p.scene.subjects.push(s);renderStudioSubjects();renderStudioShots();scheduleStudioSave();
}
function studioNewShot(start=0){
  return {id:studioId("shot"),start_seconds:start,framing:"Medium",camera_motion:"Static Shot",camera_custom:"",amplitude:"",speed:"",subjects:[],action:"",dialogue:"",sound:"",blocking:[],locks:[]};
}
function addStudioShot(){
  const p=studioProject();if(!p)return;
  const shots=p.scene.shots||[],last=shots[shots.length-1],start=Math.min(Math.max(0,Number(p.duration)-.001),Number(last?.start_seconds||0)+3);
  shots.push(studioNewShot(start));
  p.scene.shots=shots;renderStudioTimeline();renderStudioShots();scheduleStudioSave();
}
function studioSetShotCount(){
  const p=studioProject();if(!p)return;
  const count=Math.max(1,Math.min(12,Number($("#studioShotCount").value||1)));
  const shots=p.scene.shots||[];
  while(shots.length<count)shots.push(studioNewShot(0));
  if(shots.length>count)shots.splice(count);
  p.scene.shots=shots;
  studioEvenTiming();
}
function studioEvenTiming(){
  const p=studioProject();if(!p)return;
  const shots=p.scene.shots||[],duration=Number(p.duration||1);
  shots.forEach((s,i)=>{if(i===0)s.start_seconds=0;else if(!(s.locks||[]).includes("start_seconds"))s.start_seconds=Number((duration*i/shots.length).toFixed(3));});
  renderStudioTimeline();renderStudioShots();scheduleStudioSave();
}
function blockingShot(){
  const p=studioProject();return p?.scene?.shots?.find(x=>x.id===state.studioBlockingShotId)||null;
}
function blockingSelected(){
  const shot=blockingShot();return shot?.blocking?.find(x=>x.id===state.studioBlockingSelectedId)||null;
}
function openStudioBlocking(shotId){
  state.studioBlockingShotId=shotId;state.studioBlockingSelectedId=null;
  renderStudioBlocking();
  const d=$("#studioBlockingDialog");if(d.showModal)d.showModal();else d.setAttribute("open","");
}
function closeStudioBlocking(){
  const d=$("#studioBlockingDialog");if(d.close&&d.open)d.close();else d.removeAttribute("open");
  state.studioBlockingShotId=null;state.studioBlockingSelectedId=null;
  renderStudioShots();scheduleStudioSave();
}
function addStudioBlockingSubject(){
  const p=studioProject(),shot=blockingShot(),sid=$("#studioBlockingSubject").value;if(!p||!shot||!sid)return;
  const subject=p.scene.subjects.find(x=>x.id===sid);if(!subject)return;
  shot.blocking=shot.blocking||[];
  const existing=shot.blocking.find(x=>x.subject_id===sid&&x.kind==="subject");
  if(existing){state.studioBlockingSelectedId=existing.id;renderStudioBlocking();return;}
  const block={id:studioId("block"),subject_id:sid,kind:"subject",label:subject.label||"Subject",x:.4,y:.2,width:.2,height:.6,facing:"unspecified",note:""};
  shot.blocking.push(block);if(!(shot.subjects||[]).includes(sid))shot.subjects.push(sid);
  state.studioBlockingSelectedId=block.id;renderStudioBlocking();scheduleStudioSave();
}
function renderStudioBlocking(){
  const p=studioProject(),shot=blockingShot(),dialog=$("#studioBlockingDialog");if(!p||!shot||!dialog)return;
  const ratio=studioAspectRatioValue(p.aspect_ratio);
  $("#studioBlockingFrame").style.aspectRatio=String(ratio);
  $("#studioBlockingTitle").textContent="Shot "+((p.scene.shots||[]).findIndex(x=>x.id===shot.id)+1)+" blocking";
  $("#studioBlockingSubject").innerHTML='<option value="">Choose subject…</option>'+(p.scene.subjects||[]).map((s,i)=>'<option value="'+esc(s.id)+'">S'+(i+1)+' · '+esc(s.label||"Subject")+'</option>').join("");
  const blocks=shot.blocking||[];
  $("#studioBlockingFrame").innerHTML=blocks.map((b,i)=>'<button type="button" class="studio-block '+(b.id===state.studioBlockingSelectedId?"selected":"")+'" data-block="'+esc(b.id)+'" style="left:'+(Number(b.x)*100)+'%;top:'+(Number(b.y)*100)+'%;width:'+(Number(b.width)*100)+'%;height:'+(Number(b.height)*100)+'%"><span>'+esc(b.label||("Block "+(i+1)))+'</span><small>'+esc(b.facing&&b.facing!=="unspecified"?"faces "+b.facing:"")+'</small></button>').join("");
  $$(".studio-block",$("#studioBlockingFrame")).forEach(el=>{
    el.onclick=e=>{e.stopPropagation();state.studioBlockingSelectedId=el.dataset.block;renderStudioBlocking();};
    let start=null;
    el.onpointerdown=e=>{
      if(e.button!==undefined&&e.button!==0)return;
      const b=blocks.find(x=>x.id===el.dataset.block);if(!b)return;
      state.studioBlockingSelectedId=b.id;el.setPointerCapture?.(e.pointerId);
      const frame=$("#studioBlockingFrame").getBoundingClientRect();
      start={px:e.clientX,py:e.clientY,x:Number(b.x),y:Number(b.y),fw:frame.width,fh:frame.height};
      e.preventDefault();
    };
    el.onpointermove=e=>{
      if(!start)return;
      const b=blocks.find(x=>x.id===el.dataset.block);if(!b)return;
      b.x=Math.max(0,Math.min(1-Number(b.width),(start.x+(e.clientX-start.px)/start.fw)));
      b.y=Math.max(0,Math.min(1-Number(b.height),(start.y+(e.clientY-start.py)/start.fh)));
      el.style.left=(b.x*100)+"%";el.style.top=(b.y*100)+"%";
    };
    el.onpointerup=()=>{if(start){start=null;renderStudioBlockingControls();scheduleStudioSave();}};
  });
  renderStudioBlockingControls();
}
function renderStudioBlockingControls(){
  const b=blockingSelected(),panel=$("#studioBlockingControls");if(!panel)return;
  if(!b){panel.innerHTML='<div class="muted">Tap a block to edit its size, facing, or note. Drag blocks directly in the frame.</div>';return;}
  panel.innerHTML='<div class="labelrow"><strong>'+esc(b.label||"Block")+'</strong><button id="studioRemoveBlock" class="danger ghost small" type="button">Remove</button></div>'+
    '<div class="grid2"><div><label>Width</label><input id="studioBlockWidth" type="range" min=".05" max=".8" step=".01" value="'+Number(b.width)+'"></div><div><label>Height</label><input id="studioBlockHeight" type="range" min=".05" max=".95" step=".01" value="'+Number(b.height)+'"></div></div>'+
    '<label>Facing</label><select id="studioBlockFacing"><option value="unspecified">Unspecified</option><option value="left">Left</option><option value="right">Right</option><option value="camera">Camera</option><option value="away">Away from camera</option></select>'+
    '<label>Blocking note</label><input id="studioBlockNote" value="'+esc(b.note||"")+'" placeholder="e.g. leaning against wall, foreground">';
  $("#studioBlockFacing").value=b.facing||"unspecified";
  $("#studioBlockWidth").oninput=e=>{b.width=Number(e.target.value);b.x=Math.min(b.x,1-b.width);renderStudioBlocking();scheduleStudioSave();};
  $("#studioBlockHeight").oninput=e=>{b.height=Number(e.target.value);b.y=Math.min(b.y,1-b.height);renderStudioBlocking();scheduleStudioSave();};
  $("#studioBlockFacing").onchange=e=>{b.facing=e.target.value;renderStudioBlocking();scheduleStudioSave();};
  $("#studioBlockNote").oninput=e=>{b.note=e.target.value;scheduleStudioSave();};
  $("#studioRemoveBlock").onclick=()=>{const shot=blockingShot();shot.blocking=(shot.blocking||[]).filter(x=>x.id!==b.id);state.studioBlockingSelectedId=null;renderStudioBlocking();scheduleStudioSave();};
}

async function saveStudioSubject(subject){
  const label=prompt("Saved subject name",subject.label||"Subject");if(label===null)return;
  const copy=structuredClone(subject);copy.label=label||copy.label;
  try{
    await api("/api/prompt-studio/subjects",{method:"POST",body:{subject:copy}});
    const d=await api("/api/prompt-studio/subjects");state.studioSavedSubjects=d.subjects||[];renderStudioSavedSubjects();toast("Subject saved for reuse");
  }catch(e){toast(e.message);}
}
async function studioPlan(){
  const p=await saveStudioProject(false);if(!p)return;
  const button=$("#studioPlan");button.disabled=true;button.textContent="Planning…";msg($("#studioMsg"),"Gemini is building the editable scene plan…");
  try{
    const d=await api("/api/prompt-studio/projects/"+encodeURIComponent(p.id)+"/plan",{method:"POST",body:{}});
    state.studioProject=d.project;renderStudioProject();studioSetSaveState("Saved");msg($("#studioMsg"),"Scene plan updated. Edit any field or shot independently.","ok");
    await loadStudioIndex(p.id);
  }catch(e){msg($("#studioMsg"),e.message,"error");}
  finally{button.disabled=false;button.textContent="✨ Build / Rebuild Plan";}
}
async function studioPromptEdit(scope,targetId,title){
  const instruction=prompt(title||"Describe the change");if(!instruction)return;
  return studioAIEdit(scope,targetId,instruction);
}
async function studioAIEdit(scope,targetId,instruction){
  const p=await saveStudioProject(false);if(!p)return;
  msg($("#studioMsg"),"Applying scoped AI edit…");
  try{
    const d=await api("/api/prompt-studio/projects/"+encodeURIComponent(p.id)+"/edit",{method:"POST",body:{scope,target_id:targetId||"",instruction}});
    state.studioProject=d.project;renderStudioProject();studioSetSaveState("Saved");msg($("#studioMsg"),d.summary||"Scoped edit applied.","ok");
    await loadStudioIndex(p.id);
  }catch(e){msg($("#studioMsg"),e.message,"error");}
}
async function studioCompile(){
  const p=await saveStudioProject(false);if(!p)return;
  const button=$("#studioCompile");button.disabled=true;button.textContent="Compiling…";msg($("#studioMsg"),"Compiling and validating the H3 prompt…");
  try{
    const d=await api("/api/prompt-studio/projects/"+encodeURIComponent(p.id)+"/compile",{method:"POST",body:{}});
    state.studioProject=d.project;renderStudioProject();studioSetSaveState("Saved");
    const valid=d.project.validation?.valid;msg($("#studioMsg"),valid?"Compiled prompt passed validation.":"Compiled, but the validator still found something to review.",valid?"ok":"error");
    await loadStudioIndex(p.id);
  }catch(e){msg($("#studioMsg"),e.message,"error");}
  finally{button.disabled=false;button.textContent="Compile + Validate";}
}
async function studioCheckpoint(){
  const p=await saveStudioProject(false);if(!p)return;
  const label=prompt("Checkpoint label","Manual checkpoint");if(label===null)return;
  try{
    const d=await api("/api/prompt-studio/projects/"+encodeURIComponent(p.id)+"/revisions",{method:"POST",body:{label}});
    state.studioProject=d.project;renderStudioCompiled();toast("Checkpoint saved");
  }catch(e){toast(e.message);}
}
async function studioRestoreRevision(){
  const p=studioProject(),rid=$("#studioRevisionSelect").value;if(!p||!rid)return;
  if(!confirm("Restore this revision? Current unsaved edits will be replaced."))return;
  try{
    const d=await api("/api/prompt-studio/projects/"+encodeURIComponent(p.id)+"/revisions/"+encodeURIComponent(rid)+"/restore",{method:"POST",body:{}});
    state.studioProject=d.project;renderStudioProject();toast("Revision restored");
  }catch(e){toast(e.message);}
}
function studioReferenceTransfer(project){
  if(project.mode!=="r2v")return [];
  const subjects=project.scene?.subjects||[], used=new Set(), byPicture=new Map();
  subjects.forEach(s=>{
    const r=s.reference||{};
    if(["asset","picture_slot"].includes(r.mode))used.add(Number(r.picture_number||1));
    if(r.mode==="asset"&&r.asset_file)byPicture.set(Number(r.picture_number||1),r.asset_file);
  });
  if(!used.size)return [];
  const max=Math.max(...used);
  for(let n=1;n<=max;n++){if(!used.has(n)||!byPicture.has(n))return null;}
  return [...Array(max)].map((_,i)=>({kind:"image",file:byPicture.get(i+1)}));
}
function studioUseInCustom(){
  const p=studioProject();if(!p?.final_prompt){toast("Compile the prompt first.");return;}
  const refs=studioReferenceTransfer(p);
  const currentLoras=selectedLoraArray();
  const snap={
    mode:p.mode,prompt_mode:"custom",prompt:p.final_prompt,
    aspect_ratio:p.aspect_ratio,duration:p.duration,
    megapixels:Number($("#mp")?.value||.7),seed:Number($("#seed")?.value||1),
    randomize_seed:$("#randomSeed")?.checked!==false,
    starting_image:p.mode==="i2v"?(p.starting_image?.asset_file||null):null,
    refs:Array.isArray(refs)?refs:[],loras:currentLoras
  };
  applySnapshot(snap);
  switchTab("generate");
  if(refs===null)toast("Prompt loaded. Picture slots are mixed/opaque, so add the R2V references in Picture order before generating.");
  else toast("Prompt loaded into "+p.mode.toUpperCase()+" Custom.");
}
function wireStudio(){
  $("#studioProjectSelect").onchange=e=>{if(e.target.value)loadStudioProject(e.target.value);};
  $("#studioNewProject").onclick=()=>createStudioProject(true);
  $("#studioDeleteProject").onclick=async()=>{
    const p=studioProject();if(!p)return;
    if(!confirm('Delete Prompt Studio project "'+p.name+'"?'))return;
    try{await api("/api/prompt-studio/projects/"+encodeURIComponent(p.id),{method:"DELETE"});state.studioProject=null;await loadStudioIndex();toast("Prompt project deleted");}catch(e){toast(e.message);}
  };
  ["studioName","studioDuration","studioModel","studioConcept","studioEnvironment","studioVisualStyle","studioSoundscape","studioMusic"].forEach(id=>$("#"+id).addEventListener("input",scheduleStudioSave));
  ["studioMode","studioAspect","studioStartingImage","studioStartingAnalyze"].forEach(id=>$("#"+id).addEventListener("change",()=>{
    pullStudioStatic();
    if(id==="studioMode")renderStudioProject();
    scheduleStudioSave();
  }));
  $("[data-studio-scene-lock]").forEach(el=>el.onchange=e=>{
    const scene=studioScene();if(!scene)return;
    scene.locks=studioToggleLock(scene.locks,e.target.dataset.studioSceneLock,e.target.checked);scheduleStudioSave();
  });
  $("[data-studio-edit-field]").forEach(el=>el.onclick=()=>studioPromptEdit(el.dataset.studioEditField,"","Describe the change to this field only"));
  $("#studioPlan").onclick=studioPlan;
  $("#studioCheckpoint").onclick=studioCheckpoint;
  $("#studioAddSubject").onclick=()=>addStudioSubject();
  $("#studioAddSavedSubject").onclick=()=>{const id=$("#studioSavedSubjectSelect").value,s=state.studioSavedSubjects.find(x=>(x.saved_id||x.id)===id);if(s)addStudioSubject(s);};
  $("#studioAddShot").onclick=addStudioShot;
  $("#studioSetShotCount").onclick=studioSetShotCount;
  $("#studioEvenTiming").onclick=studioEvenTiming;
  $("#studioCloseBlocking").onclick=closeStudioBlocking;
  $("#studioAddBlockingSubject").onclick=addStudioBlockingSubject;
  $("#studioBlockingDialog").addEventListener("click",e=>{if(e.target===$("#studioBlockingDialog"))closeStudioBlocking();});
  $("#studioApplyEdit").onclick=()=>{const instruction=$("#studioEditInstruction").value.trim();if(!instruction){toast("Describe the requested change.");return;}studioAIEdit($("#studioEditScope").value,"",instruction);};
  $("#studioCompile").onclick=studioCompile;
  $("#studioCopyPrompt").onclick=async()=>{const text=$("#studioFinalPrompt").value;if(!text)return;try{await navigator.clipboard.writeText(text);toast("Prompt copied");}catch{toast("Copy failed");}};
  $("#studioUseCustom").onclick=studioUseInCustom;
  $("#studioRestoreRevision").onclick=studioRestoreRevision;
}

function wire(){
  $$("#tabs button").forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
  $$("#modeSeg button").forEach(b=>b.onclick=()=>setMode(b.dataset.value));
  $$("#promptModeSeg button").forEach(b=>b.onclick=()=>setPromptMode(b.dataset.value));
  $("#generate").onclick=generate;
  $("#refreshQueue").onclick=()=>{refreshQueue();refreshProgress();};
  $("#refreshOutputs").onclick=()=>refreshOutputs(true);

  ["prompt","aspect","mp","duration","seed","randomSeed"].forEach(id=>{
    const el=$("#"+id);
    if(!el)return;
    const event=(el.tagName==="SELECT"||el.type==="checkbox")?"change":"input";
    el.addEventListener(event,()=>stashCurrentDraft());
  });

  $("#clearDraft").onclick=()=>{
    const label=state.mode.toUpperCase()+" "+(state.promptMode==="auto"?"Auto":"Custom");
    if(!confirm("Clear the "+label+" draft only?"))return;
    delete state.uiProfiles[profileKey()];
    applyDraft({});
    stashCurrentDraft();
    toast(label+" draft cleared");
  };

  $("#uploadStart").onclick=()=>$("#startFile").click();
  $("#chooseStartAsset").onclick=()=>openAssetPicker("start","image");
  $("#clearStartImage").onclick=()=>{state.startingImage=null;renderStartingImage();stashCurrentDraft();};
  $("#startFile").onchange=async e=>{
    const file=e.target.files?.[0]; if(!file)return;
    try{
      const d=await uploadFile(file);
      await loadAssets(false);
      state.startingImage=d.file;
      renderStartingImage();
      stashCurrentDraft();
      toast("Starting image uploaded");
    }catch(err){toast(err.message);}
    finally{e.target.value="";}
  };

  $$("[data-ref-kind]").forEach(b=>b.onclick=()=>{
    state.refKind=b.dataset.refKind;
    $("#refFile").accept=state.refKind==="image"?"image/*":state.refKind==="video"?"video/*":"audio/*";
    $("#refFile").click();
  });
  $("#refFile").onchange=e=>{
    const file=e.target.files?.[0]; if(file)addUploadedReference(state.refKind,file);
    e.target.value="";
  };
  $("#chooseExistingRef").onclick=()=>openAssetPicker("ref","all");
  $("#clearRefs").onclick=()=>{if(state.refs.length&&confirm("Clear all R2V references?")){state.refs=[];renderRefs();stashCurrentDraft();}};

  $("#closeAssetPicker").onclick=closeAssetPicker;
  $("#assetPickerSearch").oninput=renderAssetPicker;
  $("#assetPicker").addEventListener("click",e=>{if(e.target===$("#assetPicker"))closeAssetPicker();});

  $("#addGenerationLora").onchange=e=>{if(e.target.value)addGenerationLora(e.target.value);};
  $("#goLoras").onclick=()=>switchTab("loras");
  $("#loraSearch").oninput=renderLoras;
  $("#loraSort").onchange=renderLoras;
  $("#syncLoras").onclick=async()=>{try{await syncLoraCatalog(true);}catch(e){toast(e.message);}};
  $("#addLoraVersion").onclick=async()=>{
    const input=$("#newLoraVersion"), source=input.value.trim();
    if(!source){toast("Enter a CivitAI version ID or URL");return;}
    const button=$("#addLoraVersion"); button.disabled=true; button.textContent="Adding…";
    try{
      await api("/api/loras/config",{method:"POST",body:{source,enabled:true}});
      button.textContent="Downloading…";
      await syncLoraCatalog(false);
      input.value="";
      toast("LoRA added to managed catalog");
    }catch(e){toast(e.message);}
    finally{button.disabled=false;button.textContent="Add + Sync";}
  };

  $("#assetSearch").oninput=renderAssetLibrary;
  $("#assetSort").onchange=renderAssetLibrary;
  $("#refreshAssets").onclick=()=>loadAssets(true);

  document.addEventListener("paste",async e=>{
    if(state.tab!=="generate")return;
    const item=[...(e.clipboardData?.items||[])].find(x=>x.type.startsWith("image/"));
    if(!item)return;
    const blob=item.getAsFile(); if(!blob)return;
    const file=new File([blob],"pasted_"+Date.now()+".png",{type:blob.type||"image/png"});
    e.preventDefault();
    try{
      const d=await uploadFile(file);
      await loadAssets(false);
      if(state.mode==="r2v"){
        addRef("image",d.file);
        toast("Pasted image added as R2V reference");
      }else{
        if(state.mode!=="i2v")setMode("i2v");
        state.startingImage=d.file;
        renderStartingImage();
        stashCurrentDraft();
        toast("Pasted image set as I2V start");
      }
    }catch(err){toast(err.message);}
  });

  $("#saveTemplate").onclick=async()=>{
    const name=prompt("Template name"); if(!name)return;
    try{
      stashCurrentDraft();
      await api("/api/templates",{method:"POST",body:{name,value:formSnapshot()}});
      await loadTemplates(); $("#templateSelect").value=name; toast("Template saved");
    }catch(e){toast(e.message);}
  };
  $("#templateSelect").onchange=e=>{if(e.target.value&&state.templates[e.target.value])applySnapshot(state.templates[e.target.value]);};
  $("#deleteTemplate").onclick=async()=>{
    const name=$("#templateSelect").value;if(!name)return;
    try{await api("/api/templates/"+encodeURIComponent(name),{method:"DELETE"});await loadTemplates();toast("Template deleted");}catch(e){toast(e.message);}
  };

  $("#freeMemory").onclick=async()=>{try{await api("/api/system/free",{method:"POST"});toast("Memory release requested");}catch(e){toast(e.message);}};
  $("#interrupt").onclick=async()=>{if(confirm("Interrupt the current Comfy generation?"))try{await api("/api/system/interrupt",{method:"POST"});toast("Interrupt requested");}catch(e){toast(e.message);}};
  $("#savePrompts").onclick=async()=>{
    try{
      await api("/api/system-prompts",{method:"PUT",body:{prompts:{t2v_auto:$("#spT2V").value,i2v_auto:$("#spI2V").value,r2v_auto:$("#spR2V").value}}});
      msg($("#systemMsg"),"System prompts saved. New Auto jobs use them immediately.","ok");
    }catch(e){msg($("#systemMsg"),e.message,"error");}
  };

  wireStudio();
  window.addEventListener("pagehide",()=>{stashCurrentDraft(); if(studioProject())saveStudioProject(false).catch(()=>{});});
}

async function init(){
  wire();
  renderRefs(); renderSelectedLoras(); renderStartingImage();
  await Promise.allSettled([
    refreshInfo(),loadLoras(),loadAssets(true),loadTemplates(),
    refreshQueue(),refreshProgress(),refreshOutputs(true)
  ]);
  await loadUiState();
  renderLoras(); renderAssetLibrary(); renderRefs(); renderStartingImage(); renderSelectedLoras();
  setInterval(refreshProgress,700);
  setInterval(refreshQueue,2500);
  setInterval(refreshInfo,5000);
  setInterval(()=>{if(state.tab==="outputs")refreshOutputs(false);},15000);
}
init();
