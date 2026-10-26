/* People dialogs reuse the existing signed profile and correspondence forms. */
(function () {
  'use strict';
  const dialog = document.querySelector('[data-people-action-dialog]');
  if (!dialog || !dialog.showModal || !window.fetch || !window.AbortController ||
      !window.DOMParser || !window.FormData || !window.aelmsRegisterForms ||
      !window.aelmsFormHasChanges || !window.aelmsConfirm || !Element.prototype.replaceChildren) return;
  const t = window.aelmsUI.t;
  const desk = document.querySelector('[data-people-desk]');
  const content = dialog.querySelector('[data-people-action-content]');
  const status = dialog.querySelector('[data-people-action-status]');
  const recovery = dialog.querySelector('[data-people-action-recovery]');
  const retry = dialog.querySelector('[data-people-action-retry]');
  const fallback = dialog.querySelector('[data-people-action-fallback]');
  const closeButton = dialog.querySelector('[data-people-action-close]');
  const actor = dialog.dataset.peopleActor;
  const kind = dialog.dataset.peopleKind;
  const messageNew = new URL(dialog.dataset.messageNewUrl, location.href);
  const threadTemplate = new URL(dialog.dataset.threadUrl, location.href);
  if (!desk || !actor || !['student', 'teacher'].includes(kind) ||
      messageNew.origin !== location.origin || threadTemplate.origin !== location.origin) return;
  let current = null;
  let controller = null;
  let revision = 0;
  let posting = false;
  let confirming = false;
  let uncertain = false;
  let validationDraft = false;
  let scopeInvalid = false;
  let active = true;

  const setStatus = (text, error = false) => {
    status.textContent = text ? t(text) : '';
    status.hidden = !text;
    status.setAttribute('role', error ? 'alert' : 'status');
  };
  const cancelRead = () => {
    revision += 1;
    controller?.abort();
    controller = null;
  };
  const release = () => content.querySelectorAll('form').forEach((form) => form.dispatchEvent(new Event('workspace:saved')));
  const dirty = () => validationDraft || window.aelmsFormHasChanges(content) ||
    (current?.mode === 'message' && Boolean(content.querySelector('textarea[name="body"]')?.value));
  const close = () => {
    cancelRead();
    release();
    content.replaceChildren();
    dialog.close();
  };
  const permitClose = async () => {
    if (posting || confirming) return;
    confirming = true;
    try {
      if (dirty() && !await window.aelmsConfirm(t('Discard these unsaved details?'), t('Discard draft'))) return;
      close();
    } finally { confirming = false; }
  };
  const binding = (node) => {
    if (node?.dataset.peopleActor !== actor) { scopeInvalid = true; throw new Error('scope'); }
    if (node.querySelector('script,style,iframe,object,embed')) throw new Error('unavailable');
  };
  const parsed = async (response) => {
    if (!(response.headers.get('Content-Type') || '').includes('text/html')) throw new Error('unavailable');
    const destination = new URL(response.url);
    if (destination.origin !== location.origin) throw new Error('unavailable');
    return {page: new DOMParser().parseFromString(await response.text(), 'text/html'), destination};
  };
  const threadURL = (id) => {
    if (!id || !/^[A-Za-z0-9_-]+$/.test(id)) throw new Error('unavailable');
    return threadTemplate.href.replace('__thread__', encodeURIComponent(id));
  };
  const nativeForm = (form, postURL) => {
    if (!form || form.method.toLowerCase() !== 'post' || !form.querySelector('input[name="csrf_token"]')) throw new Error('unavailable');
    const declared = form.getAttribute('action');
    if (declared && new URL(declared, postURL).href !== postURL) throw new Error('unavailable');
    form.action = postURL;
    current.postURL = postURL;
  };
  const editForm = (loaded) => {
    if (loaded.destination.href !== current.getURL) throw new Error('unavailable');
    const form = loaded.page.querySelector('[data-people-edit-form]');
    if (!form || form.dataset.personId !== current.id || form.dataset.peopleKind !== kind ||
        !form.querySelector('input[name="account_snapshot"]')) throw new Error('unavailable');
    binding(form);
    nativeForm(form, current.getURL);
    return form;
  };
  const messagePanel = (loaded) => {
    const panel = loaded.page.querySelector('[data-people-message-panel]');
    if (!panel) throw new Error('unavailable');
    binding(panel);
    const form = panel.querySelector('form[data-people-message-form]');
    if (panel.dataset.messageMode === 'compose') {
      if (loaded.destination.href !== current.getURL && loaded.destination.href !== messageNew.href) throw new Error('unavailable');
      if (current.thread || panel.dataset.personId !== current.id ||
          form?.querySelector('[name="recipient"]')?.value !== current.id) throw new Error('unavailable');
      nativeForm(form, messageNew.href);
    } else if (panel.dataset.messageMode === 'reply') {
      const id = panel.dataset.threadId;
      const url = threadURL(id);
      const validationURL = current.thread === id && loaded.destination.href === current.postURL;
      if ((loaded.destination.href !== url && !validationURL) || (current.thread && current.thread !== id)) throw new Error('unavailable');
      current.thread = id;
      if (form) nativeForm(form, url + '/reply');
    } else throw new Error('unavailable');
    if (form && !form.querySelector('input[name="message_state"]')) throw new Error('unavailable');
    return panel;
  };
  const install = (source) => {
    release();
    const node = document.importNode(source, true);
    // Prefix presentation IDs; field names, signed values and POSTs stay native.
    const ids = new Map();
    [node, ...node.querySelectorAll('[id]')].forEach((field) => {
      if (!field.id) return;
      ids.set(field.id, 'people-action-' + field.id);
      field.id = ids.get(field.id);
    });
    node.querySelectorAll('[for],[aria-describedby],[aria-labelledby],[aria-controls]').forEach((field) => {
      ['for', 'aria-describedby', 'aria-labelledby', 'aria-controls'].forEach((name) => {
        if (field.hasAttribute(name)) field.setAttribute(name, field.getAttribute(name).split(' ').map((id) => ids.get(id) || id).join(' '));
      });
    });
    // The compact composer uses the native text/send controls, without loading
    // the full Messages page's optional emoji UI and event handlers.
    node.querySelectorAll('[data-form-ui],[data-emoji-toggle]').forEach((field) => field.remove());
    content.replaceChildren(node);
    if (current.mode === 'message') {
      const note = document.createElement('p');
      note.className = 'pf-action-note';
      note.textContent = t('Only the two participants can access this conversation.');
      content.prepend(note);
    }
    window.aelmsRegisterForms(content);
    setStatus('');
    recovery.hidden = true;
    const error = content.querySelector('.field__error,[role="alert"]');
    const field = error?.closest('.field')?.querySelector('input,textarea') ||
      content.querySelector('[aria-invalid="true"],input:not([type="hidden"]):not([type="submit"]),textarea');
    field?.focus({preventScroll: true});
    error?.scrollIntoView({block: 'nearest'});
  };
  const load = async () => {
    if (!current || posting || scopeInvalid || !dialog.open) return;
    cancelRead();
    const mine = revision;
    const request = new AbortController();
    controller = request;
    recovery.hidden = true;
    dialog.setAttribute('aria-busy', 'true');
    setStatus('Loading form…');
    try {
      const response = await fetch(current.getURL, {credentials:'same-origin', cache:'no-store', signal:request.signal, headers:{'Accept':'text/html'}});
      const loaded = await parsed(response);
      if (mine !== revision || request.signal.aborted || !active || !dialog.open) return;
      if (!response.ok || (current.mode === 'edit' && response.redirected)) throw new Error('unavailable');
      if (current.mode === 'message' && loaded.destination.href !== current.getURL && !response.redirected) throw new Error('unavailable');
      install(current.mode === 'edit' ? editForm(loaded) : messagePanel(loaded));
    } catch (error) {
      if (mine !== revision || request.signal.aborted || !active || !dialog.open) return;
      setStatus(scopeInvalid ? 'Your session changed. Reload this page to continue.' : 'The form could not load. Retry or open the full page.', true);
      recovery.hidden = false;
      retry.hidden = scopeInvalid;
    } finally { if (mine === revision) { controller = null; dialog.removeAttribute('aria-busy'); } }
  };
  desk.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey || scopeInvalid) return;
    const link = event.target.closest('a[data-people-action]');
    const profile = link?.closest('[data-people-profile]');
    if (!profile || !['edit', 'message'].includes(link.dataset.peopleAction) || dialog.open) return;
    if (profile.dataset.peopleActor !== actor || profile.dataset.peopleKind !== kind || !profile.dataset.personId) return;
    const url = new URL(link.href, location.href);
    const directory = new URL(desk.querySelector('[data-query-form]').action || location.href);
    const expected = link.dataset.peopleAction === 'edit' ?
      directory.origin + directory.pathname.replace(/\/$/, '') + '/' + encodeURIComponent(profile.dataset.personId) + '/edit' :
      messageNew.href + '?to=' + encodeURIComponent(profile.dataset.personId);
    if (url.href !== expected) return;
    event.preventDefault();
    current = {mode:link.dataset.peopleAction, id:profile.dataset.personId, getURL:url.href, opener:link, thread:null, postURL:null};
    uncertain = false;
    validationDraft = false;
    retry.hidden = false;
    fallback.href = url.href;
    fallback.textContent = t('Open full page');
    dialog.querySelector('[data-people-action-title]').textContent = t(current.mode === 'edit' ? 'Edit profile' : 'Message');
    dialog.querySelector('[data-people-action-person]').textContent = profile.querySelector('[data-people-profile-heading]').textContent;
    dialog.showModal();
    document.body.dataset.peopleActionOpen = 'true';
    load();
  });
  closeButton.addEventListener('click', permitClose);
  retry.addEventListener('click', load);
  dialog.addEventListener('input', (event) => {
    if (!event.target.matches('textarea[name="body"]')) return;
    const count = content.querySelector('[data-message-count]');
    if (count) count.textContent = event.target.value.length + ' / ' + event.target.maxLength;
  });
  dialog.addEventListener('cancel', (event) => {event.preventDefault(); permitClose();});
  dialog.addEventListener('click', (event) => {
    if (event.target.closest('[data-people-action-cancel]')) { event.preventDefault(); event.stopPropagation(); permitClose(); return; }
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) permitClose();
  });
  dialog.addEventListener('close', () => {
    cancelRead();
    delete document.body.dataset.peopleActionOpen;
    if (current?.opener.isConnected) current.opener.focus({preventScroll:true});
  });
  dialog.addEventListener('submit', async (event) => {
    const form = event.target;
    if (event.defaultPrevented || !form.matches('[data-people-edit-form],[data-people-message-form]')) return;
    event.preventDefault();
    if (posting || uncertain || scopeInvalid || !active || !dialog.open || form.action !== current?.postURL) return;
    const data = new FormData(form);
    if (event.submitter?.name) data.append(event.submitter.name, event.submitter.value);
    posting = true;
    validationDraft = true;
    closeButton.disabled = true;
    const buttons = Array.from(form.querySelectorAll('[type="submit"]'));
    buttons.forEach((button) => {button.disabled = true;});
    form.setAttribute('aria-busy', 'true');
    setStatus(current.mode === 'edit' ? 'Saving…' : 'Sending…');
    try {
      const response = await fetch(current.postURL, {method:'POST', body:data, credentials:'same-origin', cache:'no-store', headers:{'Accept':'text/html'}});
      const loaded = await parsed(response);
      if (!active || !dialog.open) return;
      if (!response.redirected && [200,400,422].includes(response.status) && loaded.destination.href === current.postURL) {
        install(current.mode === 'edit' ? editForm(loaded) : messagePanel(loaded));
        return;
      }
      if (!response.ok || !response.redirected) throw new Error('unavailable');
      if (current.mode === 'edit') {
        const profile = loaded.page.querySelector('[data-people-profile]');
        if (!profile) throw new Error('unavailable');
        binding(profile);
        if (profile.dataset.personId !== current.id || profile.dataset.peopleKind !== kind ||
            loaded.destination.href !== current.getURL.replace(/\/edit$/, '')) throw new Error('unavailable');
        const query = desk.querySelector('[data-query-form]');
        const id = current.id;
        close();
        if (query.dataset.queryRefreshReady === 'true') {
          desk.dataset.peopleSelect = id;
          query.dispatchEvent(new Event('aelms:query-refresh', {cancelable:true}));
        } else location.reload();
      } else {
        const panel = messagePanel(loaded);
        if (panel.dataset.messageMode !== 'reply' || panel.dataset.messageSent !== 'true' || panel.dataset.messageBlocked !== 'false') throw new Error('unavailable');
        close();
      }
      const notice = desk.querySelector('[data-people-action-feedback]');
      notice.textContent = t(current.mode === 'edit' ? 'Account updated.' : 'Message sent.');
      notice.hidden = false;
    } catch (error) {
      if (!active || !dialog.open) return;
      uncertain = true;
      setStatus(scopeInvalid ? 'Your session changed. Reload this page to continue.' : 'The result could not be confirmed. Your details remain here. Review the full page before trying again.', true);
      retry.hidden = true;
      recovery.hidden = false;
    } finally {
      posting = false;
      closeButton.disabled = false;
      form.removeAttribute('aria-busy');
      buttons.forEach((button) => {button.disabled = uncertain || scopeInvalid;});
    }
  });
  window.addEventListener('pagehide', () => {active = false; cancelRead(); delete document.body.dataset.peopleActionOpen;});
  window.addEventListener('pageshow', (event) => {
    active = true;
    if (event.persisted && dialog.open) {
      document.body.dataset.peopleActionOpen = 'true';
      uncertain = true;
      content.querySelectorAll('[type="submit"]').forEach((button) => {button.disabled = true;});
      setStatus('Reload this page before submitting the form.', true);
    }
  });
})();
