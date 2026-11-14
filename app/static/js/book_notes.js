/* Account-private page markup. The PDF and its publication rules stay authoritative. */
window.aelmsModules.register(document.currentScript, function(scope) {
  'use strict';
  const toolbar = document.querySelector('[data-book-notes]');
  if (!toolbar) return;
  const reader = toolbar.closest('.sf-reader,.cb-reader');
  const toggle = reader.querySelector('[data-book-notes-toggle]');
  const hub = toolbar.querySelector('[data-note-hub]');
  const tools = toolbar.querySelector('[data-note-tools]');
  const options = toolbar.querySelector('[data-note-options]');
  const optionsToggle = toolbar.querySelector('[data-note-options-toggle]');
  const status = toolbar.querySelector('[data-note-status]');
  const dialog = reader.querySelector('[data-note-dialog]');
  const input = dialog.querySelector('[data-note-text]');
  const color = toolbar.querySelector('[data-note-color]');
  const width = toolbar.querySelector('[data-note-width]');
  const undo = toolbar.querySelector('[data-note-undo]');
  const redo = toolbar.querySelector('[data-note-redo]');
  const owner = toolbar.dataset.owner;
  const csrf = document.querySelector('meta[name=csrf-token]')?.content || '';
  const headers = {'X-Book-Notes-Owner':owner};
  const ns = 'http://www.w3.org/2000/svg';
  const states = [];
  const past = [], future = [], pointers = new Set();
  const preferenceKey = `aelms-floating-pen:${owner}`;
  let preference = {visible:true,x:1,y:.52};
  try { preference = {...preference,...JSON.parse(localStorage.getItem(preferenceKey))}; } catch (_) {}
  let mode = null, active = null, edit = null, orbitOpen = false, drag = null, suppressTap = false;
  let idleTimer = 0;
  const clamp = (value,min,max) => Math.min(max,Math.max(min,value));
  function savePreference() {
    try { localStorage.setItem(preferenceKey,JSON.stringify(preference)); } catch (_) {}
  }
  function viewport() {
    const header = reader.querySelector('.sf-header');
    const top = Math.max(35, Math.ceil(header?.getBoundingClientRect().bottom || 0) + 35);
    return {left:34,right:Math.max(34,window.innerWidth-34),top,bottom:Math.max(top,window.innerHeight-34)};
  }
  function position() {
    const bounds=viewport();
    const x=bounds.left+clamp(Number(preference.x)||0,0,1)*(bounds.right-bounds.left);
    const y=bounds.top+clamp(Number(preference.y)||0,0,1)*(bounds.bottom-bounds.top);
    const orbitRadius=Math.min(78,Math.max(65,(window.innerHeight-185)/2));
    const room=orbitRadius+28;
    const bottom=Math.max(room+6,innerHeight-room-28);
    const top=Math.min(bottom,bounds.top+room-27);
    const dockLeft=preference.x<.5;
    toolbar.style.setProperty('--note-radius',`${orbitRadius}px`);
    toolbar.style.setProperty('--note-x',`${orbitOpen ? clamp(x,room,Math.max(room,innerWidth-room)) : x}px`);
    toolbar.style.setProperty('--note-y',`${orbitOpen ? clamp(y,top,bottom) : y}px`);
    toolbar.style.setProperty('--note-dock-offset',`${dockLeft ? 8-x : innerWidth-8-x}px`);
  }
  function wakeHub() {
    toolbar.classList.remove('is-idle');
    scope.clearTimeout(idleTimer);
    if (toolbar.hidden || orbitOpen || drag || active || dialog.open) return;
    idleTimer=scope.setTimeout(() => {
      if (!orbitOpen && !drag && !active && !dialog.open) toolbar.classList.add('is-idle');
    },5500);
  }
  function showOptions(value) {
    options.hidden=!value;
    toolbar.classList.toggle('has-options',value);
    tools.inert=value || !orbitOpen;
    optionsToggle.setAttribute('aria-expanded',String(value));
    if (value) options.querySelector('select').focus({preventScroll:true});
  }
  function setMode(value) {
    cancelStroke(); mode=value;
    if (value && !orbitOpen && !toolbar.hidden) reader.dataset.noteMode=value;
    else delete reader.dataset.noteMode;
    toolbar.querySelectorAll('[data-note-tool]').forEach(button => button.setAttribute('aria-pressed',String(button.dataset.noteTool===value)));
    hub.classList.toggle('is-writing',Boolean(value));
  }
  function openOrbit(value) {
    const hadFocus=tools.contains(document.activeElement) || options.contains(document.activeElement);
    cancelStroke(); orbitOpen=value;
    toolbar.classList.toggle('is-open',value);
    showOptions(false);
    hub.setAttribute('aria-expanded',String(value));
    hub.setAttribute('aria-label',value ? hub.dataset.closeLabel : hub.dataset.openLabel);
    if (!value && hadFocus) hub.focus({preventScroll:true});
    setMode(mode); position();
    wakeHub();
    if (value) { states.filter(state=>state.visible && !state.ready).forEach(load); message(); }
  }
  function setFloating(visible) {
    if (!visible) { setMode(null); openOrbit(false); }
    toolbar.hidden=!visible;
    toggle.setAttribute('aria-pressed',String(visible));
    toggle.title=visible ? toggle.dataset.hideLabel : toggle.dataset.showLabel;
    toggle.setAttribute('aria-label',toggle.title);
    position();
    wakeHub();
  }
  function inkColor() { toolbar.style.setProperty('--note-ink',color.value); }
  function message() {
    const blocked = states.find(state => state.error);
    toolbar.classList.toggle('has-sync-error',Boolean(blocked));
    status.textContent = blocked ? toolbar.dataset[blocked.error] : states.some(state => state.pending.size || state.saving)
      ? toolbar.dataset.saving : toolbar.dataset.saved;
  }
  function persist(state) {
    try {
      if (state.pending.size) localStorage.setItem(state.key, JSON.stringify([...state.pending]));
      else localStorage.removeItem(state.key);
    } catch (_) { /* The in-memory queue still retries when storage is unavailable. */ }
  }
  function overlayPending(state, marks) {
    const map = new Map(marks.map(mark => [mark.id,mark]));
    for (const [id,mark] of state.pending) { if (mark) map.set(id,mark); else map.delete(id); }
    return [...map.values()];
  }
  function path(state, mark) {
    const node = document.createElementNS(ns,'polyline');
    node.setAttribute('points', mark.points.map(point => `${point[0]},${point[1]*state.ratio}`).join(' '));
    node.setAttribute('fill','none'); node.setAttribute('stroke',mark.color);
    node.setAttribute('stroke-width',mark.size);
    node.setAttribute('stroke-linecap','round'); node.setAttribute('stroke-linejoin','round');
    if (mark.kind === 'highlight') node.setAttribute('opacity','.32');
    return node;
  }
  function render(state) {
    state.ratio = state.sheet.clientHeight / Math.max(1,state.sheet.clientWidth);
    state.svg.setAttribute('viewBox',`0 0 10000 ${10000*state.ratio}`);
    state.svg.replaceChildren(); state.texts.replaceChildren();
    for (const mark of state.marks) {
      if (active?.state === state && active.erased?.has(mark.id)) continue;
      if (mark.kind !== 'text') state.svg.append(path(state,mark));
      else {
        const node = document.createElement('div'); node.className = 'book-note-text';
        node.dataset.noteId = mark.id; node.dir = 'auto'; node.textContent = mark.text;
        Object.assign(node.style,{left:`${mark.x/100}%`,top:`${mark.y/100}%`,
          color:mark.color,fontSize:`${mark.size/100}cqw`});
        state.texts.append(node);
        const box=state.sheet.getBoundingClientRect(), bounds=node.getBoundingClientRect();
        node.style.left=`${Math.max(0,Math.min(mark.x/100,100-bounds.width/box.width*100))}%`;
        node.style.top=`${Math.max(0,Math.min(mark.y/100,100-bounds.height/box.height*100))}%`;
      }
    }
    if (active?.state === state && active.mark) state.svg.append(path(state,active.mark));
  }
  function historyButtons() { undo.disabled = !past.length; redo.disabled = !future.length; }
  function change(state, add, remove, remember=true) {
    state.generation=(state.generation || 0)+1;
    const removed = state.marks.filter(mark => remove.includes(mark.id));
    if (remember) { past.push({state,add,removed}); if (past.length > 100) past.shift(); future.length=0; }
    for (const id of remove) state.pending.set(id,null);
    for (const mark of add) state.pending.set(mark.id,mark);
    state.marks = overlayPending(state,state.marks);
    state.error = null; persist(state); render(state); historyButtons(); message();
    scope.clearTimeout(state.timer);
    state.timer = scope.setTimeout(() => save(state),300);
  }
  async function save(state) {
    if (state.saving || !state.ready || !state.pending.size || state.stopped || scope.signal.aborted) return;
    const batch = new Map(state.pending);
    const body = JSON.stringify({add:[...batch.values()].filter(Boolean),remove:[...batch].filter(([,mark]) => !mark).map(([id]) => id)});
    state.saving=true; message();
    try {
      const response = await fetch(state.url,{method:'POST',headers:{...headers,'Content-Type':'application/json','X-CSRFToken':csrf},body,credentials:'same-origin',signal:scope.signal});
      if (!response.ok) {
        if ([401,403,404].includes(response.status) || response.redirected) state.stopped=true;
        state.error = response.status === 413 ? 'limit' : 'retry';
        throw new Error('save');
      }
      const data = await response.json();
      if (!Array.isArray(data.marks) || data.book !== state.book) throw new Error('response');
      for (const [id,mark] of batch) if (state.pending.get(id) === mark) state.pending.delete(id);
      state.marks = overlayPending(state,data.marks); state.error=null; state.delay=2000;
      persist(state); render(state);
    } catch (_) { if (!state.error) state.error='retry'; }
    finally {
      state.saving=false; message();
      if (state.pending.size && !state.stopped && state.error !== 'limit' && !scope.signal.aborted) {
        state.timer = scope.setTimeout(() => save(state),state.error ? state.delay : 100);
        state.delay = Math.min(30000,state.delay*2);
      }
    }
  }
  async function load(state) {
    if (state.loading || state.saving || state.stopped || scope.signal.aborted) return;
    state.loading=true;
    const generation=state.generation || 0;
    try {
      const response = await fetch(state.url,{headers,credentials:'same-origin',cache:'no-store',signal:scope.signal});
      if (!response.ok || response.redirected) {
        if ([401,403,404].includes(response.status) || response.redirected) state.stopped=true;
        throw new Error('load');
      }
      const data = await response.json();
      if (!Array.isArray(data.marks) || !/^[a-f0-9]{64}$/.test(data.book)) throw new Error('response');
      if (state.ready && generation!==(state.generation || 0)) return;
      // Queue keys include the exact PDF hash so a replacement book never inherits old ink.
      const key = `aelms-book-notes:${owner}:${data.book}:${state.sheet.dataset.sheet}`;
      if (!state.ready) {
        state.key=key;
        try { const queue = JSON.parse(localStorage.getItem(key) || '[]'); if (Array.isArray(queue)) state.pending=new Map(queue); } catch (_) {}
      }
      state.book=data.book; state.marks=overlayPending(state,data.marks); state.ready=true;
      state.error=null; render(state); if (state.pending.size) save(state);
    } catch (_) { if (!scope.signal.aborted) state.error='retry'; }
    finally { state.loading=false; message(); }
  }
  function point(state,event) {
    const box=state.sheet.getBoundingClientRect();
    return [Math.round(Math.max(0,Math.min(1,(event.clientX-box.left)/box.width))*10000),
      Math.round(Math.max(0,Math.min(1,(event.clientY-box.top)/box.height))*10000)];
  }
  function eraseAt(state,pixel) {
    if (!active?.erased) return;
    const box=state.sheet.getBoundingClientRect();
    const toPixel=p => [p[0]*box.width/10000,p[1]*box.height/10000];
    const p=toPixel(pixel), radius=12;
    const distance=(p,a,b) => {
      const x=b[0]-a[0], y=b[1]-a[1], t=Math.max(0,Math.min(1,((p[0]-a[0])*x+(p[1]-a[1])*y)/(x*x+y*y||1)));
      return Math.hypot(p[0]-a[0]-t*x,p[1]-a[1]-t*y);
    };
    for (const mark of [...state.marks].reverse()) {
      if (active.erased.has(mark.id)) continue;
      if (mark.kind === 'text') {
        const node=[...state.texts.children].find(node => node.dataset.noteId === mark.id);
        const bounds=node?.getBoundingClientRect();
        if (bounds && pixel[0]/10000*box.width >= bounds.left-box.left-radius && pixel[0]/10000*box.width <= bounds.right-box.left+radius &&
            pixel[1]/10000*box.height >= bounds.top-box.top-radius && pixel[1]/10000*box.height <= bounds.bottom-box.top+radius) active.erased.add(mark.id);
      } else {
        const points=mark.points.map(toPixel);
        if (points.some((a,i) => distance(p,a,points[Math.max(0,i-1)]) < radius+mark.size*box.width/20000)) active.erased.add(mark.id);
      }
    }
    render(state);
  }
  function cancelStroke() { const state=active?.state; active=null; if (state) { render(state); wakeHub(); } }
  function textNote(state,at,id) {
    const previous=state.marks.find(mark => mark.id===id && mark.kind==='text');
    edit={state,at,previous}; input.value=previous?.text || '';
    dialog.querySelector('[data-note-remove]').hidden=!previous;
    dialog.showModal(); input.focus();
  }
  scope.listen(dialog.querySelector('[data-note-cancel]'),'click',() => dialog.close());
  scope.listen(dialog,'close',() => { edit=null; wakeHub(); (toolbar.hidden ? toggle : hub).focus({preventScroll:true}); });
  scope.listen(dialog.querySelector('form'),'submit',event => {
    event.preventDefault(); if (!edit || !input.value.trim()) { input.focus(); return; }
    const mark={id:crypto.randomUUID(),kind:'text',color:color.value,size:({thin:140,medium:200,thick:280})[width.value],
      x:edit.previous?.x ?? edit.at[0],y:edit.previous?.y ?? edit.at[1],text:input.value.trim()};
    change(edit.state,[mark],edit.previous ? [edit.previous.id] : []); dialog.close();
  });
  scope.listen(dialog.querySelector('[data-note-remove]'),'click',() => {
    if (edit?.previous) change(edit.state,[],[edit.previous.id]); dialog.close();
  });
  toggle.hidden=false;
  setFloating(preference.visible!==false); inkColor();
  scope.listen(toggle,'click',() => { preference.visible=toolbar.hidden; setFloating(preference.visible); savePreference(); });
  scope.listen(hub,'click',event => {
    if (suppressTap && event.detail !== 0) { suppressTap=false; return; }
    if (!options.hidden) { showOptions(false); hub.focus({preventScroll:true}); return; }
    if (orbitOpen) setMode(null);
    openOrbit(!orbitOpen);
  });
  scope.listen(hub,'pointerdown',event => {
    if (event.button || !event.isPrimary) return;
    suppressTap=false;
    drag={id:event.pointerId,x:event.clientX,y:event.clientY,moved:false};
    wakeHub();
    hub.setPointerCapture(event.pointerId);
  });
  scope.listen(window,'pointermove',event => {
    if (!drag || drag.id!==event.pointerId) return;
    if (!drag.moved && Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<6) return;
    if (!drag.moved) {
      drag.moved=true; suppressTap=true;
      toolbar.classList.add('is-dragging');
      if (orbitOpen) openOrbit(false);
    }
    const bounds=viewport();
    preference.x=clamp((event.clientX-bounds.left)/Math.max(1,bounds.right-bounds.left),0,1);
    preference.y=clamp((event.clientY-bounds.top)/Math.max(1,bounds.bottom-bounds.top),0,1);
    position();
  });
  for (const type of ['pointerup','pointercancel']) scope.listen(window,type,event => {
    if (!drag || drag.id!==event.pointerId) return;
    if (drag.moved) savePreference();
    drag=null; toolbar.classList.remove('is-dragging');
    wakeHub();
  });
  scope.listen(hub,'focus',wakeHub);
  scope.listen(window,'resize',position,{passive:true});
  scope.listen(document,'fullscreenchange',position);
  scope.listen(document,'aelms:reader-display-change',position);
  // The floating control remains movable while reading; touch strokes only
  // intercept the page after an annotation tool has been selected.
  const header=reader.querySelector('.sf-header');
  if (header && window.ResizeObserver) scope.createObserver(ResizeObserver,position).observe(header);
  scope.listen(document,'keydown',event => {
    if (event.key!=='Escape' || dialog.open || (!orbitOpen && !mode)) return;
    event.stopPropagation(); event.preventDefault();
    if (!options.hidden) { showOptions(false); optionsToggle.focus({preventScroll:true}); return; }
    setMode(null); openOrbit(false); hub.focus({preventScroll:true});
  },{capture:true});
  scope.listen(document,'click',event => {
    if (toolbar.hidden || dialog.open || toolbar.contains(event.target) || toggle.contains(event.target) || drag || active) return;
    if (orbitOpen) { setMode(null); openOrbit(false); }
    scope.clearTimeout(idleTimer);
    toolbar.classList.add('is-idle');
  });
  scope.listen(optionsToggle,'click',() => showOptions(options.hidden));
  scope.listen(toolbar.querySelector('[data-note-options-done]'),'click',() => { showOptions(false); optionsToggle.focus(); });
  scope.listen(color,'change',inkColor);
  scope.listen(toolbar.querySelector('[data-note-done]'),'click',() => { setMode(null); openOrbit(false); });
  for (const tool of toolbar.querySelectorAll('[data-note-tool]')) scope.listen(tool,'click',() => {
    const chosen=tool.dataset.noteTool;
    if (chosen!==mode && chosen!=='eraser') color.value=chosen==='highlight' ? '#e8bd32' : chosen==='text' ? '#192f4a' : '#255ec5';
    inkColor(); setMode(chosen); openOrbit(false);
  });
  scope.listen(undo,'click',() => { const action=past.pop(); if (!action) return; change(action.state,action.removed,action.add.map(mark=>mark.id),false); future.push(action); historyButtons(); });
  scope.listen(redo,'click',() => { const action=future.pop(); if (!action) return; change(action.state,action.add,action.removed.map(mark=>mark.id),false); past.push(action); historyButtons(); });
  scope.listen(reader,'pointerdown',event => { pointers.add(event.pointerId); if (pointers.size>1) cancelStroke(); },{capture:true});
  for (const name of ['pointerup','pointercancel']) scope.listen(window,name,event => pointers.delete(event.pointerId));
  for (const sheet of reader.querySelectorAll('.ce-sheet[data-sheet]')) {
    const image=sheet.querySelector('img'), source=image?.dataset.pdfSrc || image?.getAttribute('src');
    if (!source) continue;
    const url=new URL(source,location.href); if (url.origin!==location.origin) continue; url.searchParams.set('notes','1');
    const layer=document.createElement('div'); layer.className='book-note-layer'; layer.setAttribute('aria-hidden','true');
    const svg=document.createElementNS(ns,'svg'), texts=document.createElement('div'); layer.append(svg,texts); sheet.append(layer);
    const state={sheet,image,layer,svg,texts,url:url.href,marks:[],pending:new Map(),ready:false,loading:false,saving:false,visible:false,delay:2000};
    states.push(state);
    const resize=() => render(state);
    scope.listen(image,'load',resize);
    if (window.ResizeObserver) scope.createObserver(ResizeObserver,resize).observe(sheet);
    scope.listen(layer,'pointerdown',event => {
      if (toolbar.hidden || event.button || pointers.size>1) return;
      if (!state.ready) { load(state); status.textContent=toolbar.dataset.loading; return; }
      event.preventDefault(); const at=point(state,event);
      active={state,pointer:event.pointerId,at,mode,moved:false};
      wakeHub();
      layer.setPointerCapture(event.pointerId);
      if (mode==='pen' || mode==='highlight') {
        active.mark={id:crypto.randomUUID(),kind:mode,color:color.value,size:(mode==='pen' ? {thin:24,medium:42,thick:70} : {thin:150,medium:240,thick:360})[width.value],points:[at]};
      } else if (mode==='eraser') { active.erased=new Set(); eraseAt(state,at); }
      else active.textId=event.target.closest('[data-note-id]')?.dataset.noteId;
      render(state);
    });
    scope.listen(layer,'pointermove',event => {
      if (!active || active.state!==state || active.pointer!==event.pointerId || pointers.size>1) return;
      const at=point(state,event);
      if (Math.hypot(at[0]-active.at[0],at[1]-active.at[1])>40) active.moved=true;
      if (active.mark) {
        const points=active.mark.points, last=points[points.length-1];
        if (Math.hypot(at[0]-last[0],at[1]-last[1])<8) return;
        points.push(at);
        if (points.length>1000) active.mark.points=points.filter((_,i)=>i%2===0 || i===points.length-1);
        render(state);
      } else if (active.erased) eraseAt(state,at);
    });
    scope.listen(layer,'pointerup',event => {
      if (!active || active.state!==state || active.pointer!==event.pointerId) return;
      const stroke=active; active=null;
      wakeHub();
      if (pointers.size>1) { render(state); return; }
      if (stroke.mark) {
        if (stroke.mark.points.length===1) { const p=stroke.mark.points[0]; stroke.mark.points.push([Math.min(10000,p[0]+1),p[1]]); }
        change(state,[stroke.mark],[]);
      } else if (stroke.erased?.size) change(state,[],[...stroke.erased]);
      else if (stroke.mode==='text' && !stroke.moved) textNote(state,stroke.at,stroke.textId);
    });
    scope.listen(layer,'pointercancel',cancelStroke);
  }
  if (window.IntersectionObserver) {
    const observer=scope.createObserver(IntersectionObserver,entries => {
      for (const entry of entries) { const state=states.find(state=>state.sheet===entry.target); state.visible=entry.isIntersecting; if (state.visible && !state.ready) load(state); }
    },{root:reader.querySelector('.sf-scroll') || null,rootMargin:'300px'});
    states.forEach(state=>observer.observe(state.sheet));
  } else states.forEach(state=>{state.visible=true;load(state);});
  scope.setInterval(() => {
    if (document.hidden || active || dialog.open) return;
    states.filter(state=>state.visible).forEach(state=>state.pending.size ? save(state) : load(state));
  },15000);
  scope.listen(window,'online',() => states.filter(state=>state.pending.size).forEach(state=>{state.stopped=false;save(state);}));
  scope.listen(window,'storage',event => {
    if (states.some(state=>state.key===event.key) && !active) states.filter(state=>state.visible && !state.pending.size).forEach(load);
  });
  // Flush a small idempotent batch during navigation; larger queues remain account-scoped for retry.
  function flush() {
    for (const state of states) {
      if (!state.ready || !state.pending.size || state.stopped) continue;
      persist(state);
      const body=JSON.stringify({add:[...state.pending.values()].filter(Boolean),remove:[...state.pending].filter(([,mark])=>!mark).map(([id])=>id)});
      if (new Blob([body]).size<60000) fetch(state.url,{method:'POST',headers:{...headers,'Content-Type':'application/json','X-CSRFToken':csrf},body,credentials:'same-origin',keepalive:true}).catch(()=>{});
    }
  }
  scope.listen(document,'visibilitychange',() => { if (document.hidden) flush(); });
  scope.signal.addEventListener('abort',flush,{once:true});
});
