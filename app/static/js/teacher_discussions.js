/* Server-rendered, scoped enhancements. Protected writes keep their native handlers. */
(() => {
  'use strict';
  const t = window.aelmsUI ? window.aelmsUI.t : (text) => text;
  const registerForms = window.aelmsRegisterForms;
  const hasChanges = window.aelmsFormHasChanges;
  const confirmAction = window.aelmsConfirm;
  const releaseForms = (root) => root.querySelectorAll('form').forEach((form) => {
    form.dispatchEvent(new Event('workspace:saved'));
  });
  document.querySelectorAll('[data-teacher-discussions]').forEach((workspace) => {
    const picker = workspace.querySelector('[data-tdisc-picker]');
    const groupForm = workspace.querySelector('[data-tdisc-group-form]');
    const select = groupForm && groupForm.querySelector('[data-tdisc-group-select]');
    const apply = groupForm && groupForm.querySelector('[data-tdisc-group-apply]');
    if (select && apply && typeof groupForm.requestSubmit === 'function') {
      select.addEventListener('change', () => groupForm.requestSubmit());
      apply.hidden = true;
    }
    if (picker && 'open' in picker) {
      const summary = picker.querySelector('summary');
      const options = Array.from(picker.querySelectorAll('[data-tdisc-group-option]'));
      picker.hidden = false;
      workspace.classList.add('is-enhanced');
      picker.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          picker.open = false;
          summary.focus();
          return;
        }
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || !options.length) return;
        event.preventDefault();
        picker.open = true;
        const current = options.indexOf(document.activeElement);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
          : event.key === 'ArrowDown' ? (current + 1) % options.length
            : (current - 1 + options.length) % options.length;
        options[next].focus();
      });
      document.addEventListener('pointerdown', (event) => {
        if (picker.open && !picker.contains(event.target)) picker.open = false;
      });
    }

    const dialog = workspace.querySelector('[data-tdisc-dialog]');
    if (!dialog || !dialog.showModal || !window.fetch || !window.AbortController ||
        !window.DOMParser || !registerForms || !hasChanges || !confirmAction) return;
    const actor = workspace.dataset.tdiscActor;
    const group = workspace.dataset.tdiscGroup;
    const groupURL = new URL(workspace.dataset.tdiscGroupUrl, location.origin);
    const newURL = new URL(workspace.dataset.tdiscNewUrl, location.origin);
    const content = dialog.querySelector('[data-tdisc-dialog-content]');
    const dialogStatus = dialog.querySelector('[data-tdisc-dialog-status]');
    const readerStatus = workspace.querySelector('[data-tdisc-reader-status]');
    const recovery = dialog.querySelector('[data-tdisc-dialog-recovery]');
    const closeButton = dialog.querySelector('[data-tdisc-close]');
    let readerController = null;
    let dialogController = null;
    let readerRevision = 0;
    let dialogRevision = 0;
    let posting = false;
    let scopeInvalid = false;
    let confirming = false;
    let trigger = null;
    let active = true;

    const scopedURL = (value) => {
      const url = new URL(value, location.href);
      return url.origin === location.origin &&
        (url.pathname === groupURL.pathname || url.pathname.startsWith(groupURL.pathname + '/')) ? url : null;
    };
    const setStatus = (element, message, error = false) => {
      element.textContent = message ? t(message) : '';
      element.hidden = !message;
      element.setAttribute('role', error ? 'alert' : 'status');
    };
    const cancelReader = () => {
      readerRevision += 1;
      if (readerController) readerController.abort();
      readerController = null;
      workspace.classList.remove('tdisc-loading');
      workspace.removeAttribute('aria-busy');
    };
    const cancelDialog = () => {
      dialogRevision += 1;
      if (dialogController) dialogController.abort();
      dialogController = null;
    };
    const parsedPage = async (response, requested, expectedTopic = null) => {
      if (!response.ok || !response.headers.get('Content-Type')?.includes('text/html')) {
        throw new Error('unavailable');
      }
      const finalURL = scopedURL(response.url);
      if (!finalURL) throw new Error('unavailable');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const root = page.querySelector('[data-teacher-discussions]');
      if (!root) throw new Error('unavailable');
      if (root.dataset.tdiscActor !== actor || root.dataset.tdiscGroup !== group) {
        scopeInvalid = true;
        throw new Error('scope');
      }
      if (expectedTopic !== null && (root.dataset.tdiscTopic || '') !== expectedTopic) throw new Error('unavailable');
      if (requested && finalURL.pathname !== requested.pathname) throw new Error('unavailable');
      return {page, root, url: finalURL};
    };
    const permissionToReplace = async (root) => {
      if (!hasChanges(root)) return true;
      if (confirming) return false;
      confirming = true;
      try {
        return await confirmAction(t('You have unsaved work. Leave this page and discard it?'), t('Continue'));
      } finally {
        confirming = false;
      }
    };
    const installLayout = (loaded, address) => {
      const old = workspace.querySelector('.tdisc-layout');
      const incoming = loaded.root.querySelector('.tdisc-layout');
      if (!old || !incoming) throw new Error('unavailable');
      const next = document.importNode(incoming, true);
      releaseForms(old);
      old.replaceWith(next);
      registerForms(next);
      const notices = workspace.querySelector('[data-tdisc-notices]');
      const incomingNotices = loaded.root.querySelector('[data-tdisc-notices]');
      if (notices && incomingNotices) notices.replaceChildren(...Array.from(incomingNotices.children, (node) => document.importNode(node, true)));
      const topic = loaded.root.dataset.tdiscTopic || '';
      workspace.dataset.tdiscTopic = topic;
      workspace.closest('.tdisc-content')?.classList.toggle('tdisc-content-reading', Boolean(topic));
      document.title = loaded.page.title;
      if (history.replaceState) history.replaceState(history.state, '', address.href);
      const reader = next.querySelector('.tdisc-reader');
      if (reader) {
        reader.classList.add('tdisc-reading-enter');
        reader.addEventListener('animationend', () => reader.classList.remove('tdisc-reading-enter'), {once: true});
        reader.querySelector('.tdisc-back')?.focus({preventScroll: true});
      } else {
        next.querySelector('.tdisc-topic')?.focus({preventScroll: true});
      }
      if (address.hash) {
        let anchor;
        try { anchor = document.getElementById(decodeURIComponent(address.hash.slice(1))); } catch (_) { anchor = null; }
        if (anchor && next.contains(anchor)) anchor.scrollIntoView({block: 'start'});
      }
      setStatus(readerStatus, '');
    };
    const loadReader = async (url) => {
      if (scopeInvalid || confirming) return;
      cancelReader();
      const revision = readerRevision;
      const controller = new AbortController();
      readerController = controller;
      const current = workspace.querySelector('.tdisc-layout');
      if (!current) return;
      if (!await permissionToReplace(current) || revision !== readerRevision || !active) return;
      const draftSnapshot = () => Array.from(current.querySelectorAll('textarea,input:not([type="hidden"]),select')).map((field) => field.value).join('\u001f');
      const acceptedDraft = draftSnapshot();
      workspace.classList.add('tdisc-loading');
      workspace.setAttribute('aria-busy', 'true');
      setStatus(readerStatus, 'Loading discussion…');
      try {
        const response = await fetch(url.href, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
        const expected = url.pathname === groupURL.pathname ? '' : url.pathname.split('/').pop();
        const loaded = await parsedPage(response, url, expected);
        if (revision !== readerRevision || controller.signal.aborted || !active) return;
        // A reply can be edited while the GET is in flight; confirm that draft too.
        if (draftSnapshot() !== acceptedDraft && !await permissionToReplace(current)) return;
        if (revision !== readerRevision || !active) return;
        installLayout(loaded, url);
      } catch (error) {
        if (revision === readerRevision && !controller.signal.aborted && active) {
          setStatus(readerStatus, scopeInvalid
            ? 'Your session changed. Reload this page to continue.'
            : 'The discussion could not load. Your current draft is still here. Try again.', true);
        }
      } finally {
        if (revision === readerRevision) {
          readerController = null;
          workspace.classList.remove('tdisc-loading');
          workspace.removeAttribute('aria-busy');
        }
      }
    };
    const focusForm = () => {
      (content.querySelector('[aria-invalid="true"]') || content.querySelector('input[name="title"]'))?.focus();
    };
    const loadCreateForm = async () => {
      if (scopeInvalid || posting) return;
      cancelDialog();
      const revision = dialogRevision;
      const controller = new AbortController();
      dialogController = controller;
      setStatus(dialogStatus, 'Loading form…');
      recovery.hidden = true;
      try {
        const response = await fetch(newURL.href, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
        const loaded = await parsedPage(response, newURL);
        if (revision !== dialogRevision || controller.signal.aborted || !dialog.open || !active) return;
        const panel = loaded.root.querySelector('.tdisc-new-card');
        if (!panel || !panel.querySelector('form[method="post"] [name="topic_state"]')) throw new Error('unavailable');
        content.replaceChildren(document.importNode(panel, true));
        registerForms(content);
        setStatus(dialogStatus, '');
        focusForm();
      } catch (error) {
        if (revision === dialogRevision && !controller.signal.aborted && dialog.open && active) {
          setStatus(dialogStatus, scopeInvalid
            ? 'Your session changed. Reload this page to continue.'
            : 'The form could not load. Try again.', true);
          recovery.hidden = scopeInvalid;
        }
      } finally {
        if (revision === dialogRevision) dialogController = null;
      }
    };
    const openDialog = (link) => {
      if (scopeInvalid) return;
      trigger = link;
      if (dialog.open) return;
      dialog.showModal();
      if (!content.querySelector('form')) loadCreateForm();
      else focusForm();
    };
    const closeDialog = async () => {
      if (posting || confirming) return;
      const draft = Array.from(content.querySelectorAll('[name="title"],[name="body"]')).some((field) => field.value.trim());
      if (draft) {
        confirming = true;
        let accepted;
        try { accepted = await confirmAction(t('Discard this topic draft?'), t('Discard draft')); }
        finally { confirming = false; }
        if (!accepted) return;
      }
      cancelDialog();
      releaseForms(content);
      content.replaceChildren();
      setStatus(dialogStatus, '');
      recovery.hidden = true;
      dialog.close();
      if (trigger?.isConnected) trigger.focus();
    };
    dialog.addEventListener('cancel', (event) => { event.preventDefault(); closeDialog(); });
    dialog.addEventListener('click', (event) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog();
    });
    closeButton.addEventListener('click', () => closeDialog());
    dialog.querySelector('[data-tdisc-retry]').addEventListener('click', loadCreateForm);

    workspace.addEventListener('click', (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest('a[href]');
      if (!link || link.target || link.hasAttribute('download')) return;
      if (dialog.open && link.matches('[data-tdisc-cancel]')) {
        event.preventDefault();
        event.stopPropagation();
        closeDialog();
        return;
      }
      const url = scopedURL(link.href);
      if (!url) return;
      if (link.matches('[data-tdisc-new]') && url.pathname === newURL.pathname) {
        event.preventDefault();
        event.stopPropagation();
        openDialog(link);
        return;
      }
      if (workspace.clientWidth <= 740 || !link.matches('.tdisc-topic,.tdisc-reader .tdisc-back,.tdisc-pagination a,.tdisc-reply-page')) return;
      event.preventDefault();
      event.stopPropagation();
      loadReader(url);
    });

    dialog.addEventListener('submit', async (event) => {
      const form = event.target;
      if (event.defaultPrevented || !form.matches('.tdisc-new-form') || form.method.toLowerCase() !== 'post') return;
      const url = scopedURL(form.action);
      if (!url || url.pathname !== newURL.pathname) return;
      event.preventDefault();
      if (posting || scopeInvalid) return;
      posting = true;
      closeButton.disabled = true;
      const button = event.submitter || form.querySelector('[type="submit"]');
      if (button) button.disabled = true;
      form.setAttribute('aria-busy', 'true');
      setStatus(dialogStatus, 'Creating topic…');
      recovery.hidden = true;
      cancelReader();
      const data = new FormData(form);
      try {
        const response = await fetch(url.href, {method: 'POST', body: data, credentials: 'same-origin', cache: 'no-store'});
        const loaded = await parsedPage(response);
        if (!active || !dialog.open) return;
        const panel = loaded.root.querySelector('.tdisc-new-card');
        if (panel && loaded.url.pathname === newURL.pathname) {
          releaseForms(content);
          content.replaceChildren(document.importNode(panel, true));
          registerForms(content);
          setStatus(dialogStatus, '');
          focusForm();
        } else if (loaded.root.dataset.tdiscTopic && loaded.root.querySelector('.tdisc-reader')) {
          installLayout(loaded, loaded.url);
          releaseForms(content);
          content.replaceChildren();
          dialog.close();
          workspace.querySelector('.tdisc-reader .tdisc-back')?.focus({preventScroll: true});
          setStatus(dialogStatus, '');
        } else {
          throw new Error('unavailable');
        }
      } catch (error) {
        if (active && dialog.open) {
          setStatus(dialogStatus, scopeInvalid
            ? 'Your session changed. Reload this page to continue.'
            : 'The result could not be confirmed. Your draft is still here. Check the topic list before trying again.', true);
        }
      } finally {
        posting = false;
        closeButton.disabled = false;
        form.removeAttribute('aria-busy');
        if (button) button.disabled = false;
      }
    });
    window.addEventListener('pagehide', () => {
      active = false;
      cancelReader();
      cancelDialog();
    });
    window.addEventListener('pageshow', () => { active = true; });
  });
})();
