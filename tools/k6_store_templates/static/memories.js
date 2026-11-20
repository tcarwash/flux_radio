(() => {
  const $ = id => document.getElementById(id);
  const packs = JSON.parse($('memory-packs').textContent);
  let rows = [], results = [];
  try { rows = JSON.parse(localStorage.getItem('flux-memories-v1') || '[]'); if (!Array.isArray(rows)) rows=[]; } catch {}
  const status = message => { $('status').textContent = message; };
  const cell = (tr, value) => { const td = tr.insertCell(); td.textContent = value ?? ''; return td; };
  function render() {
    $('memory-rows').replaceChildren();
    rows.forEach((r,i) => {
      const tr = $('memory-rows').insertRow();
      for (const k of ['Location','Name','Frequency','Duplex','Offset','Mode','Tone','rToneFreq','cToneFreq','DtcsCode','RxDtcsCode','DtcsPolarity','CrossMode','Power','Skip','Comment']) {
        const td = tr.insertCell(), input = document.createElement('input');
        input.value = r[k] ?? ''; input.setAttribute('aria-label', k + ' channel ' + (i+1));
        input.addEventListener('change', () => { r[k]=input.value; save(); });
        td.append(input);
      }
      const button = document.createElement('button'); button.textContent='Remove';
      button.onclick=()=>{ rows.splice(i,1); save(); render(); }; tr.insertCell().append(button);
    });
    $('count').textContent = '('+rows.length+')';
  }
  function save(){ try { localStorage.setItem('flux-memories-v1',JSON.stringify(rows)); } catch { status('Unable to save working list in this browser. Export to keep it.'); } }
  function key(r){return JSON.stringify(['Frequency','Duplex','Offset','Mode','Tone','rToneFreq','cToneFreq','DtcsCode','RxDtcsCode','DtcsPolarity','CrossMode'].map(k=>['Frequency','Offset','rToneFreq','cToneFreq'].includes(k)?Number(r[k]):r[k]||''));}
  function add(items){const keys=new Set(rows.map(key)); let added=0; for(const r of items){const k=key(r);if(!keys.has(k)){rows.push({...r,Location:""});keys.add(k);added++;}}save();render();status('Added '+added+' channels; '+(items.length-added)+' duplicates skipped.');}
  async function checked(response){if(!response.ok){let msg;try{msg=(await response.json()).error;}catch{}throw new Error(msg||'Request failed');}return response;}
  document.querySelectorAll('[data-pack]').forEach(b=>b.onclick=()=>add(packs[Number(b.dataset.pack)]));
  $('import-file').onchange=async()=>{const f=$('import-file').files[0];if(!f)return;const data=new FormData();data.append('file',f);try{const r=await checked(await fetch('/memories/import',{method:'POST',body:data}));add((await r.json()).channels);}catch(e){status(e.message);}finally{$('import-file').value='';}};
  async function download(format){try{const r=await checked(await fetch('/memories/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({format,channels:rows})}));const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download='memories.'+(format==='csv'?'csv':'fluxmem');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);status('Exported '+rows.length+' channels.');}catch(e){status(e.message);}}
  $('export-csv').onclick=()=>download('csv');$('export-pack').onclick=()=>download('fluxmem');
  $('clear').onclick=()=>{if(confirm('Clear your working memory list?')){rows=[];save();render();}};
  $('locate').onclick=()=>{if(!navigator.geolocation){status('Location unavailable. Enter coordinates manually.');return;}navigator.geolocation.getCurrentPosition(p=>{const f=$('discovery');f.elements.lat.value=p.coords.latitude;f.elements.lon.value=p.coords.longitude;},()=>status('Location unavailable. Enter coordinates manually.'));};
  const radioButtons=()=>[$('radio-location'),...document.querySelectorAll('[data-radio]')];
  function setRadioBusy(busy){radioButtons().forEach(b=>b.disabled=busy||!$('radio-program').dataset.port);}
  $('radio-location').onclick=async()=>{
    const legacy=$('legacy-radio-read').checked;
    if(legacy&&!confirm('Use legacy cable mode? The radio will restart.'))return;
    setRadioBusy(true);$('location-status').textContent='Reading radio…';
    try{
      const r=await checked(await fetch('/memories/radio/location'+(legacy?'?legacy=1':''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:$('radio-program').dataset.token})}));
      const data=await r.json(),f=$('discovery');f.elements.lat.value=data.lat;f.elements.lon.value=data.lon;
      $('location-status').textContent='Saved APRS location loaded.';
    }catch(e){$('location-status').textContent=e.message;}finally{setRadioBusy(false);}
  };
  $('add-blank').onclick=()=>{rows.push({Location:'',Name:'',Frequency:'146.520000',Mode:'FM',Offset:'0'});save();render();};
  $('discovery').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget;const query=new URLSearchParams(new FormData(form));query.set('open',form.elements.open.checked?'1':'0');const b=form.querySelector('[type=submit]');b.disabled=true;b.textContent='Searching…';$('results').hidden=true;try{const r=await checked(await fetch('/memories/repeaters?'+query));results=(await r.json()).channels;$('result-rows').replaceChildren();$('all-results').checked=true;results.forEach((row,i)=>{const tr=$('result-rows').insertRow(), check=document.createElement('input');check.type='checkbox';check.checked=true;check.dataset.result=i;check.setAttribute('aria-label','Select '+row.Name);tr.insertCell().append(check);for(const k of ['Name','Frequency','Duplex','Offset','Comment'])cell(tr,row[k]);});$('results').hidden=false;status('Found '+results.length+' repeaters.');}catch(err){status(err.message);}finally{b.disabled=false;b.textContent='Find repeaters';}};
  $('all-results').onchange=()=>document.querySelectorAll('[data-result]').forEach(c=>c.checked=$('all-results').checked);
  $('add-results').onclick=()=>add([...document.querySelectorAll('[data-result]:checked')].map(c=>results[Number(c.dataset.result)]));
  document.querySelectorAll('[data-radio]').forEach(button=>button.onclick=async()=>{
    const action=button.dataset.radio;
    const live=action==='read'&&!$('legacy-radio-read').checked;
    if(!confirm(action==='read'?('Read radio and replace the browser working list? Export your current list first if needed.'+(live?'':' The radio will restart.')):action==='recover'?'Resume the saved pending memory write for this radio?':action==='replace'?'Replace ALL radio memories with this list? A binary backup will be saved.':'Merge this list into free radio memory slots?'))return;
    setRadioBusy(true);status('Communicating with radio — keep cable connected.');
    try{const response=await checked(await fetch('/memories/radio'+(live?'/live-bank':''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:$('radio-program').dataset.token,action,confirm:action,channels:rows})}));const result=await response.json();
    if(action==='read'){const opaque=result.opaque_slots||[];if(opaque.length&&!confirm('Some radio records could not be decoded ('+opaque.slice(0,10).join(', ')+'). Load the remaining channels?')){status('Read completed; working list unchanged.');return;}rows=result.channels;save();render();status('Read '+rows.length+' memories; '+opaque.length+' unrecognized records (preserved by Merge).'+(result.pending?' Pending write: choose Resume interrupted write.':''));}
    else status('Verified '+result.occupied+' memories; '+result.added+' added, '+result.duplicates+' duplicates skipped. Backup: '+result.backup);
    }catch(e){status(e.message);}finally{setRadioBusy(false);}
  });
  $('advanced-fields').onchange=()=> $('memory-table').classList.toggle('show-advanced',$('advanced-fields').checked);
  render();
})();
