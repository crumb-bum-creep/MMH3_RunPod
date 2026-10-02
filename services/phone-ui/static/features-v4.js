"use strict";

// MMH3 v4 overlay: Gemini prompt editing, LoRA quick install, and prompt
// defaults. Layers onto app.js / features-v3.js the same way v3 does.
(function(){
  state.loraAvailable=state.loraAvailable||[];
  const edit={undo:[],busy:false};
  const lora={pollTimer:null,installing:new Set()};

  /* ---------- Gemini prompt edit ---------- */

  function ensurePromptEditUi(){
    if($("#promptEditBlock"))return;
    const block=document.createElement("div");
    block.id="promptEditBlock";
    block.className="prompt-edit-v4";
    block.innerHTML=`
      <form id="promptEditForm" class="prompt-edit-row-v4" autocomplete="off">
        <input id="promptEditInput" type="text" enterkeyhint="send" aria-label="Describe a change for Gemini">
        <button id="promptEditApply" class="secondary" type="submit">Edit</button>
      </form>
      <div class="prompt-edit-status-v4" aria-live="polite">
        <span id="promptEditStatus" class="muted"></span>
        <button id="promptEditUndo" class="ghost small" type="button" hidden>Undo</button>
      </div>`;
    $("#prompt").insertAdjacentElement("afterend",block);
    $("#promptEditForm").onsubmit=e=>{e.preventDefault();runPromptEdit();};
    $("#promptEditUndo").onclick=undoPromptEdit;
  }

  function updatePromptEditUi(){
    const input=$("#promptEditInput");
    if(!input)return;
    input.placeholder=state.promptMode==="auto"
      ?"✨ Ask Gemini to change this idea…"
      :"✨ Ask Gemini to change this prompt…";
    // Undo history belongs to the draft it was made in.
    const key=profileKey();
    edit.undo=edit.undo.filter(x=>x.key===key);
    $("#promptEditUndo").hidden=!edit.undo.length;
    if(!edit.undo.length&&!edit.busy)setEditStatus("");
  }

  function setEditStatus(text,type=""){
    const el=$("#promptEditStatus");
    if(!el)return;
    el.textContent=text;
    el.className=type==="error"?"message error":"muted";
  }

  function refTags(){
    const counts={image:0,video:0,audio:0},names={image:"Picture",video:"Video",audio:"Audio"};
    return (state.refs||[]).map(r=>{counts[r.kind]=(counts[r.kind]||0)+1;return `<${names[r.kind]||"Ref"} ${counts[r.kind]}>`;});
  }

  async function runPromptEdit(){
    const input=$("#promptEditInput"),button=$("#promptEditApply"),area=$("#prompt");
    const instruction=input.value.trim();
    if(edit.busy)return;
    if(!instruction){input.focus();return;}
    const key=profileKey(),before=area.value;
    edit.busy=true;input.disabled=true;button.disabled=true;button.textContent="…";
    setEditStatus("Asking Gemini…");
    try{
      const d=await api("/api/prompt/edit",{method:"POST",body:{
        text:before,instruction,mode:state.mode,prompt_mode:state.promptMode,
        duration:Number($("#duration")?.value||0)||undefined,
        aspect_ratio:$("#aspect")?.value||"",
        refs:state.mode==="r2v"?refTags():[],
      }});
      // The user may have switched drafts or typed while Gemini was working.
      if(profileKey()!==key||area.value!==before){
        setEditStatus("Draft changed while Gemini was working · revision discarded","error");
        return;
      }
      edit.undo.push({key,text:before});
      if(edit.undo.length>20)edit.undo.shift();
      area.value=d.text;
      area.dispatchEvent(new Event("input",{bubbles:true}));
      area.classList.remove("prompt-flash-v4");void area.offsetWidth;area.classList.add("prompt-flash-v4");
      input.value="";
      const model=String(d.model||"Gemini").split("/").pop();
      setEditStatus(`Edited by ${model} · ${d.seconds}s`);
      $("#promptEditUndo").hidden=false;
    }catch(e){
      setEditStatus(e.message,"error");
    }finally{
      edit.busy=false;input.disabled=false;button.disabled=false;button.textContent="Edit";
    }
  }

  function undoPromptEdit(){
    const key=profileKey(),i=edit.undo.map(x=>x.key).lastIndexOf(key);
    if(i<0)return;
    const [entry]=edit.undo.splice(i,1),area=$("#prompt");
    area.value=entry.text;
    area.dispatchEvent(new Event("input",{bubbles:true}));
    setEditStatus("Reverted Gemini edit");
    $("#promptEditUndo").hidden=!edit.undo.some(x=>x.key===key);
  }

  /* ---------- LoRA quick install ---------- */

  function ensureQuickInstallUi(){
    if($("#loraQuickInstall"))return;
    const library=$("#loraLibrary");
    const block=document.createElement("details");
    block.id="loraQuickInstall";
    block.className="lora-quick-v4";
    block.hidden=true;
    let open=true;
    try{open=localStorage.getItem("mmh3.loraQuick.open")!=="0";}catch{}
    block.open=open;
    block.innerHTML=`
      <summary><strong>Quick install</strong> <span id="loraQuickCount" class="pill">0</span><span class="muted lora-quick-hint-v4">In your catalog, not downloaded yet</span></summary>
      <div id="loraQuickList" class="stack"></div>`;
    library.insertAdjacentElement("beforebegin",block);
    block.addEventListener("toggle",()=>{try{localStorage.setItem("mmh3.loraQuick.open",block.open?"1":"0");}catch{}});

    const hint=$("#tab-loras .labelrow .muted");
    if(hint)hint.textContent="Catalog LoRAs download only when you tap Install. Select installed LoRAs for jobs from Generate.";
    const add=$("#addLoraVersion");
    if(add){add.textContent="Add + Install";add.onclick=addAndInstall;}
  }

  function loraName(x){return x.nickname||x.model_name||x.version_name||x.filename||("CivitAI "+x.version_id);}

  function renderQuickInstall(){
    ensureQuickInstallUi();
    const block=$("#loraQuickInstall"),host=$("#loraQuickList");
    const q=($("#loraSearch")?.value||"").trim().toLowerCase();
    const all=[...(state.loraAvailable||[])].sort((a,b)=>loraName(a).localeCompare(loraName(b),undefined,{sensitivity:"base"}));
    const items=all.filter(x=>!q||[loraName(x),x.filename,...(x.tags||[]),...(x.trigger_words||[])].join(" ").toLowerCase().includes(q));
    block.hidden=!all.length;
    $("#loraQuickCount").textContent=String(all.length);
    if(!items.length){host.innerHTML='<div class="muted">No matching LoRAs to install.</div>';return;}
    host.innerHTML=items.map(x=>{
      const vid=Number(x.version_id),busy=lora.installing.has(vid)||x.install_status==="installing";
      const failed=!busy&&x.install_status==="error";
      const tags=(x.tags||[]).slice(0,3).map(t=>`<span class="tag-chip">${esc(t)}</span>`).join("");
      return `<div class="lora-quick-row-v4" data-vid="${vid}">
        <div class="lora-quick-main-v4">
          <strong>${esc(loraName(x))}</strong>
          <div class="lora-quick-meta-v4">${tags}<span class="muted">Rec. ${Number(x.recommended_strength??1).toFixed(2)}</span></div>
          ${failed?`<div class="message error">${esc(x.install_error||"Download failed")}</div>`:""}
        </div>
        <button class="${busy?"ghost":"secondary"} small lora-install-v4" type="button" ${busy?"disabled":""}>${busy?"Downloading…":failed?"Retry":"Install"}</button>
      </div>`;
    }).join("");
    $$(".lora-quick-row-v4",host).forEach(row=>{
      $(".lora-install-v4",row).onclick=()=>installLora(Number(row.dataset.vid));
    });
  }

  async function installLora(vid){
    if(!vid)return;
    lora.installing.add(vid);
    renderQuickInstall();
    try{
      await api("/api/loras/install/"+encodeURIComponent(vid),{method:"POST"});
      schedulePoll(1500);
    }catch(e){lora.installing.delete(vid);renderQuickInstall();toast(e.message);}
  }

  async function addAndInstall(){
    const input=$("#newLoraVersion"),button=$("#addLoraVersion"),source=input.value.trim();
    if(!source){toast("Enter a CivitAI version ID or URL");return;}
    button.disabled=true;button.textContent="Adding…";
    try{
      const d=await api("/api/loras/config",{method:"POST",body:{source,enabled:true}});
      await api("/api/loras/sync",{method:"POST",body:{install:[]}});
      input.value="";
      await installLora(Number(d.lora?.version_id));
      toast("Added · downloading in the background");
    }catch(e){toast(e.message);}
    finally{button.disabled=false;button.textContent="Add + Install";}
  }

  function schedulePoll(ms=3000){
    clearTimeout(lora.pollTimer);
    lora.pollTimer=setTimeout(()=>loadLoras(),ms);
  }

  loadLoras=async function(){
    try{
      const d=await api("/api/loras");
      state.loraCatalog=d.items||[];
      state.loraAvailable=d.available||[];
      for(const [file,selected] of state.selectedLoras.entries()){
        const current=state.loraCatalog.find(x=>x.filename===file);
        if(current)state.selectedLoras.set(file,{...current,strength:selected.strength});
      }
      const now=new Set((d.installing||[]).map(Number));
      for(const vid of lora.installing){
        if(now.has(vid))continue;
        const row=state.loraAvailable.find(x=>Number(x.version_id)===vid);
        if(!row){
          const done=state.loraCatalog.find(x=>Number(x.version_id)===vid);
          toast((done?loraName(done):"LoRA")+" installed");
        }else if(row.install_status==="error"){
          toast("Install failed: "+loraName(row));
        }
      }
      lora.installing=now;
      renderLoras();renderSelectedLoras();renderQuickInstall();
      if(now.size)schedulePoll();
    }catch(e){
      const host=$("#loraLibrary");
      if(host)host.innerHTML='<div class="message error">'+esc(e.message)+'</div>';
      if(lora.installing.size)schedulePoll(6000);
    }
  };

  const originalRenderLoras=renderLoras;
  renderLoras=function(){originalRenderLoras();renderQuickInstall();};
  $("#loraSearch").oninput=renderLoras;
  $("#loraSort").onchange=renderLoras;

  /* ---------- System prompt defaults ---------- */

  let promptDefaults={};
  loadPrompts=async function(){
    try{
      const d=await api("/api/system-prompts"),p=d.prompts||{};
      promptDefaults=d.defaults||{};
      $("#spT2V").value=p.t2v_auto||"";$("#spI2V").value=p.i2v_auto||"";$("#spR2V").value=p.r2v_auto||"";
    }catch(e){msg($("#systemMsg"),e.message,"error");}
  };

  function ensurePromptDefaultsUi(){
    const save=$("#savePrompts");
    if(!save||$("#loadDefaultPrompts"))return;
    const wrap=document.createElement("div");
    wrap.className="prompt-actions-v4";
    const defaults=document.createElement("button");
    defaults.id="loadDefaultPrompts";
    defaults.className="ghost small";
    defaults.type="button";
    defaults.textContent="Load defaults";
    save.replaceWith(wrap);
    wrap.append(defaults,save);
    const hint=document.createElement("div");
    hint.className="muted";
    hint.textContent="Defaults follow MiniMax's official H3 prompt-writing guides. Loading them does not save until you tap Save.";
    $("#spT2V").previousElementSibling?.insertAdjacentElement("beforebegin",hint);
    defaults.onclick=async()=>{
      if(!Object.keys(promptDefaults).length)await loadPrompts();
      if(!confirm("Replace all three text boxes with the image defaults? Your current prompts stay saved until you tap Save."))return;
      $("#spT2V").value=promptDefaults.t2v_auto||"";$("#spI2V").value=promptDefaults.i2v_auto||"";$("#spR2V").value=promptDefaults.r2v_auto||"";
      msg($("#systemMsg"),"Defaults loaded · tap Save to use them for new Auto jobs.");
    };
  }

  /* ---------- wiring ---------- */

  // Every path that switches draft (mode tabs, restored UI state, Reuse /
  // Remix from Outputs) rewrites #promptLabel, so watch it instead of
  // wrapping each of those functions.
  new MutationObserver(updatePromptEditUi).observe($("#promptLabel"),{childList:true,characterData:true,subtree:true});

  ensurePromptEditUi();
  ensureQuickInstallUi();
  ensurePromptDefaultsUi();
  updatePromptEditUi();
  // app.js init() fetched the LoRA list before this overlay existed.
  setTimeout(()=>{loadLoras();updatePromptEditUi();},150);
})();
