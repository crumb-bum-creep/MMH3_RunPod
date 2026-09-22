"use strict";

// MMH3 v3 feature overlay. Keep the d2103 app.js intact and layer the new
// controls onto its stable draft/template machinery.
(function(){
  const STOCK="stock_convrot_int8";
  const EROS="eros_beta5_int8";
  const FALLBACK_PROFILE="balanced8";
  state.endingImage=state.endingImage||null;
  state.generationProfileData=state.generationProfileData||{profiles:{},default:FALLBACK_PROFILE,samplers:["euler"],schedulers:["simple"]};
  state.profileLoadToken=0;
  state.runtimeControls=state.runtimeControls||{memory_protection:true,output_naming_template:"MMH3/{mode}"};

  function ensureUi(){
    if(!$("#baseCheckpoint")){
      const promptModes=$("#promptModeSeg");
      const block=document.createElement("div");
      block.className="checkpoint-block";
      block.innerHTML=`
        <div class="labelrow"><label for="baseCheckpoint">Base Checkpoint</label><span id="checkpointStatus" class="muted">Stock ready</span></div>
        <select id="baseCheckpoint">
          <option value="${STOCK}">Stock H3 ConvRot INT8</option>
          <option value="${EROS}">Eros Max Beta5 INT8</option>
        </select>`;
      promptModes.insertAdjacentElement("afterend",block);
      $("#baseCheckpoint").onchange=()=>{stashCurrentDraft();updateGenerationAvailability();};
    }

    if(!$("#generationProfile")){
      const checkpoint=$("#baseCheckpoint")?.closest(".checkpoint-block");
      const block=document.createElement("div");
      block.className="checkpoint-block profile-block";
      block.innerHTML=`
        <div class="labelrow"><label for="generationProfile">Generation Profile</label><span id="profileStatus" class="muted">Checking…</span></div>
        <select id="generationProfile"><option value="">Loading profiles…</option></select>`;
      checkpoint?.insertAdjacentElement("afterend",block);
      $("#generationProfile").onchange=()=>{
        applySelectedProfileDefaults();
        stashCurrentDraft();
        updateGenerationAvailability();
      };
    }

    if(!$("#generationTuning")){
      const profile=$("#generationProfile")?.closest(".profile-block");
      const details=document.createElement("details");
      details.id="generationTuning";
      details.className="checkpoint-block";
      details.innerHTML=`
        <summary><strong>Advanced generation tuning</strong> <span class="muted">sampler · scheduler · sigma recipe</span></summary>
        <div class="grid2 padded-top">
          <div><label>Sampler</label><select id="genSampler"></select></div>
          <div><label>Schedule type</label><select id="genScheduleType"><option value="basic">BasicScheduler</option><option value="beta">BetaSamplingScheduler</option></select></div>
          <div><label>Steps</label><input id="genSteps" type="number" min="1" max="50" step="1"></div>
          <div><label>Turbo strength</label><input id="genStrength" type="number" min="0" max="2" step=".05"></div>
        </div>
        <div id="genBasicFields" class="grid2">
          <div><label>Basic scheduler</label><select id="genScheduler"></select></div>
          <div><label>Video sigma shift</label><input id="genShiftVideo" type="number" min="0" max="30" step=".5"></div>
          <div><label>Audio sigma shift</label><input id="genShiftAudio" type="number" min="0" max="30" step=".5"></div>
        </div>
        <div id="genBetaFields">
          <div class="grid2">
            <div><label>Beta alpha</label><input id="genBetaAlpha" type="number" min="0" max="2" step=".01"></div>
            <div><label>Beta beta</label><input id="genBetaBeta" type="number" min="0" max="2" step=".01"></div>
          </div>
          <label class="check"><input id="genExtendEnabled" type="checkbox"> Extend intermediate sigmas</label>
          <div id="genExtendFields" class="grid2">
            <div><label>Extend steps</label><input id="genExtendSteps" type="number" min="1" max="20" step="1"></div>
            <div><label>Spacing</label><select id="genExtendSpacing"><option value="linear">linear</option><option value="cosine">cosine</option><option value="sine">sine</option></select></div>
            <div><label>Start sigma</label><input id="genExtendStart" type="number" min="0" step=".05"></div>
            <div><label>End sigma</label><input id="genExtendEnd" type="number" min="0" step=".05"></div>
          </div>
        </div>
        <div id="genR2VFields">
          <label>R2V reference sizing</label>
          <select id="genRefImageSize"><option value="max">max</option><option value="match">match</option></select>
        </div>
        <div class="media-row"><button id="resetGenerationTuning" class="secondary" type="button">Reset to profile</button><span class="muted">Saved per Auto/Custom draft and with each output.</span></div>`;
      profile?.insertAdjacentElement("afterend",details);
      const ids=["genSampler","genScheduleType","genScheduler","genSteps","genStrength","genShiftVideo","genShiftAudio","genBetaAlpha","genBetaBeta","genExtendEnabled","genExtendSteps","genExtendStart","genExtendEnd","genExtendSpacing","genRefImageSize"];
      ids.forEach(id=>{
        const el=$("#"+id); if(!el)return;
        el.addEventListener(el.type==="number"?"input":"change",()=>{
          updateTuningVisibility();
          stashCurrentDraft();
        });
      });
      $("#resetGenerationTuning").onclick=()=>{applySelectedProfileDefaults();stashCurrentDraft();};
    }

    if(!$("#endSelected")){
      const block=$("#i2vBlock");
      const wrap=document.createElement("div");
      wrap.className="end-frame-block";
      wrap.innerHTML=`
        <div class="labelrow"><label>Ending image <span class="muted">optional</span></label><button id="clearEndImage" class="ghost small" type="button">Clear</button></div>
        <div class="media-row">
          <button id="uploadEnd" type="button">Upload</button>
          <button id="chooseEndAsset" class="secondary" type="button">Choose existing</button>
        </div>
        <input id="endFile" type="file" accept="image/*" hidden>
        <div id="endSelected" class="selected-asset-slot muted">No ending image selected.</div>`;
      block.appendChild(wrap);
      $("#uploadEnd").onclick=()=>$("#endFile").click();
      $("#chooseEndAsset").onclick=()=>openAssetPicker("end","image");
      $("#clearEndImage").onclick=()=>{state.endingImage=null;renderEndingImage();stashCurrentDraft();};
      $("#endFile").onchange=async e=>{
        const file=e.target.files?.[0]; if(!file)return;
        try{
          const d=await uploadFile(file);
          await loadAssets(false);
          state.endingImage=d.file;
          renderEndingImage();
          stashCurrentDraft();
          toast("Ending image uploaded");
        }catch(err){toast(err.message);}
        finally{e.target.value="";}
      };
    }

    if(!$("#modelProvisioning")){
      const system=$("#tab-system");
      const cards=$(".card",system);
      const card=document.createElement("div");
      card.className="card";
      card.innerHTML=`
        <div class="labelrow"><div><strong>Core model provisioning</strong><div class="muted">Stock models unlock generation first; Eros downloads last.</div></div><span id="provisioningStage" class="muted"></span></div>
        <div id="provisioningOverall" class="provisioning-overall"></div>
        <div id="modelProvisioning" class="model-provisioning stack"></div>`;
      if(cards[0])cards[0].insertAdjacentElement("afterend",card); else system.prepend(card);
    }

    if(!$("#runtimeControlsVNext")){
      const system=$("#tab-system");
      const card=document.createElement("div");
      card.className="card";
      card.id="runtimeControlsVNext";
      card.innerHTML=`
        <div class="labelrow"><div><strong>Runtime controls</strong><div class="muted">Persistent across pod migrations.</div></div></div>
        <label class="check"><input id="memoryProtectionToggle" type="checkbox" checked> Memory protection <span class="muted">blocks unsafe cross-family queueing and enables automatic RAM cleanup</span></label>
        <label for="outputNamingTemplate">Output naming template</label>
        <input id="outputNamingTemplate" value="MMH3/{mode}" placeholder="MMH3/{mode}">
        <div class="muted">Tokens: {mode} {prompt_mode} {profile} {checkpoint} {seed} {date} {time}</div>
        <div class="media-row"><button id="saveRuntimeControls" class="secondary" type="button">Save runtime controls</button><span id="runtimeControlsStatus" class="muted"></span></div>`;
      system.appendChild(card);
      $("#memoryProtectionToggle").onchange=()=>saveRuntimeControls({memory_protection:$("#memoryProtectionToggle").checked});
      $("#saveRuntimeControls").onclick=()=>saveRuntimeControls({output_naming_template:$("#outputNamingTemplate").value});
    }
  }

  async function loadRuntimeControls(){
    try{
      const value=await api("/api/runtime-controls");
      state.runtimeControls=value||state.runtimeControls;
      const mem=$("#memoryProtectionToggle");
      if(mem)mem.checked=value.memory_protection!==false;
      const naming=$("#outputNamingTemplate");
      if(naming)naming.value=value.output_naming_template||"MMH3/{mode}";
      const status=$("#runtimeControlsStatus");
      if(status)status.textContent=value.memory_protection===false?"Protection OFF":"Protection ON";
    }catch(e){
      const status=$("#runtimeControlsStatus");
      if(status)status.textContent="Unavailable";
    }
  }

  async function saveRuntimeControls(update){
    try{
      const value=await api("/api/runtime-controls",{method:"PUT",body:update});
      state.runtimeControls=value;
      const mem=$("#memoryProtectionToggle");
      if(mem)mem.checked=value.memory_protection!==false;
      const naming=$("#outputNamingTemplate");
      if(naming)naming.value=value.output_naming_template||"MMH3/{mode}";
      const status=$("#runtimeControlsStatus");
      if(status)status.textContent=value.memory_protection===false?"Protection OFF":"Saved";
      toast("Runtime controls saved");
    }catch(e){toast(e.message);}
  }

  function renderEndingImage(){
    const host=$("#endSelected");
    if(!host)return;
    const file=state.endingImage;
    if(!file){
      host.className="selected-asset-slot muted";
      host.innerHTML="No ending image selected.";
      return;
    }
    const thumb=assetThumb(file);
    host.className="selected-asset-slot";
    host.innerHTML=`<div class="selected-asset-inner">
      <div class="asset-mini">
        ${thumb?`<img src="${esc(thumb)}" alt="">`:'<div class="asset-icon">IMG</div>'}
        <div class="asset-mini-text"><strong>${esc(assetName(file))}</strong><div class="muted">${esc(file)}</div></div>
      </div>
      <button class="ghost small rename-end-asset" type="button">Name</button>
    </div>`;
    $(".rename-end-asset",host).onclick=()=>renameAsset(file);
  }

  function erosProgress(){
    const p=state.info?.provisioning||{};
    const rows=p.model_progress||{};
    return rows.eros_beta5_int8||null;
  }

  function erosReady(){
    const row=erosProgress();
    return !!(state.info?.provisioning?.addon_ready || row?.status==="ready");
  }

  function legacyProfileAlias(value){
    const raw=String(value||"").toLowerCase();
    if(["balanced","quality","8step"].includes(raw))return "balanced8";
    if(state.mode==="r2v"&&["fast","legacy","4step"].includes(raw))return "legacy_exact";
    if(["fast","4step"].includes(raw))return "fast4";
    if(raw==="legacy")return "legacy_exact";
    return raw;
  }

  function selectOptions(el,values,current){
    if(!el)return;
    const list=[...new Set((values||[]).filter(Boolean).map(String))];
    if(current&&!list.includes(String(current)))list.push(String(current));
    el.innerHTML=list.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join("");
    if(current)el.value=String(current);
  }

  function readGenerationSettings(){
    return {
      sampler:$("#genSampler")?.value||"euler",
      schedule_type:$("#genScheduleType")?.value||"basic",
      scheduler:$("#genScheduler")?.value||"simple",
      steps:Number($("#genSteps")?.value||8),
      strength:Number($("#genStrength")?.value||1),
      shift_video:Number($("#genShiftVideo")?.value||6),
      shift_audio:Number($("#genShiftAudio")?.value||3),
      beta_alpha:Number($("#genBetaAlpha")?.value||.6),
      beta_beta:Number($("#genBetaBeta")?.value||.6),
      extend_enabled:$("#genExtendEnabled")?.checked===true,
      extend_steps:Number($("#genExtendSteps")?.value||2),
      extend_start:Number($("#genExtendStart")?.value||.8),
      extend_end:Number($("#genExtendEnd")?.value||0),
      extend_spacing:$("#genExtendSpacing")?.value||"linear",
      ref_image_size:$("#genRefImageSize")?.value||"max"
    };
  }

  function updateTuningVisibility(){
    const beta=$("#genScheduleType")?.value==="beta";
    if($("#genBasicFields"))$("#genBasicFields").hidden=beta;
    if($("#genBetaFields"))$("#genBetaFields").hidden=!beta;
    if($("#genExtendFields"))$("#genExtendFields").hidden=!$("#genExtendEnabled")?.checked;
    if($("#genR2VFields"))$("#genR2VFields").hidden=state.mode!=="r2v";
  }

  function renderGenerationSettings(spec={}){
    const data=state.generationProfileData||{};
    selectOptions($("#genSampler"),data.samplers||[],spec.sampler||"euler");
    selectOptions($("#genScheduler"),data.schedulers||[],spec.scheduler||"simple");
    $("#genScheduleType").value=spec.schedule_type||"basic";
    $("#genSteps").value=spec.steps??8;
    $("#genStrength").value=spec.strength??1;
    $("#genShiftVideo").value=spec.shift_video??6;
    $("#genShiftAudio").value=spec.shift_audio??3;
    $("#genBetaAlpha").value=spec.beta_alpha??.6;
    $("#genBetaBeta").value=spec.beta_beta??.6;
    $("#genExtendEnabled").checked=spec.extend_enabled===true;
    $("#genExtendSteps").value=spec.extend_steps??2;
    $("#genExtendStart").value=spec.extend_start??.8;
    $("#genExtendEnd").value=spec.extend_end??0;
    selectOptions($("#genExtendSpacing"),["linear","cosine","sine"],spec.extend_spacing||"linear");
    selectOptions($("#genRefImageSize"),data.ref_image_sizes||["max","match"],spec.ref_image_size||"max");
    updateTuningVisibility();
  }

  function selectedProfileSpec(){
    const id=$("#generationProfile")?.value||state.generationProfileData?.default||FALLBACK_PROFILE;
    return state.generationProfileData?.profiles?.[id]||null;
  }

  function applySelectedProfileDefaults(){
    const spec=selectedProfileSpec();
    if(spec)renderGenerationSettings(spec);
  }

  async function loadGenerationProfiles(preferredProfile=null,preferredSettings=null){
    const token=++state.profileLoadToken;
    try{
      const data=await api("/api/generation-profiles?mode="+encodeURIComponent(state.mode));
      if(token!==state.profileLoadToken)return;
      state.generationProfileData=data;
      const sel=$("#generationProfile");
      const profiles=data.profiles||{};
      let wanted=legacyProfileAlias(preferredProfile||sel?.value||data.default);
      if(!profiles[wanted])wanted=data.default||Object.keys(profiles)[0]||FALLBACK_PROFILE;
      if(sel){
        sel.innerHTML=Object.entries(profiles).map(([id,spec])=>
          `<option value="${esc(id)}">${esc(spec.label||id)}${spec.ready===false?" · unavailable":""}</option>`
        ).join("");
        sel.value=wanted;
      }
      renderGenerationSettings(preferredSettings||profiles[wanted]||{});
      updateGenerationAvailability();
    }catch(e){
      const status=$("#profileStatus");
      if(status)status.textContent="Profile config unavailable";
    }
  }

  function profileReady(){
    const spec=selectedProfileSpec();
    return !!spec?.ready;
  }

  function updateGenerationAvailability(){
    const checkpoint=$("#baseCheckpoint"), checkpointStatus=$("#checkpointStatus");
    if(!checkpoint)return;
    const erosOk=erosReady();
    const erosOption=[...checkpoint.options].find(x=>x.value===EROS);
    if(erosOption)erosOption.textContent=erosOk?"Eros Max Beta5 INT8":"Eros Max Beta5 INT8 · provisioning";
    if(checkpointStatus){
      if(checkpoint.value===EROS) checkpointStatus.textContent=erosOk?"Eros ready":"Eros still downloading";
      else checkpointStatus.textContent="Stock H3 ConvRot";
    }

    const turboOk=profileReady();
    const spec=selectedProfileSpec();
    const profileStatus=$("#profileStatus");
    if(profileStatus)profileStatus.textContent=spec?(turboOk?"Ready":"Turbo unavailable"):"Checking…";

    const p=state.info?.provisioning||{};
    const button=$("#generate");
    if(button && p.core_ready){
      if(checkpoint.value===EROS && !erosOk){
        button.disabled=true;
        button.textContent="Eros checkpoint provisioning…";
      }else if(!turboOk){
        button.disabled=true;
        button.textContent="Selected profile unavailable";
      }else{
        button.disabled=false;
        button.textContent="Queue Generation";
      }
    }
  }

  function speedText(n){
    n=Number(n||0);
    return n>0?bytes(n)+"/s":"—";
  }

  function renderProvisioning(){
    const host=$("#modelProvisioning");
    if(!host)return;
    const p=state.info?.provisioning||{};
    const rows=Object.values(p.model_progress||{}).filter(x=>x&&x.show_in_ui!==false);
    $("#provisioningStage").textContent=(p.stage||p.status||"").replaceAll("_"," ");
    if(!rows.length){host.innerHTML='<div class="muted">Waiting for provisioning state…</div>';return;}

    let knownTotal=0, knownDone=0, allKnown=true;
    for(const row of rows){
      const total=Number(row.total_bytes||0), done=Number(row.downloaded_bytes||0);
      if(total>0){knownTotal+=total;knownDone+=Math.min(done,total);}else if(row.status!=="ready"){allKnown=false;}
    }
    const overall=knownTotal&&allKnown?Math.min(100,knownDone/knownTotal*100):null;
    const accelRows=Object.values(p.model_progress||{}).filter(x=>x?.phase==="accelerator");
    const accelReady=accelRows.filter(x=>x.status==="ready").length;
    $("#provisioningOverall").innerHTML=`
      <div class="progress-head"><span>${p.core_ready?"Stock core ready · generation unlocked":"Provisioning stock core"}</span><strong>${overall==null?"":pct(overall)}</strong></div>
      <div class="progress"><div style="width:${overall==null?0:overall}%"></div></div>
      <div class="muted provision-message">${esc(p.message||"")}</div>
      ${accelRows.length?`<div class="muted provision-message">Turbo profiles: ${accelReady}/${accelRows.length} files ready</div>`:""}`;

    host.innerHTML=rows.map(row=>{
      const total=Number(row.total_bytes||0), done=Number(row.downloaded_bytes||0);
      const ready=row.status==="ready";
      const progress=ready?100:(total>0?Math.min(100,done/total*100):0);
      const phase=row.phase==="addon"?"ADDON · LAST":row.phase==="accelerator"?"ACCELERATOR":"CORE";
      return `<div class="model-download ${ready?"ready":row.status==="error"?"error":""}">
        <div class="model-download-head"><div><span class="model-phase">${esc(phase)}</span><strong>${esc(row.label||row.destination||"Model")}</strong></div><strong>${ready?"READY":row.status==="error"?"ERROR":pct(progress)}</strong></div>
        <div class="progress model-progress"><div style="width:${progress}%"></div></div>
        <div class="model-download-meta"><span>${bytes(done)}${total?" / "+bytes(total):""}</span><span>${ready?"complete":speedText(row.speed_bps)}</span></div>
        ${row.error?`<div class="message error">${esc(row.error)}</div>`:""}
      </div>`;
    }).join("");
  }

  ensureUi();

  const originalCapture=captureDraft;
  captureDraft=function(){
    const v=originalCapture();
    v.base_checkpoint=$("#baseCheckpoint")?.value||STOCK;
    v.generation_profile=$("#generationProfile")?.value||state.generationProfileData?.default||FALLBACK_PROFILE;
    v.generation_settings=readGenerationSettings();
    v.ending_image=state.endingImage||null;
    return v;
  };

  const originalApply=applyDraft;
  applyDraft=function(v={}){
    originalApply(v);
    state.endingImage=v.ending_image||null;
    const sel=$("#baseCheckpoint");
    if(sel)sel.value=[STOCK,EROS].includes(v.base_checkpoint)?v.base_checkpoint:STOCK;
    loadGenerationProfiles(v.generation_profile||null,v.generation_settings||null);
    renderEndingImage();
    updateGenerationAvailability();
  };

  const originalLoadAssets=loadAssets;
  loadAssets=async function(render=true){
    const out=await originalLoadAssets(render);
    renderEndingImage();
    return out;
  };

  const originalRenderPicker=renderAssetPicker;
  renderAssetPicker=function(){
    originalRenderPicker();
    if(state.assetPickerMode!=="end")return;
    $("#assetPickerTitle").textContent="Choose ending image";
    $("#assetPickerHint").textContent="Optional final keyframe · images only";
    $$(".asset-pick-row",$("#assetPickerList")).forEach(row=>row.onclick=()=>{
      state.endingImage=row.dataset.file;
      renderEndingImage();
      stashCurrentDraft();
      closeAssetPicker();
    });
  };
  // app.js wired the search box before this overlay existed. Point it at the
  // upgraded renderer too so filtering an end-frame picker cannot fall back to
  // the R2V-reference click handler.
  $("#assetPickerSearch").oninput=renderAssetPicker;

  const originalRefreshInfo=refreshInfo;
  refreshInfo=async function(){
    await originalRefreshInfo();
    renderProvisioning();
    updateGenerationAvailability();
  };

  renderEndingImage();
  renderProvisioning();
  updateGenerationAvailability();
  setTimeout(()=>refreshInfo(),100);
  setTimeout(()=>loadGenerationProfiles(),120);
  setTimeout(()=>loadRuntimeControls(),150);
})();
