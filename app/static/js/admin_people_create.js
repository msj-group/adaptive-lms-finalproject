/* Centered account creation using the original signed forms and native POSTs. */
(function () {
  'use strict';
  const dialog = document.querySelector('[data-people-create-dialog]');
  if (!dialog || !dialog.showModal || !window.fetch || !window.AbortController ||
      !window.DOMParser || !window.FormData || !window.aelmsRegisterForms || !window.aelmsFormHasChanges ||
      !window.aelmsConfirm || !Element.prototype.replaceChildren) return;
  const t = window.aelmsUI.t;
  const content = dialog.querySelector('[data-people-create-content]');
  const status = dialog.querySelector('[data-people-create-status]');
  const recovery = dialog.querySelector('[data-people-create-recovery]');
  const retry = dialog.querySelector('[data-people-create-retry]');
  const fallback = dialog.querySelector('[data-people-create-fallback]');
  const closeButton = dialog.querySelector('[data-people-create-close]');
  const createURL = new URL(dialog.dataset.createUrl, location.href);
  const actor = dialog.dataset.peopleActor;
  const kind = dialog.dataset.peopleKind;
  if (createURL.origin !== location.origin || !actor || !['student', 'teacher'].includes(kind)) return;
  let request = null;
  let revision = 0;
  let posting = false;
  let confirming = false;
  let uncertain = false;
  let scopeInvalid = false;
  let active = true;
  let opener = null;

  const setStatus = (text, error = false) => {
    status.textContent = text ? t(text) : '';
    status.hidden = !text;
    status.setAttribute('role', error ? 'alert' : 'status');
  };
  const cancelRead = () => {
    revision += 1;
    if (request) request.abort();
    request = null;
  };
  const releaseForms = () => {
    content.querySelectorAll('form').forEach((form) => form.dispatchEvent(new Event('workspace:saved')));
  };
  const hasDraft = () => window.aelmsFormHasChanges(content) ||
    Array.from(content.querySelectorAll('input:not([type="hidden"]):not([type="submit"]),textarea'))
      .some((field) => field.value.length > 0);
  const permitClose = async () => {
    if (posting || confirming) return;
    confirming = true;
    try {
      if (hasDraft() && !await window.aelmsConfirm(t('Discard the entered account details?'), t('Discard draft'))) return;
      cancelRead();
      releaseForms();
      content.replaceChildren();
      dialog.close();
    } finally { confirming = false; }
  };
  const parsed = async (response) => {
    if (!(response.headers.get('Content-Type') || '').includes('text/html')) throw new Error('unavailable');
    const destination = new URL(response.url);
    if (destination.origin !== createURL.origin) throw new Error('unavailable');
    return {page: new DOMParser().parseFromString(await response.text(), 'text/html'), destination};
  };
  const creationForm = (loaded) => {
    if (loaded.destination.href !== createURL.href) throw new Error('unavailable');
    const form = loaded.page.querySelector('form[data-people-create-form]');
    if (!form || form.dataset.peopleKind !== kind || form.method.toLowerCase() !== 'post' ||
        form.querySelector('script,style,iframe,object,embed') ||
        !form.querySelector('input[name="csrf_token"]')) throw new Error('unavailable');
    if (form.dataset.peopleActor !== actor) {scopeInvalid = true; throw new Error('scope');}
    const declared = form.getAttribute('action');
    if (declared && new URL(declared, loaded.destination).href !== createURL.href) throw new Error('unavailable');
    // An omitted action belongs to the fetched creation page, not the directory.
    form.action = createURL.href;
    return form;
  };
  const install = (form) => {
    releaseForms();
    content.replaceChildren(document.importNode(form, true));
    window.aelmsRegisterForms(content);
    setStatus('');
    recovery.hidden = true;
    const firstError = content.querySelector('.field__error');
    const field = firstError?.closest('.field')?.querySelector('input') || content.querySelector('input:not([type="hidden"]):not([type="submit"])');
    field?.focus({preventScroll: true});
    firstError?.scrollIntoView({block: 'nearest'});
  };
  const loadForm = async () => {
    if (posting || scopeInvalid || !dialog.open) return;
    cancelRead();
    const mine = revision;
    const controller = new AbortController();
    request = controller;
    uncertain = false;
    recovery.hidden = true;
    fallback.href = createURL.href;
    fallback.textContent = t('Open full page');
    dialog.setAttribute('aria-busy', 'true');
    setStatus('Loading account form…');
    try {
      const response = await fetch(createURL.href, {credentials: 'same-origin', cache: 'no-store',
        signal: controller.signal, headers: {'Accept': 'text/html'}});
      if (!response.ok || response.redirected) throw new Error('unavailable');
      const loaded = await parsed(response);
      if (mine !== revision || controller.signal.aborted || !dialog.open || !active) return;
      install(creationForm(loaded));
    } catch (error) {
      if (mine !== revision || controller.signal.aborted || !dialog.open || !active) return;
      setStatus(scopeInvalid ? 'Your session changed. Reload this page to continue.' :
        'The account form could not load. Retry or open the full page.', true);
      recovery.hidden = false;
      retry.hidden = scopeInvalid;
    } finally {
      if (mine === revision) {request = null; dialog.removeAttribute('aria-busy');}
    }
  };
  document.querySelectorAll('[data-people-create-open]').forEach((link) => {
    if (new URL(link.href, location.href).href !== createURL.href) return;
    link.addEventListener('click', (event) => {
      if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey || scopeInvalid) return;
      event.preventDefault();
      if (dialog.open) return;
      opener = link;
      dialog.showModal();
      document.body.dataset.peopleCreateOpen = 'true';
      loadForm();
    });
  });
  closeButton.addEventListener('click', permitClose);
  retry.addEventListener('click', loadForm);
  dialog.addEventListener('cancel', (event) => {event.preventDefault(); permitClose();});
  dialog.addEventListener('click', (event) => {
    if (event.target.closest('[data-people-create-cancel]')) {
      event.preventDefault(); event.stopPropagation(); permitClose(); return;
    }
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) permitClose();
  });
  dialog.addEventListener('close', () => {
    cancelRead();
    delete document.body.dataset.peopleCreateOpen;
    if (opener?.isConnected) opener.focus({preventScroll: true});
  });
  dialog.addEventListener('submit', async (event) => {
    const form = event.target;
    if (event.defaultPrevented || !form.matches('form[data-people-create-form]')) return;
    event.preventDefault();
    if (posting || uncertain || scopeInvalid || !active || !dialog.open || form.action !== createURL.href) return;
    // Capture the native payload before disabling any controls.
    const data = new FormData(form);
    if (event.submitter?.name) data.append(event.submitter.name, event.submitter.value);
    posting = true;
    closeButton.disabled = true;
    const buttons = Array.from(form.querySelectorAll('[type="submit"]'));
    buttons.forEach((button) => {button.disabled = true;});
    form.setAttribute('aria-busy', 'true');
    recovery.hidden = true;
    setStatus('Creating account…');
    try {
      const response = await fetch(createURL.href, {method: 'POST', body: data, credentials: 'same-origin',
        cache: 'no-store', headers: {'Accept': 'text/html'}});
      const loaded = await parsed(response);
      if (!active || !dialog.open) return;
      if (!response.redirected && loaded.destination.href === createURL.href && [200, 400, 422].includes(response.status)) {
        install(creationForm(loaded));
      } else if (response.ok && response.redirected) {
        const profile = loaded.page.querySelector('[data-people-profile]');
        if (profile?.dataset.peopleActor !== actor) {scopeInvalid = true; throw new Error('scope');}
        const id = profile?.dataset.personId;
        const prefix = createURL.pathname.replace(/\/new$/, '');
        if (!profile || profile.dataset.peopleKind !== kind || !id ||
            loaded.destination.pathname !== prefix + '/' + encodeURIComponent(id) || loaded.destination.search || loaded.destination.hash) throw new Error('unavailable');
        // The native redirect and matching authorized profile confirm creation.
        // Refresh this same directory address, retaining its search and filters.
        releaseForms();
        content.replaceChildren();
        dialog.close();
        location.reload();
      } else throw new Error('unavailable');
    } catch (error) {
      if (!active || !dialog.open) return;
      uncertain = true;
      setStatus(scopeInvalid ? 'Your session changed. Reload this page to continue.' :
        'The result could not be confirmed. Your details remain here. Check the directory before trying again.', true);
      fallback.href = location.href;
      fallback.textContent = t('Check directory');
      retry.hidden = true;
      recovery.hidden = false;
    } finally {
      posting = false;
      closeButton.disabled = false;
      form.removeAttribute('aria-busy');
      buttons.forEach((button) => {button.disabled = uncertain || scopeInvalid;});
    }
  });
  window.addEventListener('pagehide', () => {active = false; cancelRead(); delete document.body.dataset.peopleCreateOpen;});
  window.addEventListener('pageshow', (event) => {
    active = true;
    if (event.persisted && dialog.open) {
      document.body.dataset.peopleCreateOpen = 'true';
      uncertain = true;
      content.querySelectorAll('[type="submit"]').forEach((button) => {button.disabled = true;});
      setStatus('Reload this page before submitting the account form.', true);
    }
  });
})();
