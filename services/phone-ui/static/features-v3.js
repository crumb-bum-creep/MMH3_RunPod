"use strict";

// MMH3 v3 feature overlay. Keep the d2103 app.js intact and layer the new
// controls onto its stable draft/template machinery.
(function(){
  const STOCK="stock_convrot_int8";
  const EROS="eros_beta5_int8";
  state.endingImage=state.endingImage||null;

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
      $("#baseCheckpoint").onchange=()=>{stashCurrentDraft();updateCheckpointAvailability();};
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
      const cards=$$(".card",system);
      const card=document.createElement("div");
      card.className="card";
      card.innerHTML=`
        <div class="labelrow"><div><strong>Core model provisioning</strong><div class="muted">Stock models unlock generation first; Eros downloads last.</div></div><span id="provisioningStage" class="muted"></span></div>
        <div id="provisioningOverall" class="provisioning-overall"></div>
        <div id="modelProvisioning" class="model-provisioning stack"></div>`;
      if(cards[0])cards[0].insertAdjacentElement("afterend",card); else system.prepend(card);
    }
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

  function updateCheckpointAvailability(){
    const sel=$("#baseCheckpoint"), status=$("#checkpointStatus");
    if(!sel)return;
    const ready=erosReady();
    const option=[...sel.options].find(x=>x.value===EROS);
    if(option)option.textContent=ready?"Eros Max Beta5 INT8":"Eros Max Beta5 INT8 · provisioning";
    if(status){
      if(sel.value===EROS) status.textContent=ready?"Eros ready":"Eros still downloading";
      else status.textContent="Stock H3 ConvRot";
    }
    const p=state.info?.provisioning||{};
    const button=$("#generate");
    if(button && p.core_ready && sel.value===EROS && !ready){
      button.disabled=true;
      button.textContent="Eros checkpoint provisioning…";
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
    $("#provisioningOverall").innerHTML=`
      <div class="progress-head"><span>${p.core_ready?"Stock core ready · generation unlocked":"Provisioning stock core"}</span><strong>${overall==null?"":pct(overall)}</strong></div>
      <div class="progress"><div style="width:${overall==null?0:overall}%"></div></div>
      <div class="muted provision-message">${esc(p.message||"")}</div>`;

    host.innerHTML=rows.map(row=>{
      const total=Number(row.total_bytes||0), done=Number(row.downloaded_bytes||0);
      const ready=row.status==="ready";
      const progress=ready?100:(total>0?Math.min(100,done/total*100):0);
      const phase=row.phase==="addon"?"ADDON · LAST":"CORE";
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
    v.ending_image=state.endingImage||null;
    return v;
  };

  const originalApply=applyDraft;
  applyDraft=function(v={}){
    originalApply(v);
    state.endingImage=v.ending_image||null;
    const sel=$("#baseCheckpoint");
    if(sel)sel.value=[STOCK,EROS].includes(v.base_checkpoint)?v.base_checkpoint:STOCK;
    renderEndingImage();
    updateCheckpointAvailability();
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

  const originalRefreshInfo=refreshInfo;
  refreshInfo=async function(){
    await originalRefreshInfo();
    renderProvisioning();
    updateCheckpointAvailability();
  };

  renderEndingImage();
  renderProvisioning();
  updateCheckpointAvailability();
  setTimeout(()=>refreshInfo(),100);
})();
