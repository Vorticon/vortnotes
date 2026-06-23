(() => {
  'use strict';
  const config=window.VORTNOTES_STICKY_CONFIG||{};
  let notes=Array.isArray(window.VORTNOTES_STICKY_NOTES)?window.VORTNOTES_STICKY_NOTES:[];
  const grid=document.getElementById('vnStickyGrid');
  const search=document.getElementById('vnStickySearch');
  const status=document.getElementById('vnStickyStatus');
  const timers=new Map();
  const colors=['yellow','pink','blue','green','purple','orange'];
  const token=()=>document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')||'';
  const setStatus=text=>{if(status)status.textContent=text||'';};
  const api=async(url,payload)=>{
    const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRFToken':token()},body:JSON.stringify(payload)});
    const data=await response.json().catch(()=>({}));
    if(!response.ok||!data.ok)throw new Error(data.error||'Request failed');
    return data;
  };
  const filteredNotes=()=>{const q=(search?.value||'').trim().toLowerCase();return q?notes.filter(note=>`${note.title||''}\n${note.body||''}`.toLowerCase().includes(q)):notes;};
  const saveLater=note=>{
    if(!config.canEdit)return;
    clearTimeout(timers.get(note.id));
    timers.set(note.id,setTimeout(async()=>{setStatus('Saving…');try{const data=await api(config.saveUrl,note);Object.assign(note,data.note);setStatus('All notes saved.');const foot=document.querySelector(`[data-note-id="${note.id}"] .vn-sticky-foot`);if(foot)foot.textContent='Saved just now';}catch(_){setStatus('Could not save this note.');}},450));
  };
  const makeNote=note=>{
    const card=document.createElement('article');card.className=`vn-sticky-note vn-sticky-${colors.includes(note.color)?note.color:'yellow'}`;card.dataset.noteId=String(note.id);card.style.setProperty('--tilt',`${((Number(note.id)%5)-2)*.35}deg`);
    const head=document.createElement('div');head.className='vn-sticky-note-head';
    const title=document.createElement('input');title.className='vn-sticky-title';title.value=note.title||'';title.placeholder='Untitled';title.maxLength=120;title.disabled=!config.canEdit;
    const color=document.createElement('select');color.className='vn-sticky-color';color.title='Note color';color.disabled=!config.canEdit;colors.forEach(name=>{const option=document.createElement('option');option.value=name;option.textContent=name[0].toUpperCase();option.selected=name===note.color;color.appendChild(option);});
    const del=document.createElement('button');del.type='button';del.className='vn-sticky-delete';del.title='Delete note';del.setAttribute('aria-label','Delete note');del.textContent='×';del.hidden=!config.canEdit;
    const body=document.createElement('textarea');body.className='vn-sticky-body';body.value=note.body||'';body.placeholder='Write something…';body.maxLength=10000;body.disabled=!config.canEdit;
    const foot=document.createElement('div');foot.className='vn-sticky-foot';foot.textContent='';
    title.addEventListener('input',()=>{note.title=title.value;saveLater(note);});body.addEventListener('input',()=>{note.body=body.value;saveLater(note);});color.addEventListener('change',()=>{note.color=color.value;card.className=`vn-sticky-note vn-sticky-${note.color}`;saveLater(note);});
    del.addEventListener('click',async()=>{if(!confirm('Delete this sticky note?'))return;clearTimeout(timers.get(note.id));try{await api(config.deleteUrl,{id:note.id});notes=notes.filter(item=>item.id!==note.id);render();setStatus('Note deleted.');}catch(_){setStatus('Could not delete this note.');}});
    head.append(title,color,del);card.append(head,body,foot);return card;
  };
  const render=()=>{if(!grid)return;grid.innerHTML='';const visible=filteredNotes();if(!visible.length){const empty=document.createElement('div');empty.className='vn-sticky-empty';empty.textContent=notes.length?'No notes match your search.':'No sticky notes yet.';grid.appendChild(empty);return;}visible.forEach(note=>grid.appendChild(makeNote(note)));};
  document.getElementById('vnStickyAdd')?.addEventListener('click',async()=>{setStatus('Creating note…');try{const data=await api(config.saveUrl,{title:'',body:'',color:'yellow'});notes.unshift(data.note);if(search)search.value='';render();setStatus('New note created.');grid.querySelector('.vn-sticky-title')?.focus();}catch(_){setStatus('Could not create a note.');}});
  search?.addEventListener('input',render);
  render();
})();
