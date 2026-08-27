"use strict";

const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];

const state = {
  tab: "generate",
  mode: "t2v",
  promptMode: "auto",
  refs: [],
  selectedLoras: new Map(),
  loraCatalog: [],
  templates: {},
  outputs: [],
  info: null,
  openOutputs: new Set(),
  refKind: "image",
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

async function api(path, options={}){
  const opts={...options, headers:{...(options.headers||{})}};
  if(opts.body && !(opts.body instanceof FormData) && typeof opts.body!=="string"){
    opts.headers["Content-Type"]="application/json";
    opts.body=JSON.stringify(opts.body);
  }
  const r=await fetch(path,opts);
  const text=await r.text();
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
  if(name==="outputs") refreshOutputs();
  if(name==="loras") loadLoras();
  if(name==="system"){ refreshInfo(); loadPrompts(); }
}
function setSegment(host, value){
  $$("button",host).forEach(b=>b.classList.toggle("active",b.dataset.value===value));
}
function setMode(mode){
  state.mode=mode; setSegment($("#modeSeg"),mode);
  $("#i2vBlock").hidden=mode!=="i2v";
  $("#r2vBlock").hidden=mode!=="r2v";
  $("#promptLabel").textContent=state.promptMode==="auto"?"Prompt idea":"Custom prompt";
  refreshInputOptions();
}
function setPromptMode(mode){
  state.promptMode=mode; setSegment($("#promptModeSeg"),mode);
  $("#promptLabel").textContent=mode==="auto"?"Prompt idea":"Custom prompt";
}

async function uploadFile(file){
  const fd=new FormData(); fd.append("file",file,file.name||"upload.bin");
  return api("/api/upload",{method:"POST",body:fd});
}

async function refreshInputOptions(){
  try{
    const d=await api("/api/inputs?kind=image");
    const start=$("#startImage"); const current=start.value;
    start.innerHTML='<option value="">Select existing image…</option>'+
      (d.items||[]).map(x=>'<option value="'+esc(x.file)+'">'+esc(x.file)+'</option>').join("");
    if([...start.options].some(o=>o.value===current)) start.value=current;

    const all=await api("/api/inputs?kind=all");
    const ref=$("#refExisting"); const rc=ref.value;
    ref.innerHTML='<option value="">Existing input…</option>'+
      (all.items||[]).map(x=>{
        const ext=x.file.split(".").pop().toLowerCase();
        const kind=["png","jpg","jpeg","webp","bmp"].includes(ext)?"image":
          ["mp4","mov","mkv","webm","avi"].includes(ext)?"video":"audio";
        return '<option data-kind="'+kind+'" value="'+esc(x.file)+'">'+esc(kind.toUpperCase()+" · "+x.file)+'</option>';
      }).join("");
    if([...ref.options].some(o=>o.value===rc)) ref.value=rc;
  }catch(e){}
}
function renderRefs(){
  const host=$("#refs");
  if(!state.refs.length){host.innerHTML='<div class="muted">No references selected.</div>';return;}
  host.innerHTML=state.refs.map((r,i)=>`
    <div class="ref-card" data-i="${i}">
      <div>
        <strong>${esc(r.kind.toUpperCase())}</strong> · ${esc(r.file)}
        ${r.kind==="video"?`<label class="check"><input class="soundtrack" type="checkbox" ${r.use_soundtrack!==false?"checked":""}> Use video soundtrack</label>`:""}
      </div>
      <button class="danger ghost remove-ref">×</button>
    </div>`).join("");
  $$(".ref-card",host).forEach(row=>{
    const i=Number(row.dataset.i);
    $(".remove-ref",row).onclick=()=>{state.refs.splice(i,1);renderRefs();};
    const cb=$(".soundtrack",row); if(cb) cb.onchange=()=>state.refs[i].use_soundtrack=cb.checked;
  });
}
function canAddRef(kind){
  const cap={image:9,video:3,audio:3}[kind];
  return state.refs.filter(x=>x.kind===kind).length<cap;
}
function addRef(kind,file,useSoundtrack=true){
  if(!canAddRef(kind)){toast("Reference limit reached for "+kind);return false;}
  state.refs.push({kind,file,...(kind==="video"?{use_soundtrack:useSoundtrack}:{})});
  renderRefs(); return true;
}

async function addUploadedReference(kind,file){
  try{
    const d=await uploadFile(file);
    addRef(kind,d.file,true); refreshInputOptions();
  }catch(e){toast("Upload failed: "+e.message);}
}

function selectedLoraArray(){
  return [...state.selectedLoras.values()].map(x=>({
    filename:x.filename,
    strength:Number(x.strength ?? x.recommended_strength ?? 1),
    nickname:x.nickname||x.model_name||x.filename
  }));
}
function renderSelectedLoras(){
  const host=$("#selectedLoras"), arr=selectedLoraArray();
  $("#loraCount").textContent=String(arr.length);
  if(!arr.length){host.innerHTML='<div class="muted">No custom LoRAs selected.</div>';return;}
  host.innerHTML=arr.map(x=>`<div class="ref-card"><div><strong>${esc(x.nickname)}</strong><div class="muted">${esc(x.filename)}</div></div><span class="pill">${Number(x.strength).toFixed(2)}</span></div>`).join("");
}
function renderLoras(){
  const q=$("#loraSearch").value.trim().toLowerCase();
  const host=$("#loraLibrary");
  const items=state.loraCatalog.filter(x=>!q||[x.nickname,x.model_name,x.version_name,x.filename,...(x.tags||[])].join(" ").toLowerCase().includes(q));
  if(!items.length){host.innerHTML='<div class="muted">No matching LoRAs. Sync if you recently changed the catalog.</div>';return;}
  host.innerHTML=items.map((x,i)=>{
    const key=x.filename, selected=state.selectedLoras.get(key);
    const strength=selected?.strength ?? x.recommended_strength ?? 1;
    const triggers=(x.trigger_words||[]).join(", ");
    return `<div class="lora-card" data-file="${esc(key)}">
      <div class="lora-head">
        <input class="pick" type="checkbox" ${selected?"checked":""}>
        <div class="lora-main">
          <div class="lora-name">${esc(x.nickname||x.model_name||x.version_name||x.filename)}</div>
          <div class="lora-file">${esc(x.filename)}</div>
          <div>${x.managed?'<span class="pill">managed</span>':'<span class="pill">local</span>'}
          ${x.version_id?'<span class="pill">v'+esc(x.version_id)+'</span>':""}</div>
        </div>
      </div>
      <div class="lora-controls">
        <label>Strength</label><input class="strength" type="number" step=".05" min="-3" max="3" value="${esc(strength)}">
      </div>
      ${triggers?'<div class="triggers"><strong>Triggers:</strong> '+esc(triggers)+'</div>':""}
      ${(x.notes||[]).length?'<div class="triggers"><strong>Notes:</strong> '+esc((x.notes||[]).join(" · "))+'</div>':""}
      ${x.managed?`<details class="meta-editor">
        <summary>Edit metadata</summary>
        <label>Nickname</label><input class="meta-nickname" value="${esc(x.nickname||"")}">
        <label>Recommended strength</label><input class="meta-strength" type="number" step=".05" value="${esc(x.recommended_strength??1)}">
        <label>Trigger words <span class="muted">(comma-separated)</span></label><input class="meta-triggers" value="${esc((x.trigger_words||[]).join(", "))}">
        <label>Tags <span class="muted">(comma-separated)</span></label><input class="meta-tags" value="${esc((x.tags||[]).join(", "))}">
        <label>Personal notes <span class="muted">(one per line)</span></label><textarea class="meta-notes" rows="3">${esc((x.notes||[]).join("\n"))}</textarea>
        <button class="secondary save-meta">Save metadata</button>
      </details>`:""}
    </div>`;
  }).join("");
  $$(".lora-card",host).forEach(card=>{
    const file=card.dataset.file;
    const item=state.loraCatalog.find(x=>x.filename===file);
    const pick=$(".pick",card), strength=$(".strength",card);
    const sync=()=>{
      if(pick.checked){
        state.selectedLoras.set(file,{...item,strength:Number(strength.value||item.recommended_strength||1)});
      }else state.selectedLoras.delete(file);
      renderSelectedLoras();
    };
    pick.onchange=sync;
    strength.onchange=()=>{if(pick.checked)sync();};
    const save=$(".save-meta",card);
    if(save) save.onclick=async()=>{
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
    state.loraCatalog=d.items||[]; renderLoras(); renderSelectedLoras();
  }catch(e){$("#loraLibrary").innerHTML='<div class="message error">'+esc(e.message)+'</div>';}
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

function formSnapshot(){
  return {
    mode:state.mode,prompt_mode:state.promptMode,prompt:$("#prompt").value,
    aspect_ratio:$("#aspect").value,megapixels:Number($("#mp").value),
    duration:Number($("#duration").value),seed:Number($("#seed").value),
    randomize_seed:$("#randomSeed").checked,starting_image:$("#startImage").value||null,
    refs:state.refs,loras:selectedLoraArray()
  };
}
function applySnapshot(v){
  if(!v) return;
  setMode(v.mode||"t2v"); setPromptMode(v.prompt_mode||"auto");
  $("#prompt").value=v.prompt||v.prompt_idea||"";
  if(v.aspect_ratio) $("#aspect").value=v.aspect_ratio;
  if(v.megapixels!=null) $("#mp").value=v.megapixels;
  if(v.duration!=null) $("#duration").value=v.duration;
  if(v.seed!=null) $("#seed").value=v.seed;
  $("#randomSeed").checked=v.randomize_seed!==false;
  state.refs=Array.isArray(v.refs)?structuredClone(v.refs):[];
  renderRefs();
  state.selectedLoras.clear();
  (v.loras||[]).forEach(x=>{if(x.filename)state.selectedLoras.set(x.filename,{...x});});
  renderSelectedLoras(); renderLoras();
  if(v.starting_image) $("#startImage").value=v.starting_image;
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
    if(!(d.items||[]).length){host.innerHTML='<div class="muted">Queue empty.</div>';return;}
    host.innerHTML=d.items.map(x=>`<div class="qitem" data-pid="${esc(x.prompt_id)}"><div><strong>${esc((x.record.mode||"").toUpperCase())} · ${esc((x.record.prompt_mode||"").toUpperCase())}</strong><div class="muted">${esc((x.record.prompt_idea||x.record.prompt||"").slice(0,130))}</div></div><button class="danger cancel">Cancel</button></div>`).join("");
    $$(".qitem",host).forEach(row=>$(".cancel",row).onclick=async()=>{
      try{await api("/api/cancel/"+encodeURIComponent(row.dataset.pid),{method:"POST"});await refreshQueue();}catch(e){toast(e.message);}
    });
  }catch(e){}
}

function copyText(text){
  if(navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(()=>toast("Copied")).catch(()=>toast("Copy failed"));
  else toast("Clipboard unavailable");
}
async function refreshOutputs(){
  try{
    state.openOutputs=new Set($$("details.output[open]").map(x=>x.dataset.file));
    const d=await api("/api/outputs"); state.outputs=d.items||[];
    const host=$("#outputs");
    if(!state.outputs.length){host.innerHTML='<div class="muted">No completed videos with audio yet.</div>';return;}
    host.innerHTML=state.outputs.map((x,i)=>{
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
        try{await api("/api/outputs/"+item.file.split("/").map(encodeURIComponent).join("/"),{method:"DELETE"});refreshOutputs();}catch(e){toast(e.message);}
      };
    });
  }catch(e){$("#outputs").innerHTML='<div class="message error">'+esc(e.message)+'</div>';}
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

function wire(){
  $$("#tabs button").forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
  $$("#modeSeg button").forEach(b=>b.onclick=()=>setMode(b.dataset.value));
  $$("#promptModeSeg button").forEach(b=>b.onclick=()=>setPromptMode(b.dataset.value));
  $("#generate").onclick=generate;
  $("#refreshQueue").onclick=()=>{refreshQueue();refreshProgress();};
  $("#refreshOutputs").onclick=refreshOutputs;
  $("#goLoras").onclick=()=>switchTab("loras");
  $("#loraSearch").oninput=renderLoras;

  $("#uploadStart").onclick=()=>$("#startFile").click();
  $("#startFile").onchange=async e=>{
    const file=e.target.files?.[0]; if(!file)return;
    try{
      const d=await uploadFile(file); await refreshInputOptions(); $("#startImage").value=d.file; $("#startSelected").textContent=d.file; toast("Starting image uploaded");
    }catch(err){toast(err.message);} finally{e.target.value="";}
  };
  $("#startImage").onchange=()=>$("#startSelected").textContent=$("#startImage").value;

  $$("[data-ref-kind]").forEach(b=>b.onclick=()=>{
    state.refKind=b.dataset.refKind;
    $("#refFile").accept=state.refKind==="image"?"image/*":state.refKind==="video"?"video/*":"audio/*";
    $("#refFile").click();
  });
  $("#refFile").onchange=e=>{
    const file=e.target.files?.[0]; if(file)addUploadedReference(state.refKind,file);
    e.target.value="";
  };
  $("#addExistingRef").onclick=()=>{
    const sel=$("#refExisting"), opt=sel.selectedOptions[0];
    if(!opt||!sel.value)return;
    addRef(opt.dataset.kind||"image",sel.value,true);
  };

  document.addEventListener("paste",async e=>{
    if(state.tab!=="generate")return;
    const item=[...(e.clipboardData?.items||[])].find(x=>x.type.startsWith("image/"));
    if(!item)return;
    const blob=item.getAsFile(); if(!blob)return;
    const file=new File([blob],"pasted_"+Date.now()+".png",{type:blob.type||"image/png"});
    e.preventDefault();
    try{
      const d=await uploadFile(file); await refreshInputOptions();
      if(state.mode==="r2v"){addRef("image",d.file);toast("Pasted image added as R2V reference");}
      else {setMode("i2v");$("#startImage").value=d.file;$("#startSelected").textContent=d.file;toast("Pasted image set as I2V start");}
    }catch(err){toast(err.message);}
  });

  $("#saveTemplate").onclick=async()=>{
    const name=prompt("Template name"); if(!name)return;
    try{await api("/api/templates",{method:"POST",body:{name,value:formSnapshot()}});await loadTemplates();$("#templateSelect").value=name;toast("Template saved");}catch(e){toast(e.message);}
  };
  $("#templateSelect").onchange=e=>{if(e.target.value&&state.templates[e.target.value])applySnapshot(state.templates[e.target.value]);};
  $("#deleteTemplate").onclick=async()=>{
    const name=$("#templateSelect").value;if(!name)return;
    try{await api("/api/templates/"+encodeURIComponent(name),{method:"DELETE"});await loadTemplates();toast("Template deleted");}catch(e){toast(e.message);}
  };

  $("#syncLoras").onclick=async()=>{
    try{await syncLoraCatalog(true);}catch(e){toast(e.message);}
  };
  $("#addLoraVersion").onclick=async()=>{
    const input=$("#newLoraVersion"), source=input.value.trim();
    if(!source){toast("Enter a CivitAI version ID or URL");return;}
    const b=$("#addLoraVersion");b.disabled=true;b.textContent="Adding…";
    try{
      await api("/api/loras/config",{method:"POST",body:{source,enabled:true}});
      b.textContent="Downloading…";
      await syncLoraCatalog(false);
      input.value="";
      toast("LoRA added to managed catalog");
    }catch(e){toast(e.message);}
    finally{b.disabled=false;b.textContent="Add + Sync";}
  };

  $("#freeMemory").onclick=async()=>{try{await api("/api/system/free",{method:"POST"});toast("Memory release requested");}catch(e){toast(e.message);}};
  $("#interrupt").onclick=async()=>{if(confirm("Interrupt the current Comfy generation?"))try{await api("/api/system/interrupt",{method:"POST"});toast("Interrupt requested");}catch(e){toast(e.message);}};
  $("#savePrompts").onclick=async()=>{
    try{
      await api("/api/system-prompts",{method:"PUT",body:{prompts:{t2v_auto:$("#spT2V").value,i2v_auto:$("#spI2V").value,r2v_auto:$("#spR2V").value}}});
      msg($("#systemMsg"),"System prompts saved. New Auto jobs use them immediately.","ok");
    }catch(e){msg($("#systemMsg"),e.message,"error");}
  };
}

async function init(){
  wire(); setMode("t2v");setPromptMode("auto");renderRefs();renderSelectedLoras();
  await Promise.allSettled([refreshInfo(),refreshInputOptions(),loadLoras(),loadTemplates(),refreshQueue(),refreshProgress(),refreshOutputs()]);
  setInterval(refreshProgress,700);
  setInterval(refreshQueue,2500);
  setInterval(refreshInfo,5000);
  setInterval(()=>{if(state.tab==="outputs")refreshOutputs();},15000);
}
init();
