'use strict';
(function () {
  const editor = document.querySelector('[data-theme-editor]');
  if (!editor) return;
  const q = s => editor.querySelector(s);
  const radio = q('[data-editor-custom]'), border = q('[data-editor-border]'), rail = q('[data-editor-rail]');
  const grid = q('[data-editor-pixels]'), focus = q('[data-editor-focus]'), code = q('[data-editor-code]');
  const library = q('[data-editor-library]'), name = q('[data-editor-name]'), message = q('[data-editor-message]');
  const key = 'flux-theme-library-v1';
  const locationNote = q('[data-theme-library-location]');
  function invalidReason(token) {
    if (typeof token!=='string'||token.length>13400) return 'Theme code is too large or missing.';
    const m=token.match(/^custom-([01]{2})-([0-9a-f]{16})(?:-([01][0-3][0-3][0-3]|[01][0-3]3[0-3]-m[0-9a-f]{28}))?(?:-b([0-9a-f]{240}))?(?:-g[0-9a-f]{64})?(?:-(l[^-]+))?(?:-f([0-9a-f]{264}))?(?:-w([0-9a-f]{30}|[0-9a-f]{100}|[0-9a-f]{150}|[0-9a-f]{290}|[0-9a-f]{320}|[0-9a-f]{340}))?$/);
    if(!m)return 'Theme code format is invalid.';
    if(m[2].match(/../g).some(x=>parseInt(x,16)&1))return 'The active VFO marker must stay seven pixels wide.';
    try {
      const logo=token.match(/-g([0-9a-f]{64})(?:-|$)/);
      if(!!(m[3]&&m[3][3]==='3')!==!!logo)return 'A custom startup icon needs its 16 × 16 sprite.';
      if(logo&&!/[1-9a-f]/.test(logo[1]))return 'Draw at least one pixel in the startup icon.';
      const ps=m[5]?window.FluxLayers.parse(m[5]):null;
      if(m[4]){
        const b=m[4].match(/../g).map(x=>parseInt(x,16));
        for(let i=0;i<120;i+=24){const g=b.slice(i,i+24);if(!g.some(Boolean))return 'Each custom battery charge state needs visible pixels.';if(g.some((x,j)=>j%3===2&&(x&127)))return 'Custom battery artwork must stay 17 pixels wide.';}
      }
      if(ps&&ps.slice(1).some(p=>p.some(Boolean)&&!p.includes(1)))return 'Each drawn cursor layer needs at least one ink pixel.';
      if(Number(m[1])===0&&parseInt(m[2],16)===0&&(!ps||!ps.slice(1).every(p=>p.includes(1))))return 'Add a visible active VFO indicator.';
      if(m[3]&&m[3].includes('-m')){
        const bytes=m[3].split('-m')[1].match(/../g).map(x=>parseInt(x,16));
        if(bytes.some(x=>x&7))return 'Custom meter glyphs must stay five pixels wide.';
        if(!bytes.slice(0,7).some(Boolean)||!bytes.slice(7).some(Boolean))return 'Both custom meter glyphs need visible pixels.';
      }
      if(m[6]&&Array.from({length:11},(_,i)=>m[6].slice(i*24,i*24+24)).some(x=>!/[1-9a-f]/.test(x)))return 'Each custom frequency glyph needs visible pixels.';
      return '';
    }catch(err){return err.message||'Invalid theme artwork.';}
  }
  function valid(token){return !invalidReason(token);}
  let rows=Array(8).fill(0),active=0,saved=[],mode="layout",past=[],future=[],baseline=null,restoring=false;
  const widgets=window.FluxWidgets.create(editor,()=>update(true),()=>update(false));
  const layout=window.FluxLayout.create(editor,()=>update(true),{
    editing:()=>mode==='layout',
    preview:()=>{setActive(layout.activeSide());update(false);},
    drawWidget(group,id,value,rx){
      if(id.endsWith('-marker')){
        if(!value)return;
        for(let y=0;y<8;y++)for(let x=0;x<7;x++)if(rows[y]&(128>>x)){
          const r=document.createElementNS('http://www.w3.org/2000/svg','rect');for(const [k,v] of Object.entries({x,y,width:1,height:1,fill:'#172211','pointer-events':'none'}))r.setAttribute(k,v);group.append(r);
        }
        if(!rows.some(Boolean)){
          const f=window.FluxLCDFonts.small;let d='';for(const [y,row] of (f.glyphs['>']||[]).entries())for(let x=0;x<f.w;x++)if(row&(1<<x))d+='M'+x+' '+y+'h1v1h-1z';
          const p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('d',d);p.setAttribute('fill','#172211');group.append(p);
        }
      }else widgets.drawWidget(group,id,rx);
    }
  });
  const layers = window.FluxLayers.create(editor,q('[data-editor-screen]'),{active:setActive,update,legacy:()=>({border:border.checked,rail:rail.checked,rows})});
  function setActive(x){active=x;layout.setActiveSide(x);editor.querySelectorAll('[data-editor-vfo]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.editorVfo)===active)));}
  try { saved = JSON.parse(localStorage.getItem(key) || '[]').filter(x => x && typeof x.name === 'string' && valid(x.token)).map(x => ({...x,source:'browser'})); } catch (_) {}
  const buttons = [];
  for (let y=0; y<8; y++) for (let x=0; x<7; x++) {
    const b = document.createElement('button');
    b.type = 'button'; b.setAttribute('aria-label', 'Pixel '+(x+1)+', '+(y+1));
    b.addEventListener('click', () => { rows[y] ^= 128 >> x; update(true); });
    grid.append(b); buttons.push(b);
  }
  function rect(x,y,w,h) {
    const el = document.createElementNS('http://www.w3.org/2000/svg','rect');
    for (const [k,v] of Object.entries({x,y,width:w,height:h})) el.setAttribute(k,v);
    focus.append(el);
  }
  function capture(){return {layout:layout.snapshot(),layers:layers.snapshot(),rows:rows.slice(),border:border.checked,rail:rail.checked,widgets:widgets.suffix()};}
  function historyButtons(){q('[data-studio-undo]').disabled=!past.length;q('[data-studio-redo]').disabled=!future.length;}
  function checkpoint(){
    const next=capture();
    if(!restoring&&baseline&&JSON.stringify(next)!==JSON.stringify(baseline)){past.push(baseline);if(past.length>40)past.shift();future=[];}
    baseline=next;historyButtons();
  }
  function replay(from,to){
    if(!from.length)return;
    layers.cancel();
    to.push(baseline);const v=from.pop();restoring=true;
    rows=v.rows.slice();border.checked=v.border;rail.checked=v.rail;
    widgets.load(v.widgets.split('-').filter(Boolean));layers.restore(v.layers);layout.restore(v.layout);
    update(true);restoring=false;
  }
  q('[data-studio-undo]').onclick=()=>replay(past,future);
  q('[data-studio-redo]').onclick=()=>replay(future,past);
  function setMode(next){
    layers.cancel();mode=next;q('[data-layer-draw]').checked=mode==='graphics';
    if(mode==='graphics'&&Number(q('[data-layer-which]').value))setActive(Number(q('[data-layer-which]').value)-1);
    editor.querySelectorAll('[data-studio-mode]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.studioMode===mode)));
    editor.querySelectorAll('[data-studio-panel]').forEach(el=>el.hidden=el.dataset.studioPanel!==mode);
    q('[data-layout-controls]').open=mode==='preview';
    q('[data-studio-help]').textContent=({layout:'Drag widgets. Pink guides indicate aligned edges or centers. Shift-drag moves their parent; Alt bypasses snapping.',graphics:'Draw on the selected graphics layer. Isolate hides live widgets; Composite shows the actual layout.',preview:'Change simulated radio state without altering the design.'})[mode];
    update(false);
  }
  editor.querySelectorAll('[data-studio-mode]').forEach(el=>el.onclick=()=>setMode(el.dataset.studioMode));
  function update(select) {
    focus.replaceChildren();
    buttons.forEach((b,i)=>b.setAttribute('aria-pressed',String(!!(rows[Math.floor(i/7)]&(128>>(i%7))))));
    widgets.render();layout.render();layers.render(active);
    if(!select)return; // preview/draft state must never rewrite the exported token
    let token,error='';
    try{
      token='custom-'+Number(border.checked)+Number(rail.checked)+'-'+rows.map(x=>x.toString(16).padStart(2,'0')).join('')+widgets.suffix()+layers.suffix()+layout.suffix();
      const reason=invalidReason(token);if(reason)throw Error(reason);
    }catch(e){error=e.message;token='invalid-designer-draft';}
    radio.value=token;
    if(!error)code.value=token;
    q('[data-studio-status]').textContent=error||'Design updated · ready to export';
    message.textContent=error||'Design updated. Save to use it in the firmware builder.';
    q('[data-editor-export]').disabled=!!error;q('[data-editor-save]').disabled=!!error;q('[data-layout-package]').disabled=!!error;
    checkpoint();
  }
  function load(token, select) {
    if (!valid(token)) return;
    border.checked=token[7]==='1'; rail.checked=token[8]==='1';
    const parts=token.split('-'); rows=parts[2].match(/../g).map(x=>parseInt(x,16));
    const w=parts.at(-1).startsWith('w')?parts.pop().slice(1):''; const f=parts.at(-1).startsWith('f')?parts.pop().slice(1):'';const opts=/^[01][0-3][0-3][0-3]$/.test(parts[3]||'')?parts[3]:'0000';layout.load(w,f,Number(opts[0]),Number(opts[1]));
    const l=parts.at(-1).startsWith('l')?parts.pop():'';
    layers.load(l);widgets.load(parts.slice(3));
    if(!w||[30,100].includes(w.length))layers.migrateLegacy(border.checked,rail.checked,[layout.groupY('a'),layout.groupY('b')]);
    if(select)update(true);else{update(false);radio.value=token;code.value=token;checkpoint();}

  }
  function refresh() {
    library.replaceChildren(new Option('Choose saved theme',''));
    saved.forEach((item,i)=>library.add(new Option(item.name+(item.source==='browser'?' (browser only)':''),String(i))));
  }
  function persistBrowser() {
    try { localStorage.setItem(key,JSON.stringify(saved.filter(x=>x.source==='browser').map(({name,token})=>({name,token})))); return true; }
    catch(_) { return false; }
  }
  async function libraryRequest(method, data){
    const r=await fetch('/themes/library',{method,body:data});
    const result=await r.json();
    if(!r.ok)throw Error(result.error||'Theme library unavailable.');
    if(result.path)locationNote.textContent='Disk library: '+result.path;
    return result;
  }
  async function capturePreview(){
    const drawing=q('[data-layer-draw]');
    const visibility=[...editor.querySelectorAll('[data-layer-visible]')];
    const wasDrawing=drawing.checked,wasVisible=visibility.map(el=>el.checked);
    let svg;
    try{
      drawing.checked=false;
      visibility.forEach(el=>{el.checked=true;});
      update(false);
      svg=q('[data-editor-screen]').cloneNode(true);
    }finally{
      drawing.checked=wasDrawing;
      visibility.forEach((el,i)=>{el.checked=wasVisible[i];});
      update(false);
    }
    svg.querySelectorAll('[data-layout-guides],[data-editor-guide],[data-editor-ghost],[data-editor-draft]').forEach(el=>el.remove());
    svg.setAttribute('xmlns','http://www.w3.org/2000/svg');
    svg.setAttribute('width','256');svg.setAttribute('height','128');
    svg.removeAttribute('style');
    const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)],{type:'image/svg+xml'}));
    try{
      const image=new Image();image.src=url;await image.decode();
      const canvas=document.createElement('canvas');canvas.width=256;canvas.height=128;
      const context=canvas.getContext('2d');context.imageSmoothingEnabled=false;
      context.drawImage(image,0,0,256,128);
      return canvas.toDataURL('image/png');
    }finally{URL.revokeObjectURL(url);}
  }
  async function loadDiskLibrary(){
    try{
      const result=await libraryRequest('GET');
      const disk=result.themes.filter(x=>x&&typeof x.name==='string'&&valid(x.token)).map(x=>({...x,source:'disk'}));
      saved=[...disk,...saved.filter(x=>!disk.some(d=>d.name===x.name))];
      refresh();
    }catch(err){message.textContent=location.protocol==='file:'?'Open the live catalog at http://localhost:5055/themes to save themes to disk.':'Disk theme library unavailable: '+err.message;}
  }
  function actionToken() {
    const pasted=code.value.trim();
    if(pasted&&pasted!==radio.value){
      if(!valid(pasted)){message.textContent='The theme code is invalid. Load or correct it before saving or exporting.';return null;}
      load(pasted,true);
    }
    if(!valid(radio.value)){message.textContent='Select or load a valid theme before saving or exporting.';return null;}
    return radio.value;
  }
  border.addEventListener('change',()=>update(true)); rail.addEventListener('change',()=>update(true));
  editor.querySelectorAll('[data-editor-vfo]').forEach(b=>b.addEventListener('click',()=>{
    setActive(Number(b.dataset.editorVfo));
    editor.querySelectorAll('[data-editor-vfo]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));
    update(false);
  }));
  editor.querySelectorAll('[data-editor-preset]').forEach(b=>b.addEventListener('click',()=>{
    rows=({arrow:[192,240,252,254,252,240,192,0],diamond:[16,56,124,254,124,56,16,0],clear:Array(8).fill(0)})[b.dataset.editorPreset].slice();
    update(true);
  }));
  q('[data-editor-save]').addEventListener('click',async ()=>{
    const n=name.value.trim();
    const token=actionToken();
    if(!n) { message.textContent='Enter a theme name.'; return; }
    if(!token)return;
    try{
      const data=new FormData();data.append('name',n);data.append('theme',token);
      data.append('preview',await capturePreview());
      const result=await libraryRequest('POST',data);
      saved=saved.filter(x=>x.name!==n);
      saved.push({...result.theme,source:'disk'});
      saved.sort((a,b)=>a.name.localeCompare(b.name));
      persistBrowser();refresh();
      library.value=String(saved.findIndex(x=>x.name===n));
      load(result.theme.token,false);
      message.textContent='Saved '+n+' to disk. Select it in the firmware builder.';
    }catch(err){message.textContent='Save failed: '+err.message;}
  });
  library.addEventListener('change',()=>{
    if(library.value==='') return;
    const item=saved[Number(library.value)]; if(!item) return;
    name.value=item.name; load(item.token,true);
  });
  q('[data-editor-delete]').addEventListener('click',async ()=>{
    if(library.value==='') return;
    const item=saved[Number(library.value)];if(!item)return;
    try{
      if(item.source==='disk'){
        const data=new FormData();data.append('name',item.name);
        await libraryRequest('DELETE',data);
      }
      saved=saved.filter(x=>x!==item);persistBrowser();refresh();
      name.value='';message.textContent='Saved theme deleted; current design retained.';
    }catch(err){message.textContent='Delete failed: '+err.message;}
  });
  q('[data-editor-import]').addEventListener('click',()=>{
    const token=code.value.trim();
    if(!valid(token)) { message.textContent='Invalid theme code. Check the marker, layout and layers.'; return; }
    load(token,true); name.value=''; library.value='';
  });
  q('[data-editor-file]').addEventListener('change',async e=>{
    const file=e.target.files[0]; if(!file)return;
    if(file.size>8192){message.textContent='Theme exceeds 8 KiB.';return;}
    try {
      const data=new FormData();data.append('theme',file);
      const r=await fetch('/themes/import',{method:'POST',body:data}),j=await r.json();
      if(!r.ok||!valid(j.theme))throw Error(j.error||'Invalid theme');
      load(j.theme,true);name.value=j.name||file.name.replace(/\.[^.]+$/,'');message.textContent='Imported '+file.name;
    }catch(err){message.textContent=err.message;}finally{e.target.value='';}
  });
  q('[data-editor-export]').addEventListener('click',async ()=>{
    try {
      const token=actionToken();if(!token)return;
      const data=new FormData();data.append('theme',token);data.append('name',name.value.trim());
      const r=await fetch('/themes/export',{method:'POST',body:data});
      if(!r.ok)throw Error((await r.json()).error||'Export failed');
      const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');
      a.href=url;a.download=(name.value.trim()||'flux-theme')+'.fluxtheme';a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
      message.textContent='Theme package exported. It can be installed into an empty radio library slot with the theme installer.';
    }catch(err){message.textContent=err.message;}
  });
  const base = q('[data-editor-base]');
  function loadBase(token, select) {
    if (!valid(token)) return;
    load(token, select);
    name.value = ''; library.value = '';
    base.value = token;
  }
  q('[data-editor-load-base]').addEventListener('click', () => loadBase(base.value, true));
  // The zoom is a display-only integer pixel scale, never part of a theme.
  const viewport=q('[data-studio-viewport]'),zoom=q('[data-studio-zoom]'),readout=q('[data-studio-zoom-readout]'),expand=q('[data-studio-expand]');
  let scale=4;
  function fitScale(){
    const css=getComputedStyle(viewport);
    const width=viewport.clientWidth-parseFloat(css.paddingLeft)-parseFloat(css.paddingRight);
    return Math.max(1,Math.min(16,Math.floor(width/128)));
  }
  function resizeZoom(at){
    const svg=q('[data-editor-screen]'),old=svg.getBoundingClientRect(),area=viewport.getBoundingClientRect();
    const point=at||{x:area.left+viewport.clientWidth/2,y:area.top+viewport.clientHeight/2};
    const fx=old.width?Math.max(0,Math.min(1,(point.x-old.left)/old.width)):.5;
    const fy=old.height?Math.max(0,Math.min(1,(point.y-old.top)/old.height)):.5;
    scale=zoom.value==='fit'?fitScale():Number(zoom.value);
    svg.style.setProperty('--lcd-width',128*scale+'px');
    svg.style.setProperty('--lcd-height',64*scale+'px');
    const next=svg.getBoundingClientRect();
    viewport.scrollLeft+=next.left+fx*next.width-point.x;
    viewport.scrollTop+=next.top+fy*next.height-point.y;
    readout.textContent=scale+'× · '+128*scale+' × '+64*scale+' px';
    q('[data-studio-zoom-out]').disabled=scale<=1;
    q('[data-studio-zoom-in]').disabled=scale>=16;
  }
  function stepZoom(delta,at){zoom.value=String(Math.max(1,Math.min(16,scale+delta)));resizeZoom(at);}
  zoom.onchange=()=>resizeZoom();
  q('[data-studio-zoom-out]').onclick=()=>stepZoom(-1);
  q('[data-studio-zoom-in]').onclick=()=>stepZoom(1);
  q('[data-studio-zoom-fit]').onclick=()=>{zoom.value='fit';resizeZoom();viewport.scrollTo(0,0);};
  viewport.addEventListener('wheel',ev=>{if(!ev.ctrlKey&&!ev.metaKey)return;ev.preventDefault();stepZoom(ev.deltaY<0?1:-1,{x:ev.clientX,y:ev.clientY});},{passive:false});
  function expanded(on){
    editor.classList.toggle('studio-expanded',on);
    expand.setAttribute('aria-pressed',String(on));expand.textContent=on?'Exit expanded':'Expand';
    resizeZoom();
  }
  expand.onclick=()=>expanded(!editor.classList.contains('studio-expanded'));
  document.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&editor.classList.contains('studio-expanded')&&mode!=='graphics')expanded(false);});
  new ResizeObserver(()=>{if(zoom.value==='fit'&&viewport.clientWidth>0&&fitScale()!==scale)resizeZoom();}).observe(viewport);
  resizeZoom();
  refresh(); if(valid(radio.value)) load(radio.value,false); else update(true);
  loadDiskLibrary();
  base.value = radio.value;past=[];future=[];historyButtons();setMode('layout');
})();
