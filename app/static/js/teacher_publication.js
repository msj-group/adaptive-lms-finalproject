/* One authoritative save at a time; no automatic POST retry or confirmation dialog. */
window.aelmsModules.register(document.currentScript, function (scope) {
  'use strict';
  const root = document.querySelector('[data-publication-workspace]');
  if (!root) return;
  const t = window.aelmsUI.t;
  root.dataset.publicationEnhanced = 'true';
  let busy = false, uncertain = false;
  const form = () => root.querySelector('[data-publication-form]');
  const units = () => [...root.querySelectorAll('[data-publication-unit]')];
  function feedback(text, error = false, reload = false) {
    const node = root.querySelector('[data-publication-feedback]');
    node.replaceChildren(document.createTextNode(t(text)));
    node.classList.toggle('is-error', error); node.hidden = !text;
    if (reload) { const button = document.createElement('button'); button.type = 'button'; button.className = 'pub-text-button'; button.textContent = t('Refresh'); button.dataset.publicationReload = ''; node.append(button); }
  }
  function expand(unit, open) { unit.querySelector('[data-unit-lessons]').hidden = !open; unit.querySelector('[data-unit-disclosure]').setAttribute('aria-expanded',String(open)); }
  function selection() {
    const count = root.querySelectorAll('.pub-checkbox:checked').length;
    const label = root.querySelector('[data-selection-count]'); if (label) label.textContent = count;
    root.querySelectorAll('[data-selection-action]').forEach(button => { button.disabled = busy || uncertain || count === 0; });
  }
  function search() {
    const query = root.querySelector('[data-publication-search]').value.trim().toLocaleLowerCase();
    let visible = 0;
    units().forEach(unit => {
      const match = unit.dataset.unitTitle.toLocaleLowerCase().includes(query);
      let lessons = 0;
      unit.querySelectorAll('[data-publication-lesson]').forEach(lesson => { lesson.hidden = !match && !lesson.dataset.searchTitle.toLocaleLowerCase().includes(query); if (!lesson.hidden) lessons++; });
      unit.hidden = lessons === 0; if (!unit.hidden) visible++;
      if (query && !unit.hidden) expand(unit,true);
    });
    root.querySelector('[data-search-empty]').hidden = visible !== 0 || units().length === 0;
  }
  function prepare(open = null) {
    units().forEach((unit,index) => expand(unit,open ? open.has(unit.dataset.publicationUnit) : index === 0));
    selection(); search();
  }
  function lock(value) { busy = value; form().setAttribute('aria-busy',String(value)); form().querySelectorAll('button,.pub-checkbox').forEach(control => { control.disabled = value || uncertain; }); selection(); }
  function remember() {
    return {open:new Set(units().filter(unit => !unit.querySelector('[data-unit-lessons]').hidden).map(unit => unit.dataset.publicationUnit)),
      search:root.querySelector('[data-publication-search]').value, selected:[...root.querySelectorAll('.pub-checkbox:checked')].map(input => [input.name,input.value]),
      focus:document.activeElement?.closest('[name=command]')?.value};
  }
  async function refresh(data = null) {
    if (busy) return;
    const state = remember(); lock(true); feedback(data ? 'Saving…' : 'Refreshing…');
    try {
      const response = await scope.fetch(form().action,{method:data ? 'POST':'GET',body:data,credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json','X-Publication-Request':'1'}});
      if (![200,409].includes(response.status) || !(response.headers.get('Content-Type') || '').includes('application/json')) throw new Error('unavailable');
      const result = await response.json();
      const page = new DOMParser().parseFromString(result.html,'text/html');
      const panel = page.querySelector('[data-publication-panel]'), old = root.querySelector('[data-publication-panel]');
      if (!panel || panel.dataset.publicationActor !== root.dataset.publicationActor ||
          panel.dataset.publicationGroup !== old.dataset.publicationGroup || panel.dataset.publicationBook !== root.dataset.publicationBook || panel.querySelector('script,style,iframe,object')) throw new Error('unavailable');
      old.replaceWith(document.importNode(panel,true)); uncertain = false;
      root.querySelector('[data-publication-search]').value = state.search;
      const bulk = data?.get('command')?.split('|')[1] === 'selected';
      if (!bulk || !result.ok) state.selected.forEach(([name,value]) => { for (const input of root.querySelectorAll('.pub-checkbox')) if (input.name === name && input.value === value) input.checked = true; });
      prepare(state.open); feedback(result.message || (result.ok ? 'Saved' : 'The publication could not be saved.'),!result.ok);
      if (state.focus) {
        const buttons = [...root.querySelectorAll('[name=command]')];
        const focus = buttons.find(button => button.value === state.focus) || buttons.find(button =>
          button.matches('.pub-switch,.pub-unit-toggle') && button.value.split('|').slice(1).join('|') === state.focus.split('|').slice(1).join('|'));
        focus?.focus({preventScroll:true});
      }
    } catch (_) {
      uncertain = uncertain || Boolean(data);
      feedback(data ? 'Connection interrupted. Refresh to check the current visibility.' : 'Could not refresh. Try again.',true,true);
    } finally { lock(false); }
  }
  scope.listen(root,'submit',event => {
    if (!event.target.matches('[data-publication-form]')) return;
    event.preventDefault(); if (busy || uncertain || !event.submitter?.name) return;
    if (event.submitter.value.startsWith('schedule|') && !event.target.querySelector('[data-publication-date]').reportValidity()) return;
    const data = new FormData(event.target); data.set(event.submitter.name,event.submitter.value);
    refresh(data);
  });
  scope.listen(root,'input',event => { if (event.target.matches('[data-publication-search]')) search(); });
  scope.listen(root,'change',event => { if (event.target.matches('.pub-checkbox')) selection(); });
  scope.listen(root,'click',event => {
    const button = event.target.closest('button'); if (!button) return;
    if (button.matches('[data-publication-reload]')) { refresh(); return; }
    if (button.matches('[data-unit-disclosure]')) { const unit = button.closest('[data-publication-unit]'); expand(unit,unit.querySelector('[data-unit-lessons]').hidden); }
    if (button.matches('[data-expand-units]')) {
      const open = units().some(unit => unit.querySelector('[data-unit-lessons]').hidden);
      units().forEach(unit => expand(unit,open)); button.textContent = t(open ? 'Collapse all':'Expand all');
    }
    if (button.matches('[data-schedule-target]')) {
      const panel = root.querySelector('[data-publication-scheduler]');
      panel.hidden = false; panel.querySelector('[data-schedule-context]').textContent = button.dataset.scheduleLabel;
      panel.querySelector('[data-schedule-submit]').value = 'schedule|' + button.dataset.scheduleTarget;
      const input = panel.querySelector('[data-publication-date]'); input.disabled = false; input.required = true; input.focus();
    }
    if (button.matches('[data-close-schedule]')) { const panel = root.querySelector('[data-publication-scheduler]'); panel.hidden = true; panel.querySelector('[data-publication-date]').disabled = true; }
  });
  scope.listen(root,'keydown',event => { if (event.key === 'Escape') { const close = root.querySelector('[data-publication-scheduler]:not([hidden]) [data-close-schedule]'); if (close) { close.click(); event.stopPropagation(); } } });
  prepare();
});
