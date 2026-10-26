/* Native academic forms, enhanced within the current directory. No simulated saves. */
(function () {
  "use strict";
  const root = document.querySelector('[data-academic-workspace]');
  const dialog = document.querySelector('[data-academic-editor]');
  if (!root || !dialog?.showModal || !window.fetch || !window.DOMParser || !window.AbortController ||
      !window.aelmsRegisterForms || !window.aelmsFormHasChanges || !window.aelmsConfirm) return;
  const t = window.aelmsUI?.t || ((text) => text);
  const content = dialog.querySelector('[data-academic-editor-content]');
  const messages = dialog.querySelector('[data-academic-editor-messages]');
  const notice = dialog.querySelector('[data-academic-editor-status]');
  const recovery = dialog.querySelector('[data-academic-editor-recovery]');
  const retry = dialog.querySelector('[data-academic-editor-retry]');
  const close = dialog.querySelector('[data-academic-editor-close]');
  const heading = dialog.querySelector('h2');
  const actor = root.dataset.academicActor;
  const kind = root.dataset.academicKind;
  const sectionPath = {courses:'courses',terms:'academic-terms',levels:'levels',rooms:'rooms'}[kind];
  if (!sectionPath || !actor) return;
  const currentArea=root.querySelector('[data-academic-area][aria-current="page"]');
  if(!currentArea)return;
  const listPath = new URL(currentArea.href,location.href).pathname;
  let target = null, record = '', opener = null, controller = null, revision = 0;
  let posting = false, uncertain = false, scopeInvalid = false, confirming = false, active = true, baseline = null;
  const values = (form) => JSON.stringify(Array.from(new FormData(form)).filter(([name]) => !['csrf_token','edit_snapshot','snapshot','submit'].includes(name)));
  const release = (scope) => scope.querySelectorAll('form').forEach((form) => form.dispatchEvent(new Event('workspace:saved')));
  function status(text, error = false) {notice.textContent = t(text); notice.hidden = !text; notice.setAttribute('role',error?'alert':'status');}
  function pageNotice(text) {const n=root.querySelector('[data-academic-notice]'); n.textContent=t(text); n.hidden=!text;}
  function blocked(text) {uncertain=true; pageNotice(text);const reload=root.querySelector('[data-academic-reload]');reload.href=location.href;reload.hidden=false;recovery.querySelector('a').href=location.href;root.querySelectorAll('.ah-status-select').forEach(select=>select.disabled=true);content.querySelectorAll('[type=submit]').forEach(button=>button.disabled=true);}
  function cancelRead() {revision++; controller?.abort(); controller=null;}
  function copyMessages(destination, page) {
    const source=page.querySelector('[data-academic-messages]');
    destination.replaceChildren();
    if(source && !source.querySelector('script,style,iframe,object,embed')) destination.append(...Array.from(source.childNodes).map(n=>document.importNode(n,true)));
  }
  async function parsed(response) {
    if (!response.ok || !(response.headers.get('Content-Type')||'').includes('text/html')) throw new Error('response');
    const url=new URL(response.url);
    if(url.origin!==location.origin)throw new Error('origin');
    return {page:new DOMParser().parseFromString(await response.text(),'text/html'),url,redirected:response.redirected};
  }
  function bound(element) {
    if (!element || element.dataset.academicKind!==kind || element.dataset.academicActor!==actor) {
      scopeInvalid=true; throw new Error('scope');
    }
    if(element.querySelector('script,style,iframe,object,embed'))throw new Error('content');
    return element;
  }
  function install(loaded) {
    if(loaded.url.pathname!==target.pathname)throw new Error('destination');
    const form=bound(loaded.page.querySelector('form[data-academic-form]'));
    if(form.dataset.academicRecord!==record || form.method.toLowerCase()!=='post' || !form.querySelector('input[name=csrf_token]'))throw new Error('form');
    const declared=form.getAttribute('action');
    if(declared && new URL(declared,loaded.url).pathname!==target.pathname)throw new Error('action');
    form.action=target.href;
    const title=loaded.page.querySelector('[data-workspace-page-heading]');
    if(title)heading.textContent=title.textContent.trim();
    release(content); content.replaceChildren(document.importNode(form,true));
    copyMessages(messages,loaded.page);
    const inserted=content.querySelector('form');
    if(baseline===null || loaded.redirected)baseline=values(inserted);
    window.aelmsRegisterForms(content);
    status(''); recovery.hidden=true;
    const error=content.querySelector('.field__error');
    const field=error?.closest('.field')?.querySelector('input,select,textarea') || content.querySelector('input:not([type=hidden]):not([type=submit]),select,textarea');
    field?.focus({preventScroll:true});
  }
  async function refresh() {
    const url=new URL(location.href);
    const loaded=await parsed(await fetch(url.href,{credentials:'same-origin',cache:'no-store'}));
    if(!active || loaded.redirected || loaded.url.pathname!==listPath)throw new Error('directory');
    const incoming=bound(loaded.page.querySelector('[data-academic-workspace]'));
    const oldResults=root.querySelector('#query-results');
    const destination=oldResults || root.querySelector('.ah-directory');
    const source=oldResults ? incoming.querySelector('#query-results') : incoming.querySelector('.ah-directory');
    if(!source)throw new Error('records');
    release(destination);
    destination.replaceChildren(...Array.from(source.childNodes).map(n=>document.importNode(n,true)));
    root.querySelectorAll('[data-academic-area]').forEach(card=>{
      const next=incoming.querySelector('[data-academic-area="'+card.dataset.academicArea+'"] .ah-total');
      if(next)card.querySelector('.ah-total').replaceChildren(...Array.from(next.childNodes).map(n=>document.importNode(n,true)));
    });
    enhance();
  }
  async function load() {
    if(posting || uncertain || scopeInvalid || !dialog.open)return;
    cancelRead(); const mine=revision; controller=new AbortController();
    const signal=controller.signal;
    status('Loading form…'); recovery.hidden=true;
    try {
      const loaded=await parsed(await fetch(target.href,{credentials:'same-origin',cache:'no-store',signal}));
      if(!active || signal.aborted || mine!==revision || !dialog.open)return;
      if(loaded.redirected)throw new Error('redirect');
      install(loaded);
    } catch(error) {
      if(signal.aborted || mine!==revision || !active)return;
      if(scopeInvalid)blocked('Your session changed. Reload this page to continue.');
      status(scopeInvalid?'Your session changed. Reload this page to continue.':'The form could not load. Retry or reload this page.',true);
      recovery.hidden=false; retry.hidden=scopeInvalid;
    }
  }
  async function dismiss() {
    if(posting || confirming)return;
    const form=content.querySelector('form');
    confirming=true;
    try {
      if(form && (window.aelmsFormHasChanges(content) || baseline!==null && baseline!==values(form)) &&
          !await window.aelmsConfirm(t('Discard this academic record draft?'),t('Discard draft')))return;
      cancelRead(); release(content); content.replaceChildren(); messages.replaceChildren(); dialog.close();
    } finally {confirming=false;}
  }
  function finish() {cancelRead();release(content);content.replaceChildren();messages.replaceChildren();dialog.close();}
  root.addEventListener('click',event=>{
    const link=event.target.closest('[data-academic-editor-open]');
    if(!link || event.defaultPrevented || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)return;
    const url=new URL(link.href,location.href);
    const edit=url.pathname.startsWith(listPath+'/') && url.pathname.slice(listPath.length+1).match(/^([0-9a-f-]{36})\/edit$/);
    if(url.origin!==location.origin || (!edit && url.pathname!==listPath+'/new'))return;
    event.preventDefault();
    if(posting || dialog.open || uncertain || scopeInvalid)return;
    if(kind==='courses' && !edit){const level=root.querySelector('[name=level_id]')?.value;if(level)url.searchParams.set('level_id',level);else url.searchParams.delete('level_id');}
    target=url;record=edit?edit[1]:'';opener=link;baseline=null;
    heading.textContent=link.textContent.replace(/^\s*\+\s*/,'').trim();
    dialog.showModal();document.body.dataset.academicEditorOpen='true';load();
  });
  close.addEventListener('click',dismiss);
  retry.addEventListener('click',load);
  dialog.addEventListener('cancel',event=>{event.preventDefault();dismiss();});
  dialog.addEventListener('close',()=>{delete document.body.dataset.academicEditorOpen;opener?.isConnected&&opener.focus({preventScroll:true});});
  dialog.addEventListener('click',event=>{
    if(event.target.closest('[data-academic-editor-cancel]')){event.preventDefault();event.stopPropagation();dismiss();return;}
    if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left || event.clientX>box.right || event.clientY<box.top || event.clientY>box.bottom)dismiss();}
  });
  dialog.addEventListener('submit',async event=>{
    const form=event.target;
    if(event.defaultPrevented || !form.matches('[data-academic-form]'))return;
    event.preventDefault();event.stopPropagation();
    if(posting || uncertain || scopeInvalid || !dialog.open || form.action!==target.href)return;
    const data=new FormData(form);if(event.submitter?.name)data.append(event.submitter.name,event.submitter.value);
    posting=true;close.disabled=true;form.inert=true;status('Saving...');
    try {
      const loaded=await parsed(await fetch(target.href,{method:'POST',body:data,credentials:'same-origin',cache:'no-store'}));
      if(!active)return;
      if(loaded.page.querySelector('[data-academic-form]'))install(loaded);
      else {
        if(!loaded.redirected || loaded.url.pathname!==listPath)throw new Error('destination');
        bound(loaded.page.querySelector('[data-academic-workspace]'));
        copyMessages(root.querySelector('[data-academic-messages]'),loaded.page);
        finish();
        try{await refresh();}catch(error){blocked('The records could not be refreshed. Reload this page before continuing.');}
      }
    } catch(error) {
      if(active){blocked('Saving could not be confirmed. Reload the records before retrying.');status(scopeInvalid?'Your session changed. Reload this page to continue.':'Saving could not be confirmed. Reload the records before retrying.',true);recovery.hidden=false;retry.hidden=true;}
    } finally {posting=false;close.disabled=false;form.inert=false;}
  },true);
  function enhance() {
    root.dataset.editorEnhanced='true';
    root.querySelectorAll('[data-academic-status-apply]').forEach(button=>button.hidden=true);
    window.aelmsRegisterForms(root);
  }
  root.addEventListener('change',event=>{const form=event.target.closest('[data-academic-status-form]');if(form && event.target.matches('[name=status]'))form.requestSubmit();});
  root.addEventListener('submit',async event=>{
    const form=event.target;
    if(event.defaultPrevented || !form.matches('[data-academic-status-form]'))return;
    event.preventDefault();
    if(posting || uncertain || scopeInvalid)return;
    const url=new URL(form.action,location.href);
    if(url.origin!==location.origin || !url.pathname.startsWith(listPath+'/') || !url.pathname.endsWith('/toggle-status'))return;
    const data=new FormData(form);posting=true;root.inert=true;form.inert=true;pageNotice('Saving...');
    try {
      const loaded=await parsed(await fetch(url.href,{method:'POST',body:data,credentials:'same-origin',cache:'no-store'}));
      if(!active)return;
      if(!loaded.redirected || loaded.url.pathname!==listPath)throw new Error('destination');
      bound(loaded.page.querySelector('[data-academic-workspace]'));
      copyMessages(root.querySelector('[data-academic-messages]'),loaded.page);
      release(form);
      await refresh();pageNotice('');
    } catch(error) {if(active)blocked('Saving could not be confirmed. Reload the records before retrying.');}
    finally {posting=false;root.inert=false;form.inert=false;}
  },true);
  // Query filtering inserts fresh native status forms into the Course results.
  new MutationObserver(enhance).observe(root.querySelector('.ah-directory'),{childList:true,subtree:true});
  enhance();
  window.addEventListener('beforeunload',event=>{
    const form=content.querySelector('form');
    // Validation re-renders entered values as fresh field defaults. Retain the
    // original GET baseline for browser navigation as well as dialog dismissal.
    if(dialog.open && form && baseline!==null && values(form)!==baseline){event.preventDefault();event.returnValue='';}
  });
  window.addEventListener('pagehide',()=>{active=false;cancelRead();});
  window.addEventListener('pageshow',()=>{active=true;});
})();
