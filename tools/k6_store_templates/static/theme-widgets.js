/* Bounded battery and S-meter glyph editors; row-major LCD pixels. */
(function () {
'use strict';
const NS='http://www.w3.org/2000/svg';
const normal=()=>Array(7).fill(0xf0),over=()=>[0xf0,0x90,0x90,0x90,0x90,0x90,0xf0];
const meterDefault=()=>[...normal(),...over()].map(x=>x.toString(16).padStart(2,'0')).join('');
const outline=[0x1f,0xff,0x80,0x60,0,0x80,0x40,0,0x80,0x40,0,0x80,0x40,0,0x80,0x60,0,0x80,0x1f,0xff,0x80,0,0,0];
const segment=[0xc0,0,0xc0,0xc0,0xc0,0,0xc0,0];
const hex=xs=>Array.from(xs,x=>x.toString(16).padStart(2,'0')).join('');
const bytes=s=>Uint8Array.from(s.match(/../g).map(x=>parseInt(x,16)));
function set(p,x,y,stride,on){const i=y*stride+(x>>3),mask=128>>(x%8);p[i]=on?p[i]|mask:p[i]&~mask;}
function get(p,x,y,stride){return !!(p[y*stride+(x>>3)]&(128>>(x%8)));}
function batteryDefault(){
  const all=new Uint8Array(120);
  for(let n=0;n<5;n++){
    const p=Uint8Array.from(outline);
    for(let i=4-n;i<4;i++)for(let y=0;y<8;y++)for(let x=0;x<2;x++)set(p,4+i*3+x,y,3,!!(segment[y]&(128>>x)));
    all.set(p,n*24);
  }
  return all;
}
function rect(group,x,y,w,h,fill){
  const e=document.createElementNS(NS,'rect');
  for(const [k,v] of Object.entries({x,y,width:w,height:h}))e.setAttribute(k,v);
  if(fill)e.setAttribute('fill',fill);group.append(e);
}
function glyph(g,p,w,h,stride,x,y){
  for(let r=0;r<h;r++)for(let c=0;c<w;c++)if(get(p,c,r,stride))rect(g,x+c,y+r,1,1);
}
function grid(root,w,h,read,toggle){
  const cells=[];
  root.style.gridTemplateColumns='repeat('+w+',minmax(12px,20px))';
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){
    const b=document.createElement('button');b.type='button';
    b.setAttribute('aria-label','Pixel '+(x+1)+', '+(y+1));
    b.addEventListener('click',()=>toggle(x,y));
    root.append(b);cells.push(b);
  }
  return ()=>cells.forEach((b,i)=>b.setAttribute('aria-pressed',String(read(i%w,Math.floor(i/w)))));
}
function create(editor,changed,preview){
  const q=s=>editor.querySelector(s);
  const font=q('[data-theme-font]'),inset=q('[data-theme-inset]'),style=q('[data-theme-meter-style]');
  const meterPanel=q('[data-theme-custom-meter]'),batteryPanel=q('[data-theme-custom-battery]'),batteryOn=q('[data-theme-battery-on]');
  const signal=q('[data-preview-signal]'),charge=q('[data-preview-battery]'),batteryLevel=q('[data-theme-battery-edit]');
  const logos=window.FluxSplashLogos||[];
  const iconFromRows=rows=>Uint8Array.from((rows||Array(16).fill(0)).flatMap(row=>[(row>>8)&255,row&255]));

  let m=bytes(meterDefault()),b=batteryDefault(),icon=iconFromRows(logos[0]),legacySplash='0';
  const drawMeter=[0,1].map(i=>grid(q('[data-theme-meter-grid="'+i+'"]'),5,7,
    (x,y)=>get(m.subarray(i*7),x,y,1),
    (x,y)=>{const p=m.subarray(i*7);set(p,x,y,1,!get(p,x,y,1));changed();}));
  const drawBattery=grid(q('[data-theme-battery-grid]'),17,8,
    (x,y)=>get(b.subarray(Number(batteryLevel.value)*24),x,y,3),
    (x,y)=>{const p=b.subarray(Number(batteryLevel.value)*24);set(p,x,y,3,!get(p,x,y,3));changed();});
  const meterText=q('[data-theme-meter-level]'),batteryText=q('[data-theme-battery-level]');
  function drawWidget(group,id,rx){
    if(id==='battery'){glyph(group,(batteryOn.checked?b:batteryDefault()).subarray(Number(charge.value)*24),17,8,3,0,0);return;}
    if(id!=='meter'||!rx)return;
    const n=Number(signal.value);
    if(style.value==='3'){
      for(let i=0;i<n;i++)glyph(group,m.subarray(i<9?0:7),5,7,1,i*5,1);
    }else if(style.value==='2'){
      if(n>0)rect(group,0,1,Math.min(n,9)*5-1,7);
      if(n>9){const w=(n-9)*5-1;rect(group,45,1,w,1);rect(group,45,7,w,1);rect(group,45,2,1,5);rect(group,44+w,2,1,5);}
    }else{
      for(let i=0;i<n;i++){const h=style.value==='1'?7:Math.min(i+1,7),x=i*5;
        if(i<9)rect(group,x,8-h,4,h);
        else{rect(group,x,8-h,4,1);rect(group,x,7,4,1);rect(group,x,9-h,1,h-2);rect(group,x+3,9-h,1,h-2);}
      }
    }
  }
  function render(){
    meterPanel.hidden=style.value!=='3';batteryPanel.hidden=!batteryOn.checked;
    drawMeter.forEach(f=>f());drawBattery();
    const n=Number(signal.value),bars=Number(charge.value);
    meterText.textContent=n+' / 13';batteryText.textContent=bars+' / 4';
    const full=q('[data-theme-full-splash]');
    if(!full.childElementCount&&Array.isArray(window.FluxBootSplash)&&window.FluxBootSplash.length===1024){
      let path='';const art=window.FluxBootSplash;
      for(let page=0;page<8;page++)for(let x=0;x<128;x++)for(let bit=0;bit<8;bit++)
        if(art[page*128+x]&(1<<bit))path+='M'+x+' '+(page*8+bit)+'h1v1h-1z';
      const shape=document.createElementNS(NS,'path');shape.setAttribute('d',path);full.append(shape);
    }
  }
  for(const el of [font,inset,style,batteryOn])el.addEventListener('change',changed);
  for(const el of [batteryLevel])el.addEventListener('input',()=>{if(el===batteryLevel)charge.value=el.value;preview();});
  for(const el of editor.querySelectorAll('[data-theme-meter-preset]'))el.addEventListener('click',()=>{
    const [i,kind]=el.dataset.themeMeterPreset.split(':');const v=kind==='continuous'?Array(7).fill(0xf8):kind==='solid'?normal():kind==='hollow'?over():kind==='clear'?Array(7).fill(0):Number(i)?over():normal();
    m.set(v,Number(i)*7);changed();
  });
  q('[data-theme-battery-reset]').addEventListener('click',()=>{b=batteryDefault();changed();});
  q('[data-theme-battery-copy]').addEventListener('click',()=>{const n=Number(batteryLevel.value);if(n<4){b.copyWithin((n+1)*24,n*24,n*24+24);batteryLevel.value=String(n+1);charge.value=String(n+1);changed();}});
  return {
    render,drawWidget,
    suffix(){const digits=font.value+inset.value+style.value+legacySplash;
      return (digits==='0000'?'':'-'+digits)+(style.value==='3'?'-m'+hex(m):'')+(batteryOn.checked?'-b'+hex(b):'')+(legacySplash==='3'?'-g'+hex(icon):'');},
    load(parts){
      const d=parts.find(x=>/^[01][0-3][0-3][0-3]$/.test(x))||'0000';
      [font.value,inset.value,style.value,legacySplash]=d.split('');
      const bm=parts.find(x=>x.startsWith('m')),bt=parts.find(x=>x.startsWith('b')),gi=parts.find(x=>x.startsWith('g'));
      m=bm?bytes(bm.slice(1)):bytes(meterDefault());b=bt?bytes(bt.slice(1)):batteryDefault();batteryOn.checked=!!bt;
      icon=gi?bytes(gi.slice(1)):iconFromRows(logos[Number(legacySplash)]||logos[0]);
    }
  };
}
window.FluxWidgets={create};
})();
