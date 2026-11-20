/* Nested LCD layout authoring prototype. Integer coordinates, relative to parent. */
(function(root){
'use strict';
const NS='http://www.w3.org/2000/svg';
const defaults=[
 ['status',null,0,0,128,8],['callsign','status',16,0,30,8],['dual-watch','status',57,0,10,8],
 ['battery','status',109,0,17,8],['vfo-a',null,0,8,128,24],['vfo-b',null,0,40,128,24],
 ['signal',null,0,32,128,8],['rssi','signal',2,0,60,8],['meter','signal',62,0,65,8]
];
for(const side of ['a','b']){
 const p='vfo-'+side;
 defaults.push([p+'-marker',p,2,0,7,8],[p+'-state',p,12,0,10,8],[p+'-channel',p,2,8,30,8],
 [p+'-frequency',p,44,0,82,17],[p+'-mode',p,2,18,12,6]);
}
const modeNames=['power','tone','offset','bandwidth','reverse','break-in','repeater'];
const modeWidths=[16,24,4,16,4,20,32],modeOffsets=[13,30,55,60,77,83,83];
for(const side of ['a','b'])for(let i=0;i<7;i++)defaults.push(['vfo-'+side+'-'+modeNames[i],'vfo-'+side,2+modeOffsets[i],18,modeWidths[i],6]);
defaults.push(['power-save','status',0,0,10,8],['vox','status',84,0,10,8],['lock','status',99,0,10,8]);
for(const side of ['a','b'])defaults.push(['vfo-'+side+'-channel-name','vfo-'+side,44,10,60,8]);
const samples={'power-save':'PS',vox:'VX',lock:'LK',callsign:'AG7SU','dual-watch':'DW',battery:'▰',rssi:'-73dBm S9',meter:'▂▃▄▅▆▇▇▇▇',marker:'▸',state:'RX',channel:'VFO',mode:'FM',power:'LOW',tone:'TSQL',offset:'+',bandwidth:'NAR',reverse:'R','break-in':'BK-SE',repeater:'REV +600'};
function sample(n,state){
 if(state && Object.hasOwn(state,n.id))return state[n.id];return n.id.endsWith('frequency')?(n.id.includes('-a-')?'146.52000':'440.85000'):(samples[n.id.replace(/^vfo-[ab]-/,'')]||'');}
function visualExtent(n,state){
 const key=n.id.replace(/^vfo-[ab]-/,'');
 if(n.id.endsWith('frequency')&&state?.[n.id+'-display']){
   const mode=state[n.id+'-display'],name=state[n.id+'-name'];
   return {w:(mode==='namefreq'?Math.max(name.length,sample(n,state).length):name.length)*5,h:mode==='namefreq'?16:8};
 }
 if(n.id.endsWith('frequency'))return {w:n.font==='custom'?72:n.font==='compact'?54:82,h:n.font==='custom'?12:n.font==='compact'?10:17};
 if(['battery','meter','marker'].includes(key))return {w:n.w,h:n.h};
 return {w:Math.max(1,sample(n,state).length)*(n.h===6?4:5),h:n.h};
}
const make=()=>defaults.map(([id,parent,x,y,w,h])=>({id,parent,x,y,w,h,font:'factory'}));
function absolute(nodes,id){
 let x=0,y=0,n=nodes.find(n=>n.id===id),guard=0;
 while(n){x+=n.x;y+=n.y;n=n.parent?nodes.find(p=>p.id===n.parent):null;if(++guard>nodes.length)throw Error('Cyclic layout');}
 return {x,y};
}
function snap(value,step){return Math.round(value/step)*step;}
function extent(nodes,id,state){
 const n=nodes.find(v=>v.id===id), kids=nodes.filter(v=>v.parent===id);
 if(!kids.length){
  const {w,h}=visualExtent(n,state);
  return {x:0,y:0,w,h};
 }
 const boxes=kids.map(k=>{const b=extent(nodes,k.id,state);return {x:k.x+b.x,y:k.y+b.y,w:b.w,h:b.h};});
 const x=Math.min(...boxes.map(b=>b.x)),y=Math.min(...boxes.map(b=>b.y));
 return {x,y,w:Math.max(...boxes.map(b=>b.x+b.w))-x,h:Math.max(...boxes.map(b=>b.y+b.h))-y};
}
function runtimeExtent(nodes,id){
 const n=nodes.find(v=>v.id===id),kids=nodes.filter(v=>v.parent===id);
 if(!kids.length)return {x:0,y:0,w:n.id.endsWith('frequency')?(n.font==='custom'?72:n.font==='compact'?54:n.w):n.w,h:n.id.endsWith('frequency')?(n.font==='custom'?12:n.font==='compact'?10:n.h):n.h};
 const boxes=kids.map(k=>{const b=runtimeExtent(nodes,k.id);return {x:k.x+b.x,y:k.y+b.y,w:b.w,h:b.h};});
 const x=Math.min(...boxes.map(b=>b.x)),y=Math.min(...boxes.map(b=>b.y));
 return {x,y,w:Math.max(...boxes.map(b=>b.x+b.w))-x,h:Math.max(...boxes.map(b=>b.y+b.h))-y};
}
// Work in absolute LCD coordinates, excluding anything that moves with the
// selected widget and its ancestors. Edge/center snapping precedes grid snap.
function move(nodes,id,x,y,step=1,align=false,state,tolerance=2){
 const n=nodes.find(n=>n.id===id);if(!n)throw Error('Unknown widget');
 const p=n.parent?absolute(nodes,n.parent):{x:0,y:0},b=extent(nodes,id,state),runtime=runtimeExtent(nodes,id);
 const byId=new Map(nodes.map(v=>[v.id,v]));
 function within(candidate,ancestor){
  for(let v=byId.get(candidate);v;v=byId.get(v.parent))if(v.id===ancestor)return true;
  return false;
 }
 const peers=align?nodes.filter(v=>!within(v.id,id)&&!within(id,v.id)).map(v=>{
  const pos=absolute(nodes,v.id),r=extent(nodes,v.id,state);
  return {x:pos.x+r.x,y:pos.y+r.y,w:r.w,h:r.h};
 }):[];
 const matches={};
 function axisMove(raw,axis,span,screen){
  const origin=p[axis]+b[axis],lo=-p[axis]-runtime[axis],hi=screen-p[axis]-runtime[axis]-(axis==='x'?runtime.w:runtime.h);
  const clamp=v=>Math.max(lo,Math.min(hi,v));
  let best=null;
  function consider(target,offset,priority){
   const value=target-origin-offset;
   // Never invent a half-pixel coordinate or display a false alignment.
   if(!Number.isInteger(value)||value<lo||value>hi)return;
   const distance=Math.abs(value-raw);
   if(distance>tolerance)return;
   if(!best||distance<best.distance-1e-9||
      (Math.abs(distance-best.distance)<1e-9&&priority<best.priority)){
    best={target,value,distance,priority};
   }
  }
  if(align){
   // Prefer matching edges to opposite edges, and center to center.
   for(const r of [{x:0,y:0,w:128,h:64},...peers]){
    const start=r[axis],length=axis==='x'?r.w:r.h;
    consider(start,0,0);consider(start+length,span,0);
    consider(start+length/2,span/2,1);
    consider(start,span,2);consider(start+length,0,2);
   }
  }
  if(best){matches[axis]=best.target;return best.value;}
  return clamp(snap(raw,step));
 }
 n.x=axisMove(x,'x',b.w,128);
 n.y=axisMove(y,'y',b.h,64);
 return matches;
}
const childIds=['callsign','dual-watch','battery','rssi','meter',...['a','b'].flatMap(side=>['marker','state','channel','frequency','mode'].map(k=>'vfo-'+side+'-'+k)),...['a','b'].flatMap(side=>modeNames.map(k=>'vfo-'+side+'-'+k)),'power-save','vox','lock','vfo-a-channel-name','vfo-b-channel-name'];
function encodeGroups(nodes,children=false){
 const groups=['status','vfo-a','vfo-b','signal','battery'];
 const out=[];
 for(const id of groups){
  const p=absolute(nodes,id),f=nodes.find(n=>n.id===id+'-frequency');
  out.push(id==='battery'&&children?109:p.x,p.y,f&&f.font==='compact'?2:f&&f.font==='factory'?1:0);
 }
 if(out[12]!==109||out[13]!==out[1])throw Error('Battery must remain at the native status coordinate');
 const occupied=new Set();
 for(let i=0;i<4;i++){const h=i===1||i===2?24:8;for(let y=out[3*i+1];y<out[3*i+1]+h;y++){if(occupied.has(y)||y>=64||out[i*3]!==0)throw Error('Legacy runtime requires full-width groups');occupied.add(y);}}
 if(children){for(const id of childIds){const n=nodes.find(n=>n.id===id);if(n.x<0||n.y<0)throw Error('Legacy runtime requires positive child offsets');out.push(n.x,n.y);}out.push(0,0,0,0,0);}
 return out.map(v=>v.toString(16).padStart(2,'0')).join('');
}
function encodeFree(nodes, names=false){
 const out=[];
 for(const n of nodes.filter(v=>childIds.includes(v.id)&&(names||!v.id.endsWith("channel-name")))){
  const id=n.id,p=absolute(nodes,id),b=extent(nodes,id);
  const w=n.id.endsWith('frequency')&&n.font==='custom'?72:n.id.endsWith('frequency')&&n.font==='compact'?54:n.w,h=n.id.endsWith('frequency')&&n.font==='custom'?12:n.id.endsWith('frequency')&&n.font==='compact'?10:n.h;
  if(p.x<0||p.y<0||p.x+w>128||p.y+h>64)throw Error(id+' needs room for its longest value ('+w+' × '+h+')');
  out.push(childIds.indexOf(id),p.x,p.y,n.id.endsWith('frequency')&&n.font==='custom'?2:n.id.endsWith('frequency')&&n.font==='compact'?1:0,n.opaque?1:0);
 }
 return out.map(v=>v.toString(16).padStart(2,'0')).join('');
}
function parseLayoutJSON(data){
 const expected=make(),byId=new Map(expected.map(n=>[n.id,n])),seen=new Set();
 if(!data||data.version!==2||!Array.isArray(data.widgets)||![expected.length,expected.length-2].includes(data.widgets.length))throw Error('Expected a Flux version 2 layout JSON file.');
 const nodes=data.widgets.map(input=>{
  const base=input&&byId.get(input.id);
  if(!base||seen.has(input.id)||input.parent!==base.parent||input.w!==base.w||input.h!==base.h||
     !Number.isInteger(input.x)||!Number.isInteger(input.y)||input.x < -128||input.x > 255||input.y < -64||input.y > 127||
     !['factory','compact','custom'].includes(input.font)||
     (input.opaque!==undefined&&typeof input.opaque!=='boolean'))throw Error('Invalid layout widget: '+(input&&input.id||'unknown'));
  if(!base.parent&&(input.x!==base.x||input.y!==base.y))throw Error('Layout group positions must match the radio runtime.');
  seen.add(input.id);
  return {...base,x:input.x,y:input.y,font:input.font,opaque:!!input.opaque};
 });
 return nodes.concat(expected.filter(n=>!seen.has(n.id)&&n.id.endsWith("channel-name")));
}
function create(editor,changed,hooks){
 const q=s=>editor.querySelector(s),svg=q('[data-layout-canvas]'),tree=q('[data-layout-tree]'),controls=q('[data-layout-controls]');
 if(!svg)return;
 let names=false;const namesToggle=q('[data-layout-channel-names]');
 let nodes=make(),id='vfo-a',drag=null,enabled=true,state={},history=[],future=[];
 const content=q('[data-layout-widgets]'),guides=q('[data-layout-guides]');
 const preview=k=>q('[data-preview-'+k+']');
 function previewState(){
   const side=preview('active').value,rx=preview('rx').value,n=Number(preview('signal').value);
   state={callsign:preview('call').value.toUpperCase(),'dual-watch':preview('dual').checked?'DW':'','power-save':preview('save').checked?'PS':'',vox:preview('vox').checked?'VX':'',lock:preview('lock').checked?'LK':'',rssi:rx==='off'?'':(n>9?-73+(n-9)*10:-127+n*6)+'dBm '+(n>9?'+'+(n-9)*10:'S'+n)};
   for(const k of ['a','b']){
     const p='vfo-'+k+'-',get=f=>q('[data-preview-'+f+'="'+k+'"]').value;
     const hz=Number(get('frequency'));state[p+'frequency']=Number.isFinite(hz)&&hz>=0&&hz<1000?hz.toFixed(5).padStart(9,' '):sample({id:p+'frequency'});
     const memory=get('side-mode')==='memory',slot=Math.max(0,Math.min(999,Math.trunc(Number(get('channel-slot')))||0));
     const name=memory?get('channel-name').slice(0,12):'';
     state[p+'channel-name']=name;state[p+'channel']=memory?'M'+slot:'VFO';
     const display=preview('memory-display').value;
     state[p+'frequency-display']=!names&&name&&display!=='frequency'?display:'';
     state[p+'frequency-name']=name;
     state[p+'state']=rx===k?'RX':'';state[p+'marker']=side===k?'>':'';
     for(const f of ['mode','power','bandwidth'])state[p+f]=get(f);
     state[p+'tone']='';state[p+'reverse']='';state[p+'offset']='';state[p+'break-in']='';state[p+'repeater']='';
   }
 }
 const snapshot=()=>({nodes:JSON.parse(JSON.stringify(nodes)),glyphs:Array.from(glyphs),hasFont,enabled,names});
 function remember(){history.push(snapshot());if(history.length>40)history.shift();future=[];historyButtons();}
 function historyButtons(){q('[data-layout-undo]').disabled=!history.length;q('[data-layout-redo]').disabled=!future.length;}
 function replay(from,to){if(!from.length)return;to.push(snapshot());const v=from.pop();nodes=v.nodes;glyphs=Uint8Array.from(v.glyphs);hasFont=v.hasFont;enabled=v.enabled;names=!!v.names;historyButtons();fontGrid();render();if(changed)changed();}
 q('[data-layout-undo]').onclick=()=>replay(history,future);q('[data-layout-redo]').onclick=()=>replay(future,history);
 for(const el of editor.querySelectorAll('[data-preview-active],[data-preview-rx],[data-preview-call],[data-preview-battery],[data-preview-signal],[data-preview-dual],[data-preview-save],[data-preview-vox],[data-preview-lock],[data-preview-frequency],[data-preview-channel-name],[data-preview-channel-slot],[data-preview-side-mode],[data-preview-memory-display],[data-preview-mode],[data-preview-power],[data-preview-bandwidth],[data-preview-guides]'))el.addEventListener('input',()=>{previewState();hooks.preview();});
 previewState();
 // Original seven-segment starter patterns, not a bundled third-party font.
 const segments=['ab cdef','bc','abdeg','abcdg','bcfg','acdfg','acdefg','abc','abcdefg','abcdfg'].map(s=>s.replaceAll(' ',''));
 const coords={a:[1,0,6,1],b:[6,1,1,5],c:[6,6,1,5],d:[1,11,6,1],e:[1,6,1,5],f:[1,1,1,5],g:[1,5,6,1]};
 const starter=()=>{const a=new Uint8Array(132);for(let d=0;d<10;d++)for(const k of segments[d]){const [x,y,w,h]=coords[k];for(let j=y;j<y+h;j++)for(let i=x;i<x+w;i++)a[d*12+j]|=128>>i;}a[10*12+10]=24;a[10*12+11]=24;return a;};
 let glyphs=starter(),hasFont=false,glyphIndex=0;
 const fontSuffix=()=>hasFont?'-f'+Array.from(glyphs,v=>v.toString(16).padStart(2,'0')).join(''):'';
 const canvas=q('[data-font-grid]'),select=q('[data-font-char]');
 function fontGrid(){canvas.replaceChildren();for(let y=0;y<12;y++)for(let x=0;x<8;x++){const b=document.createElement('button');b.type='button';b.setAttribute('aria-label','Pixel '+x+','+y);b.setAttribute('aria-pressed',String(!!(glyphs[glyphIndex*12+y]&(128>>x))));b.onclick=()=>{remember();glyphs[glyphIndex*12+y]^=128>>x;hasFont=true;fontGrid();render();notify();};canvas.append(b);}}
 for(let i=0;i<11;i++)select.add(new Option(i===10?'.':String(i),String(i)));
 select.onchange=()=>{glyphIndex=Number(select.value);fontGrid();};
 q('[data-font-reset]').onclick=()=>{remember();glyphs=starter();hasFont=true;fontGrid();render();notify();};
 q('[data-font-invert]').onclick=()=>{remember();for(let y=0;y<12;y++)glyphs[glyphIndex*12+y]^=255;hasFont=true;fontGrid();render();notify();};
 q('[data-font-clear]').onclick=()=>{remember();glyphs.fill(0,glyphIndex*12,glyphIndex*12+12);hasFont=true;fontGrid();render();notify();};
 q('[data-font-import]').onchange=async e=>{const f=e.target.files[0],msg=q('[data-font-message]');if(!f)return;try{if(f.size!==132)throw Error('Expected exactly 132 bytes (11 glyphs × 12 rows).');const bytes=new Uint8Array(await f.arrayBuffer());for(let i=0;i<132;i+=12)if(!bytes.slice(i,i+12).some(Boolean))throw Error('Every glyph must contain ink.');remember();glyphs=bytes;hasFont=true;fontGrid();render();notify();msg.textContent='Font imported.';}catch(err){msg.textContent=err.message;}finally{e.target.value='';}};
 q('[data-font-export]').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([glyphs]));a.download='flux-frequency.raw';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
 fontGrid();
 const field=k=>q('[data-layout-'+k+']'),sel=()=>nodes.find(n=>n.id===id);
 const label=n=>n.id.replace(/-mode$/,'-modulation').replace(/^vfo-([ab])-/,(_,s)=>s.toUpperCase()+' · ').replaceAll('-',' ');
 function element(tag,props){const el=document.createElementNS(NS,tag);Object.entries(props).forEach(([k,v])=>el.setAttribute(k,v));return el;}
 function render(){
  previewState();
  content.replaceChildren();guides.replaceChildren();
  const editing=hooks.editing(), showGuides=editing&&preview('guides').checked;
  svg.classList.toggle('layout-moving',editing);
  const step=Number(field('snap').value);
  if(step>1&&showGuides){const g=element('g',{stroke:'#758b71','stroke-width':'.15',opacity:'.45'});for(let x=step;x<128;x+=step)g.append(element('path',{d:'M'+x+' 0V64'}));for(let y=step;y<64;y+=step)g.append(element('path',{d:'M0 '+y+'H128'}));guides.append(g);}
  for(const n of nodes){
    if(!names&&n.id.endsWith("channel-name"))continue;
    const p=absolute(nodes,n.id),b=extent(nodes,n.id,state),group=element('g',{'data-widget':n.id,transform:'translate('+(p.x+b.x)+' '+(p.y+b.y)+')'});
    const isGroup=nodes.some(c=>c.parent===n.id);
    const key=n.id.replace(/^vfo-[ab]-/,'');
    if(isGroup){
      if(!editing)continue;
      const g=element('g',{'data-widget':n.id,transform:'translate('+(p.x+b.x)+' '+(p.y+b.y)+')'});
      g.append(element('rect',{width:b.w,height:b.h,fill:'none',stroke:showGuides?(n.id===id?'#2467c3':'#69866c'):'none','stroke-width':n.id===id?'.7':'.25','stroke-dasharray':'2 1','pointer-events':'stroke'}));
      guides.append(g);continue;
    }
    const value=sample(n,state), conditional=n.h===6;
    const dimensions=n.id.endsWith('frequency')?visualExtent(n,state):{w:conditional?value.length*4:n.w,h:n.h};
    if(n.opaque&&dimensions.w)group.append(element('rect',{width:dimensions.w,height:dimensions.h,fill:'#b7c8a1','pointer-events':'none'}));
    if(editing){
      const hit=element('rect',{width:Math.max(b.w,3),height:b.h,fill:'transparent',stroke:'none','pointer-events':'all'});
      group.append(hit);
      if(showGuides){const outline=element('rect',{x:p.x+b.x,y:p.y+b.y,width:Math.max(b.w,3),height:b.h,fill:'none',stroke:n.id===id?'#2467c3':'#69866c','stroke-width':n.id===id?'.7':'.25','pointer-events':'none'});guides.append(outline);}
    }
    if(!isGroup){
      if(n.id==='battery'||n.id==='meter'||key==='marker'){
        hooks.drawWidget(group,n.id,value,preview('rx').value!=='off');
      }else if(n.font==='custom'&&n.id.endsWith('frequency')&&!state[n.id+'-display']){
        for(const [i,ch] of Array.from(value).entries()){const d=ch==='.'?10:Number(ch);if(ch===' '||!Number.isInteger(d))continue;for(let y=0;y<12;y++)for(let x=0;x<8;x++)if(glyphs[d*12+y]&(128>>x))group.append(element('rect',{x:i*8+x,y,width:1,height:1,fill:'#172211','pointer-events':'none'}));}
      }else{
      const draw=(text,key,ox=0,oy=0)=>{const f=root.FluxLCDFonts[key];let d='';for(const [i,ch] of Array.from(text).entries()){const rows=f.glyphs[ch]||f.glyphs['?']||[];for(let y=0;y<rows.length;y++)for(let x=0;x<f.w;x++)if(rows[y]&(1<<x))d+='M'+(ox+i*f.w+x)+' '+(oy+y)+'h1v1h-1z';}if(d)group.append(element('path',{d,fill:'#172211','shape-rendering':'crispEdges','pointer-events':'none'}));};
      if(n.id.endsWith('frequency')&&state[n.id+'-display']){draw(state[n.id+'-name'],'small');if(state[n.id+'-display']==='namefreq')draw(value,'small',0,8);}
      else if(n.id.endsWith('frequency')&&n.font==='factory'){draw(value.slice(0,7),'large');draw(value.slice(7),'compact',70);}
      else draw(value,n.font==='compact'?'compact':n.h===6?'mode':'small');}
    }
    content.append(group);
  }
  if(editing&&drag&&drag.matches){
    const g=element('g',{'data-layout-snap-guides':'','pointer-events':'none',stroke:'#c026d3','stroke-width':'1.5','stroke-dasharray':'4 3'});
    for(const [axis,value] of Object.entries(drag.matches)){
      g.append(element('path',{d:axis==='x'?'M'+value+' 0V64':'M0 '+value+'H128','vector-effect':'non-scaling-stroke','data-snap-axis':axis}));
    }
    guides.append(g);
  }
  const n=sel(),b=extent(nodes,id,state);for(const k of ['x','y','font'])field(k).value=n[k];field('w').value=b.w;field('h').value=b.h;
  field('font').disabled=!n.id.endsWith('frequency');
  field('opaque').checked=!!n.opaque;field('opaque').disabled=!childIds.includes(n.id);
  for(const opt of tree.options)if(opt.value.endsWith("channel-name"))opt.hidden=!names;
  namesToggle.checked=names;tree.value=id;
 }
 function choose(next){id=next;render();}
 function point(e){const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());}
 for(const n of nodes){const opt=new Option((n.parent?'  ↳ ':'')+label(n),n.id);tree.add(opt);}
 tree.onchange=()=>choose(tree.value);
 namesToggle.onchange=()=>{remember();names=namesToggle.checked;if(names){for(const side of ["a","b"]){const f=nodes.find(n=>n.id==="vfo-"+side+"-frequency");const m=nodes.find(n=>n.id==="vfo-"+side+"-channel-name");if(f.font==="factory"&&f.x===44&&f.y===0&&m.x===44&&m.y===10)f.font="compact";}}if(!names&&id.endsWith("channel-name"))id="vfo-a";render();notify();};
 svg.addEventListener('pointerdown',e=>{
  if(!hooks.editing())return;const hit=e.target.closest('[data-widget]');if(!hit||e.button!==0)return;
  let next=nodes.find(n=>n.id===hit.dataset.widget);
  if(e.shiftKey&&next.parent)next=nodes.find(n=>n.id===next.parent);
  id=next.id;remember();const p=point(e);drag={id,x:next.x,y:next.y,px:p.x,py:p.y};svg.setPointerCapture(e.pointerId);e.preventDefault();render();
 });
 svg.addEventListener('pointermove',e=>{
  if(!drag)return;
  const p=point(e),ctm=svg.getScreenCTM(),scale=Math.hypot(ctm.a,ctm.b);
  // An 8-CSS-pixel magnetic zone, bounded to 0.5–3 native LCD pixels.
  const tolerance=Math.max(.5,Math.min(3,8/scale));
  drag.matches=move(nodes,id,drag.x+p.x-drag.px,drag.y+p.y-drag.py,
   e.altKey?1:Number(field('snap').value),field('align').checked&&!e.altKey,state,tolerance);
  render();
 });
 for(const event of ['pointerup','pointercancel'])svg.addEventListener(event,()=>{if(drag){drag=null;render();notify();}});
 for(const k of ['x','y'])field(k).onchange=()=>{remember();move(nodes,id,Number(field('x').value),Number(field('y').value),1,false,state);render();notify();};
 field('font').onchange=()=>{remember();sel().font=field('font').value;if(sel().font==='custom')hasFont=true;render();notify();};
 field('snap').onchange=render;
 field('opaque').onchange=()=>{remember();sel().opaque=field('opaque').checked;render();notify();};
 for(const [key,delta] of [['front',1],['back',-1]])field(key).onclick=()=>{const n=sel();if(!childIds.includes(n.id))return;remember();nodes=nodes.filter(v=>v!==n);if(delta>0)nodes.push(n);else nodes.splice(nodes.filter(v=>!v.parent).length,0,n);render();notify();};
 function notify(){enabled=true;try{encodeFree(nodes,names);q('[data-layout-message]').textContent=hasFont&&Array.from({length:11},(_,i)=>glyphs.slice(i*12,i*12+12).some(Boolean)).includes(false)?'A custom font glyph is empty; add ink before exporting.':'Layout selected · '+(names?170:160)+' bytes of widget descriptors'+(hasFont?' + 132 font bytes':'')+'.';}catch(e){q('[data-layout-message]').textContent=e.message;}if(changed)changed();}
 q('[data-layout-reset]').onclick=()=>{remember();nodes=make();choose('vfo-a');notify();};
 q('[data-layout-package]').onclick=async()=>{
  const msg=q('[data-layout-message]');
  try{
   const token=q('[data-editor-custom]').value;
   encodeFree(nodes,names);
   const data=new FormData();data.append('theme',token);
   const r=await fetch('/themes/export',{method:'POST',body:data});
   if(!r.ok)throw Error((await r.json()).error||'Export failed');
   const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download='flux-layout.fluxtheme';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
   msg.textContent='Runtime child layout exported.';
  }catch(e){msg.textContent=e.message;}
 };
 q('[data-layout-save]').onclick=()=>{const blob=new Blob([JSON.stringify({version:2,widgets:nodes,channelNames:names},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='flux-layout.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
 const layoutFile=q('[data-layout-import]');
 q('[data-layout-import-trigger]').onclick=()=>layoutFile.click();
 layoutFile.onchange=async e=>{
  const file=e.target.files[0],msg=q('[data-layout-message]');if(!file)return;
  try{
   if(file.size>65536)throw Error('Layout JSON exceeds 64 KiB.');
   const data=JSON.parse(await file.text());const incoming=parseLayoutJSON(data);
   let adjusted=0;
   for(const n of incoming.filter(v=>v.parent)){
    const x=n.x,y=n.y;move(incoming,n.id,x,y,1,false);
    if(n.x!==x||n.y!==y)adjusted++;
   }
   encodeFree(incoming,!!data.channelNames);
   remember();nodes=incoming;names=!!data.channelNames;
   const fontNote=nodes.some(n=>n.font==='custom')&&!hasFont;
   if(fontNote)hasFont=true;
   choose(id);notify();
   msg.textContent='Layout imported'+(adjusted?'; '+adjusted+' widgets fitted inside the display':'')+(fontNote?'; import your custom font bitmap separately':'')+'.';
  }catch(err){msg.textContent='Import failed: '+err.message;}
  finally{e.target.value='';}
 };
 render();
 return {
   render(){previewState();render();},
   activeSide(){return preview('active').value==='b'?1:0;},
   setActiveSide(a){preview('active').value=a?'b':'a';previewState();},
   snapshot,
   restore(v){nodes=JSON.parse(JSON.stringify(v.nodes));glyphs=Uint8Array.from(v.glyphs);hasFont=v.hasFont;enabled=v.enabled;names=!!v.names;drag=null;fontGrid();previewState();render();},
   groupY(side){return absolute(nodes,'vfo-'+side).y;},
   suffix(){return enabled?fontSuffix()+'-w'+encodeFree(nodes,names):'';},
   load(hex,fontHex,globalFont=0,inset=0){
     glyphs=fontHex?Uint8Array.from(fontHex.match(/../g).map(x=>parseInt(x,16))):starter();hasFont=!!fontHex;fontGrid();
     nodes=make();names=!!hex&&hex.length===340;enabled=true;history=[];future=[];historyButtons();
     if(hex){
       const b=hex.match(/../g).map(x=>parseInt(x,16));
       if(b.length===170||b.length===160||b.length===145||b.length===75){
       const order=[];
       for(let i=0;i<b.length;i+=5){const n=nodes.find(v=>v.id===childIds[b[i]]);if(!n)throw Error('Unknown widget');const p=absolute(nodes,n.parent);n.x=b[i+1]-p.x;n.y=b[i+2]-p.y;n.font=b[i+3]===2?'custom':b[i+3]===1?'compact':'factory';n.opaque=!!b[i+4];order.push(n);
         if(b.length===75&&[9,14].includes(b[i])){
           const side=b[i]===9?'a':'b';
           for(let j=0;j<modeNames.length;j++){const m=nodes.find(v=>v.id==='vfo-'+side+'-'+modeNames[j]);m.x=n.x+modeOffsets[j];m.y=n.y;m.opaque=n.opaque;order.push(m);}
         }
       }
       nodes=nodes.filter(n=>!n.parent).concat(order,nodes.filter(n=>n.parent&&!order.includes(n)));
     }else if(b.length===45){
       for(const [i,key] of childIds.slice(0,15).entries()){
         const n=nodes.find(v=>v.id===key),p=absolute(nodes,n.parent);
         n.x=b[i*3]-p.x;n.y=b[i*3+1]-p.y;n.font=b[i*3+2]===1?'compact':'factory';
       }
     }else for(const [i,key] of ['status','vfo-a','vfo-b','signal'].entries()){
         const n=nodes.find(v=>v.id===key);n.x=b[i*3];n.y=b[i*3+1];
         const f=nodes.find(v=>v.id===key+'-frequency');if(f)f.font=b[i*3+2]===2?'compact':'factory';
       }
       if(b.length===50)for(const [i,key] of childIds.slice(0,15).entries()){
         const n=nodes.find(v=>v.id===key);n.x=b[15+i*2];n.y=b[16+i*2];
       }
       if(b.length===15||b.length===50){
         for(const side of ['a','b']){
           const n=nodes.find(v=>v.id==='vfo-'+side+'-mode');
           for(let j=0;j<modeNames.length;j++){const m=nodes.find(v=>v.id==='vfo-'+side+'-'+modeNames[j]);m.x=n.x+modeOffsets[j];m.y=n.y;}
         }
       }
     }
     if(!hex||[30,100].includes(hex.length)){
       for(const side of ['a','b']){
         const n=nodes.find(v=>v.id==='vfo-'+side+'-frequency'),code=hex?parseInt(hex.slice((side==='a'?5:8)*2,(side==='a'?5:8)*2+2),16):0;
         n.font=(code?code-1:globalFont)?'compact':'factory';n.x=Math.max(0,n.x+(n.font==='compact'?28:0)-inset);
       }
     }
     render();
   }
 };
}
const api={make,absolute,extent,move,snap,encodeGroups,encodeFree,parseLayoutJSON,create};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.FluxLayout=api;
})(typeof window!=='undefined'?window:globalThis);
