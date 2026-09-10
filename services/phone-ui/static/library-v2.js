"use strict";

(() => {
  const library = {
    items: [],
    meta: {version: 1, groups: [], videos: {}, updated_at: 0},
    query: "",
    group: "",
    tag: "",
    favoritesOnly: false,
    pageSize: 48,
    visibleCount: 48,
    detailFile: null,
    lastSignature: "",
  };

  const q = (s, root=document) => root.querySelector(s);
  const qa = (s, root=document) => [...root.querySelectorAll(s)];
  const html = (v="") => String(v).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
  const media = file => "/media/output/" + String(file||"").split("/").map(encodeURIComponent).join("/");
  const fmtBytes = n => {
    n = Number(n||0); if(!n) return "0 B";
    const units=["B","KB","MB","GB","TB"]; let i=0;
    while(n>=1024 && i<units.length-1){n/=1024;i++;}
    return n.toFixed(i<2?0:1)+" "+units[i];
  };
  const pctText = n => Math.round(Number(n||0))+"%";
  const videoMeta = file => library.meta.videos?.[file] || {group:"", tags:[], favorite:false};
  const uniqueSorted = values => [...new Set(values.filter(Boolean))].sort((a,b)=>String(a).localeCompare(String(b),undefined,{sensitivity:"base"}));

  function ensureUi(){
    const legacyHost=q("#outputs");
    const card=q("#tab-outputs .card");
    if(!legacyHost || !card || q("#outputLibraryV2")) return;
    legacyHost.hidden=true;
    legacyHost.setAttribute("aria-hidden","true");

    const toolbar=document.createElement("div");
    toolbar.className="output-toolbar-v2";
    toolbar.innerHTML=`
      <input id="outputSearchV2" type="search" placeholder="Search videos, prompts, tags…" aria-label="Search generated videos">
      <select id="outputGroupFilterV2" aria-label="Filter by group"><option value="">All groups</option></select>
      <select id="outputTagFilterV2" aria-label="Filter by tag"><option value="">All tags</option></select>
      <button id="outputFavoritesV2" class="ghost small favorite-filter-v2" type="button" aria-pressed="false">☆ Favorites</button>
      <button id="outputAddGroupV2" class="ghost small" type="button">+ Group</button>
      <span id="outputCountV2" class="muted output-count-v2"></span>`;

    const grid=document.createElement("div");
    grid.id="outputLibraryV2";
    grid.className="output-grid-v2";
    const more=document.createElement("button");
    more.id="outputMoreV2";
    more.className="secondary output-more-v2";
    more.type="button";
    more.hidden=true;
    more.textContent="Show more";

    legacyHost.before(toolbar);
    legacyHost.after(grid, more);

    q("#outputSearchV2").addEventListener("input", e=>{library.query=e.target.value.trim().toLowerCase();library.visibleCount=library.pageSize;render();});
    q("#outputGroupFilterV2").addEventListener("change", e=>{library.group=e.target.value;library.visibleCount=library.pageSize;render();});
    q("#outputTagFilterV2").addEventListener("change", e=>{library.tag=e.target.value;library.visibleCount=library.pageSize;render();});
    q("#outputFavoritesV2").addEventListener("click", ()=>{
      library.favoritesOnly=!library.favoritesOnly;
      q("#outputFavoritesV2").setAttribute("aria-pressed",String(library.favoritesOnly));
      library.visibleCount=library.pageSize;
      render();
    });
    q("#outputAddGroupV2").addEventListener("click", createGroup);
    q("#outputMoreV2").addEventListener("click", ()=>{library.visibleCount+=library.pageSize;render();});
    q("#refreshOutputs")?.addEventListener("click", ()=>load(true));

    const dialog=document.createElement("dialog");
    dialog.id="outputDetailV2";
    dialog.className="output-detail-v2";
    dialog.innerHTML='<div id="outputDetailBodyV2"></div>';
    document.body.appendChild(dialog);
    dialog.addEventListener("close", stopDetailVideo);
    dialog.addEventListener("click", e=>{if(e.target===dialog) dialog.close();});
  }

  function ensureProgressHud(){
    if(q("#globalProgressHud")) return;
    const hud=document.createElement("div");
    hud.id="globalProgressHud";
    hud.className="global-progress-hud";
    hud.hidden=true;
    hud.innerHTML=`
      <div class="global-progress-row"><span id="globalProcessName">Working…</span><strong id="globalProcessPct">0%</strong></div>
      <div class="global-progress-track"><div id="globalProcessBar"></div></div>
      <div class="global-progress-row overall"><span>Overall</span><strong id="globalTotalPct">0%</strong></div>
      <div class="global-progress-track total"><div id="globalTotalBar"></div></div>`;
    document.body.appendChild(hud);
  }

  function ensureMemoryButtons(){
    const full=q("#freeMemory");
    if(!full || q("#clearCacheOnly")) return;
    full.textContent="Unload models + cache";
    const cache=document.createElement("button");
    cache.id="clearCacheOnly";
    cache.className="secondary";
    cache.type="button";
    cache.textContent="Clear cache";
    full.before(cache);
    cache.addEventListener("click", async()=>{
      cache.disabled=true;
      try{
        await api("/api/system/free",{method:"POST",body:{mode:"cache"}});
        toast("Cache clear requested · loaded models kept");
      }catch(e){toast(e.message);}
      finally{cache.disabled=false;}
    });
  }

  async function refreshGlobalProgress(){
    ensureProgressHud();
    try{
      const d=await api("/api/progress");
      const status=String(d.status||"idle").toLowerCase();
      const active=status==="running" || status==="queued";
      const hud=q("#globalProgressHud");
      hud.hidden=!active;
      document.body.classList.toggle("generation-active-v2",active);
      if(!active) return;
      const process=Math.max(0,Math.min(100,Number(d.process_percent||0)));
      const total=Math.max(0,Math.min(100,Number(d.total_percent||0)));
      q("#globalProcessName").textContent=d.process_name||"Working…";
      q("#globalProcessPct").textContent=pctText(process);
      q("#globalTotalPct").textContent=pctText(total);
      q("#globalProcessBar").style.width=process+"%";
      q("#globalTotalBar").style.width=total+"%";
    }catch{}
  }

  function groups(){
    return uniqueSorted([...(library.meta.groups||[]), ...Object.values(library.meta.videos||{}).map(x=>x?.group)]);
  }

  function tags(){
    return uniqueSorted(Object.values(library.meta.videos||{}).flatMap(x=>Array.isArray(x?.tags)?x.tags:[]));
  }

  function refreshFilters(){
    const groupSelect=q("#outputGroupFilterV2");
    const tagSelect=q("#outputTagFilterV2");
    if(!groupSelect || !tagSelect) return;
    const gv=library.group, tv=library.tag;
    const groupValues=groups(), tagValues=tags();
    groupSelect.innerHTML='<option value="">All groups</option>'+groupValues.map(x=>`<option value="${html(x)}">${html(x)}</option>`).join("");
    tagSelect.innerHTML='<option value="">All tags</option>'+tagValues.map(x=>`<option value="${html(x)}">${html(x)}</option>`).join("");
    groupSelect.value=groupValues.includes(gv)?gv:"";
    tagSelect.value=tagValues.includes(tv)?tv:"";
    library.group=groupSelect.value;
    library.tag=tagSelect.value;
    const fav=q("#outputFavoritesV2");
    fav.classList.toggle("active",library.favoritesOnly);
    fav.textContent=library.favoritesOnly?"★ Favorites":"☆ Favorites";
  }

  function filteredItems(){
    return library.items.filter(item=>{
      const m=item.metadata||{}, org=videoMeta(item.file);
      if(library.group && org.group!==library.group) return false;
      if(library.tag && !(org.tags||[]).includes(library.tag)) return false;
      if(library.favoritesOnly && !org.favorite) return false;
      if(library.query){
        const hay=[item.file,m.mode,m.prompt_mode,m.prompt,m.prompt_idea,m.actual_prompt,org.group,...(org.tags||[])].join(" ").toLowerCase();
        if(!hay.includes(library.query)) return false;
      }
      return true;
    });
  }

  function cardHtml(item){
    const m=item.metadata||{}, org=videoMeta(item.file), name=item.file.split("/").pop();
    const mode=[m.mode,m.prompt_mode].filter(Boolean).map(x=>String(x).toUpperCase()).join(" · ");
    const badges=[org.group?`<span class="output-chip-v2 group">${html(org.group)}</span>`:"", ...(org.tags||[]).slice(0,3).map(t=>`<span class="output-chip-v2">${html(t)}</span>`)].join("");
    const preview=item.preview_file
      ? `<img src="${html(media(item.preview_file))}" loading="lazy" alt="" class="output-preview-v2">`
      : `<div class="output-preview-v2 output-placeholder-v2">VIDEO</div>`;
    return `<article class="output-card-v2" data-file="${html(item.file)}">
      <button class="output-open-v2" type="button" aria-label="Open ${html(name)}">${preview}<span class="output-play-v2">▶</span></button>
      <button class="output-star-v2 ${org.favorite?"active":""}" type="button" aria-label="Favorite">${org.favorite?"★":"☆"}</button>
      <div class="output-card-body-v2">
        <div class="output-title-v2" title="${html(name)}">${html(name)}</div>
        <div class="muted output-sub-v2">${html(mode||"VIDEO")} · ${fmtBytes(item.size)}</div>
        ${badges?`<div class="output-chips-v2">${badges}</div>`:""}
      </div>
    </article>`;
  }

  function render(){
    ensureUi();
    refreshFilters();
    const host=q("#outputLibraryV2");
    if(!host) return;
    const items=filteredItems();
    const visible=items.slice(0,library.visibleCount);
    q("#outputCountV2").textContent=`${items.length} / ${library.items.length}`;
    if(!visible.length){
      host.innerHTML='<div class="output-empty-v2 muted">No videos match these filters.</div>';
    }else{
      host.innerHTML=visible.map(cardHtml).join("");
      qa(".output-card-v2",host).forEach(card=>{
        const file=card.dataset.file;
        q(".output-open-v2",card).addEventListener("click",()=>openDetail(file));
        q(".output-star-v2",card).addEventListener("click",async e=>{e.stopPropagation();await toggleFavorite(file);});
      });
    }
    const more=q("#outputMoreV2");
    more.hidden=items.length<=visible.length;
    if(!more.hidden) more.textContent=`Show more · ${items.length-visible.length} remaining`;
  }

  async function persistMeta(){
    library.meta.updated_at=Date.now()/1000;
    try{
      const d=await api("/api/output-library",{method:"PUT",body:library.meta});
      library.meta={version:1,groups:d.groups||[],videos:d.videos||{},updated_at:d.updated_at||library.meta.updated_at};
      render();
    }catch(e){toast(e.message);}
  }

  async function toggleFavorite(file){
    const current={...videoMeta(file)};
    current.favorite=!current.favorite;
    current.tags=Array.isArray(current.tags)?current.tags:[];
    current.group=current.group||"";
    library.meta.videos[file]=current;
    await persistMeta();
    if(library.detailFile===file && q("#outputDetailV2")?.open) openDetail(file,true);
  }

  async function createGroup(){
    const name=prompt("New video group / folder name");
    if(name===null) return;
    const clean=name.trim().slice(0,80);
    if(!clean) return;
    library.meta.groups=uniqueSorted([...(library.meta.groups||[]),clean]);
    await persistMeta();
    library.group=clean;
    library.visibleCount=library.pageSize;
    render();
  }

  function detailGroupOptions(selected){
    return '<option value="">No group</option>'+groups().map(x=>`<option value="${html(x)}" ${x===selected?"selected":""}>${html(x)}</option>`).join("")+'<option value="__new__">+ New group…</option>';
  }

  function stopDetailVideo(){
    const video=q("#outputDetailV2 video");
    if(video){video.pause();video.removeAttribute("src");video.load();}
    library.detailFile=null;
  }

  function openDetail(file,rerender=false){
    const item=library.items.find(x=>x.file===file);
    if(!item) return;
    const dialog=q("#outputDetailV2"), body=q("#outputDetailBodyV2"), m=item.metadata||{}, org=videoMeta(file);
    library.detailFile=file;
    body.innerHTML=`
      <div class="output-detail-head-v2"><div><strong>${html(file.split("/").pop())}</strong><div class="muted">${html(String(m.mode||"").toUpperCase())} ${html(String(m.prompt_mode||"").toUpperCase())} · ${fmtBytes(item.size)}</div></div><button id="outputDetailCloseV2" class="ghost small" type="button">Close</button></div>
      <video controls autoplay preload="metadata" src="${html(media(file))}"></video>
      <div class="output-organize-v2">
        <label>Group<select id="outputDetailGroupV2">${detailGroupOptions(org.group||"")}</select></label>
        <label>Tags<input id="outputDetailTagsV2" value="${html((org.tags||[]).join(", "))}" placeholder="character, keeper, test…"></label>
        <button id="outputDetailFavoriteV2" class="secondary ${org.favorite?"active":""}" type="button">${org.favorite?"★ Favorited":"☆ Favorite"}</button>
      </div>
      <div class="actions output-actions-v2">
        <button class="secondary" id="outputCopyPromptV2">Copy prompt</button>
        <button class="secondary" id="outputCopySeedV2">Copy seed</button>
        <button class="secondary" id="outputCopyMetaV2">Copy metadata</button>
        <button class="secondary" id="outputUseR2VV2">Use as R2V ref</button>
        <button class="secondary" id="outputReuseV2">Reuse setup</button>
        <button class="danger" id="outputDeleteV2">Delete</button>
      </div>
      <div class="meta output-prompt-v2">${html((m.actual_prompt||m.prompt_idea||m.prompt||"").slice(0,1600))}</div>`;

    q("#outputDetailCloseV2").onclick=()=>dialog.close();
    q("#outputDetailFavoriteV2").onclick=()=>toggleFavorite(file);
    q("#outputDetailGroupV2").onchange=async e=>{
      let next=e.target.value;
      if(next==="__new__"){
        const raw=prompt("New video group / folder name");
        next=raw===null?(org.group||""):raw.trim().slice(0,80);
        if(next) library.meta.groups=uniqueSorted([...(library.meta.groups||[]),next]);
      }
      const current={...videoMeta(file),group:next||"",tags:[...(videoMeta(file).tags||[])]};
      library.meta.videos[file]=current;
      await persistMeta();
      openDetail(file,true);
    };
    const tagsInput=q("#outputDetailTagsV2");
    const saveTags=async()=>{
      const nextTags=uniqueSorted(tagsInput.value.split(",").map(x=>x.trim()).filter(Boolean).map(x=>x.slice(0,50))).slice(0,30);
      const current={...videoMeta(file),group:videoMeta(file).group||"",tags:nextTags};
      library.meta.videos[file]=current;
      await persistMeta();
    };
    tagsInput.onchange=saveTags;
    tagsInput.onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();tagsInput.blur();}};

    q("#outputCopyPromptV2").onclick=()=>copyText(m.actual_prompt||m.prompt||m.prompt_idea||"");
    q("#outputCopySeedV2").onclick=()=>copyText(String(m.seed??""));
    q("#outputCopyMetaV2").onclick=()=>copyText(JSON.stringify(m,null,2));
    q("#outputReuseV2").onclick=()=>{dialog.close();applySnapshot(m);switchTab("generate");toast("Setup loaded");};
    q("#outputUseR2VV2").onclick=async()=>{
      try{
        const d=await api("/api/output-to-input",{method:"POST",body:{file}});
        dialog.close();setMode("r2v");addRef("video",d.file,true);switchTab("generate");refreshInputOptions();toast("Added as R2V video reference");
      }catch(e){toast(e.message);}
    };
    q("#outputDeleteV2").onclick=async()=>{
      if(!confirm("Delete this video?")) return;
      try{
        await api("/api/outputs/"+file.split("/").map(encodeURIComponent).join("/"),{method:"DELETE"});
        dialog.close();
        library.items=library.items.filter(x=>x.file!==file);
        delete library.meta.videos[file];
        await persistMeta();
        await load(true);
      }catch(e){toast(e.message);}
    };

    if(!dialog.open) dialog.showModal ? dialog.showModal() : dialog.setAttribute("open","");
    else if(rerender) q("#outputDetailV2 video")?.play().catch(()=>{});
  }

  async function load(force=false){
    ensureUi();
    try{
      const [out,meta]=await Promise.all([api("/api/outputs"),api("/api/output-library")]);
      const next=out.items||[];
      const signature=JSON.stringify([out.index_updated_at||0,next.length,meta.updated_at||0]);
      library.items=next;
      library.meta={version:1,groups:meta.groups||[],videos:meta.videos||{},updated_at:meta.updated_at||0};
      if(force || signature!==library.lastSignature){
        library.lastSignature=signature;
        render();
      }
    }catch(e){
      const host=q("#outputLibraryV2");
      if(host) host.innerHTML='<div class="message error">'+html(e.message)+'</div>';
    }
  }

  function boot(){
    ensureUi();
    ensureProgressHud();
    ensureMemoryButtons();
    load(true);
    refreshGlobalProgress();

    qa("#tabs button").forEach(button=>button.addEventListener("click",()=>{
      if(button.dataset.tab==="outputs") load(true);
    }));
    setInterval(()=>{if(typeof state!=="undefined" && state.tab==="outputs") load(false);},10000);
    setInterval(refreshGlobalProgress,900);
  }

  if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",boot,{once:true});
  else setTimeout(boot,0);
})();
