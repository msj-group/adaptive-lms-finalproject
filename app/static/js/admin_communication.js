/* Admin announcements and shared role messaging. Native routes own data. */
(function () {
  'use strict';
  if (!window.fetch || !window.AbortController || !window.DOMParser || !window.FormData ||
      !window.aelmsRegisterForms || !window.aelmsFormHasChanges || !window.aelmsConfirm ||
      !Element.prototype.replaceChildren || !history.pushState) return;
  const t = window.aelmsUI.t;
  const announcements = document.querySelector('[data-communication-announcements]');
  const messages = document.querySelector('[data-communication-messages]');
  const dialog = document.querySelector('[data-announcement-dialog]');
  if (announcements && dialog?.showModal) enhance(announcements, dialog, false);
  if (messages && window.aelmsEnhanceMessages) enhance(messages, messages, true);

  function enhance(root, surface, isMessage) {
    const actor = root.dataset.communicationActor;
    const content = surface.querySelector('[data-communication-content]');
    const status = surface.querySelector('[data-communication-status]');
    const recovery = surface.querySelector('[data-communication-recovery]');
    const retry = surface.querySelector('[data-communication-retry]');
    const fallback = surface.querySelector('[data-communication-fallback]');
    const closeButton = surface.querySelector('[data-communication-close]');
    const listURL = new URL(isMessage ? root.dataset.inboxUrl : root.dataset.announcementsUrl, location.href);
    const newURL = new URL(isMessage ? root.dataset.messageNewUrl : root.dataset.announcementNewUrl, location.href);
    const threadURL = isMessage && new URL(root.dataset.threadUrl, location.href);
    if (!actor || !content || listURL.origin !== location.origin || newURL.origin !== location.origin) return;
    let controller, revision = 0, posting = false, confirming = false, uncertain = false;
    let scopeInvalid = false, validationDraft = false, refreshNeeded = false, opener;
    let active = true, current = isMessage ? new URL(location.href) : null, target = current;
    let committed = location.href, searchTimer;
    const isOpen = () => active && (isMessage || surface.open);
    const setStatus = (text, error = false) => {
      status.textContent = text ? t(text) : '';
      status.hidden = !text;
      status.setAttribute('role', error ? 'alert' : 'status');
    };
    const cancelRead = () => { revision++; controller?.abort(); controller = null; clearTimeout(searchTimer); };
    const release = () => content.querySelectorAll('form').forEach((form) => form.dispatchEvent(new Event('workspace:saved')));
    const dirty = () => validationDraft || window.aelmsFormHasChanges(content) ||
      Boolean(isMessage && content.querySelector('[data-people-message-form] textarea')?.value.trim());
    const permit = async () => {
      if (posting || confirming) return false;
      confirming = true;
      try { return !dirty() || await window.aelmsConfirm(t('Discard these unsaved details?'), t('Discard draft')); }
      finally { confirming = false; }
    };
    const info = (url) => {
      if (url.origin !== location.origin) return null;
      if (url.pathname === listURL.pathname) return {mode:'inbox'};
      if (url.pathname === newURL.pathname) return {mode:isMessage ? url.searchParams.has('to') ? 'compose' : 'picker' : 'create', person:url.searchParams.get('to')};
      const prefix = isMessage ? threadURL.pathname.split('__thread__')[0] : listURL.pathname.replace(/\/$/, '') + '/';
      if (!url.pathname.startsWith(prefix)) return null;
      const parts = url.pathname.slice(prefix.length).split('/');
      if (!/^[A-Za-z0-9_-]+$/.test(parts[0]) || parts.length > 2) return null;
      if (parts[1] && !['edit', 'publish', 'withdraw', 'reply'].includes(parts[1])) return null;
      return {mode:parts[1] || (isMessage ? 'thread' : 'detail'), id:parts[0]};
    };
    const parsed = async (response) => {
      if (!(response.headers.get('Content-Type') || '').includes('text/html')) throw new Error('unavailable');
      const url = new URL(response.url);
      const destination = info(url);
      if (!destination) throw new Error('unavailable');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const owner = page.querySelector(isMessage ? '[data-communication-messages]' : '[data-announcement-view]');
      if (!owner || owner.dataset.communicationActor !== actor) throw new Error('scope');
      const node = isMessage ? owner.querySelector('[data-communication-content]') : owner;
      if (!node || node.querySelector('script,style,iframe,object,embed')) throw new Error('unavailable');
      const mode = isMessage ? node.dataset.messageView : node.dataset.announcementView;
      const id = isMessage ? node.dataset.threadId : node.dataset.announcementId;
      if (destination.id && destination.id !== id) throw new Error('unavailable');
      if (!isMessage && mode !== destination.mode) throw new Error('unavailable');
      if (isMessage && mode === 'thread' && !id) throw new Error('unavailable');
      return {node, url, mode, id, response};
    };
    const validateRead = (loaded, requested) => {
      const wanted = info(requested);
      if (!wanted) throw new Error('unavailable');
      if (isMessage && wanted.mode === 'compose') {
        if (loaded.mode === 'compose' && loaded.node.dataset.personId === wanted.person &&
            loaded.node.querySelector('[name="recipient"]')?.value === wanted.person && loaded.url.href === requested.href) return;
        // The authorized recipient GET may redirect to an existing conversation or picker.
        if (loaded.response.redirected && ['thread','picker'].includes(loaded.mode)) return;
      } else if (loaded.url.href === requested.href && loaded.mode === wanted.mode) return;
      else if (!isMessage && wanted.mode === 'edit' && loaded.response.redirected && loaded.mode === 'detail' && loaded.id === wanted.id) return;
      throw new Error('unavailable');
    };
    const animate = (node) => {
      if (!node?.animate || document.body.dataset.uiMotion === 'reduce' || matchMedia('(prefers-reduced-motion:reduce)').matches) return;
      const direction = document.documentElement.dir === 'rtl' ? -12 : 12;
      node.animate([{opacity:.35, transform:'translate(' + direction + 'px, 6px)'}, {opacity:1, transform:'translate(0, 0)'}], {duration:260, easing:'cubic-bezier(.22,1,.36,1)'});
    };
    const install = (loaded, historyMode = 'push', keepDraft = false, restoreSearch = null) => {
      const node = document.importNode(loaded.node, true);
      const displayURL = new URL(loaded.url);
      if (isMessage && loaded.mode === 'thread') displayURL.pathname = threadURL.pathname.replace('__thread__', encodeURIComponent(loaded.id));
      if (isMessage && loaded.mode === 'compose') displayURL.searchParams.set('to', node.dataset.personId);
      // Resolve action-less native editors against their fetched URL, never the board URL.
      node.querySelectorAll('form[method="post"]').forEach((form) => {
        // Edit/hide/clear remain their existing native workflows and action-sheet owners.
        if (isMessage && !form.matches('[data-people-message-form]')) return;
        const post = new URL(form.getAttribute('action') || loaded.url.href, loaded.url);
        if (!info(post) || !form.querySelector('[name="csrf_token"]') ||
            !form.querySelector(isMessage ? '[name="message_state"]' : '[name="announcement_state"]')) throw new Error('unavailable');
        form.action = post.href;
      });
      if (!isMessage) {
        const ids = new Map();
        node.querySelectorAll('[id]').forEach((field) => { ids.set(field.id, 'announcement-inline-' + field.id); field.id = ids.get(field.id); });
        node.querySelectorAll('[for],[aria-describedby],[aria-labelledby],[aria-controls]').forEach((field) => {
          ['for','aria-describedby','aria-labelledby','aria-controls'].forEach((name) => {
            if (field.hasAttribute(name)) field.setAttribute(name, field.getAttribute(name).split(' ').map((id) => ids.get(id) || id).join(' '));
          });
        });
      }
      release();
      if (isMessage) window.aelmsDisposeMessages();
      if (isMessage) {
        content.replaceChildren(...Array.from(node.childNodes));
        content.dataset.messageView = loaded.mode;
        content.dataset.threadId = loaded.id;
        content.dataset.personId = node.dataset.personId;
        if (historyMode !== 'pop' && displayURL.href !== location.href) history[historyMode === 'replace' ? 'replaceState' : 'pushState'](null, '', displayURL.href);
        committed = location.href;
        window.aelmsEnhanceMessages(content);
      } else content.replaceChildren(node);
      current = displayURL;
      target = current;
      validationDraft = keepDraft;
      uncertain = false;
      window.aelmsRegisterForms(content);
      setStatus(''); recovery.hidden = true;
      const field = restoreSearch ? content.querySelector('#conversation-inbox-query') :
        content.querySelector('[aria-invalid="true"],.field:has(.field__error) input,.field:has(.field__error) textarea') ||
        (isMessage && loaded.mode === 'compose' ? content.querySelector('[data-message-input]') :
          content.querySelector('#conversation-title') || content.querySelector('h1,h2,[data-conversation-query]'));
      if (field) { if (!field.matches('input,textarea,select')) field.tabIndex = -1; field.focus({preventScroll:true}); }
      if (restoreSearch && field) field.setSelectionRange(restoreSearch.start, restoreSearch.end);
      if (isMessage) animate(content.querySelector('.messenger-chat,.messenger-welcome') || content.querySelector('.messenger-sidebar'));
      else animate(node);
      if (!isMessage) surface.scrollTop = 0;
    };
    const failed = (post = false) => {
      setStatus(scopeInvalid ? 'Your session changed. Reload this page to continue.' : post ?
        'The result could not be confirmed. Your details remain here. Review the full page before trying again.' :
        'The form could not load. Retry or open the full page.', true);
      recovery.hidden = false;
      retry.hidden = post || scopeInvalid;
      if (target) fallback.href = target.href;
    };
    const load = async (url, historyMode = 'push', restoreSearch = null) => {
      if (posting || scopeInvalid || !isOpen()) return;
      cancelRead(); target = new URL(url); fallback.href = target.href;
      const mine = revision, request = new AbortController(); controller = request;
      content.setAttribute('aria-busy','true'); recovery.hidden = true; setStatus('Loading…');
      try {
        const response = await fetch(target.href, {credentials:'same-origin', cache:'no-store', signal:request.signal, headers:{Accept:'text/html'}});
        if (mine !== revision || request.signal.aborted || !isOpen()) return;
        if (!response.ok) throw new Error([401,403].includes(response.status) ? 'scope' : 'unavailable');
        const loaded = await parsed(response);
        if (mine !== revision || request.signal.aborted || !isOpen()) return;
        validateRead(loaded, url);
        install(loaded, historyMode, false, restoreSearch);
      } catch (error) {
        if (mine !== revision || request.signal.aborted || !isOpen()) return;
        if (error.message === 'scope') scopeInvalid = true;
        if (isMessage && historyMode === 'pop') history.replaceState(null, '', committed);
        failed();
      } finally { if (mine === revision) { controller = null; content.removeAttribute('aria-busy'); } }
    };
    const close = () => {
      cancelRead(); release(); content.replaceChildren(); surface.close(); validationDraft = false;
      if (refreshNeeded) {
        const form = root.querySelector('[data-query-form]');
        if (form?.dataset.queryRefreshReady === 'true') form.dispatchEvent(new Event('aelms:query-refresh', {cancelable:true}));
        refreshNeeded = false;
      }
    };
    const closeSafely = async () => { if (await permit()) close(); };
    const click = async (event) => {
      if (event.defaultPrevented || event.button || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      const link = event.target.closest('a[href]');
      if (!link || link.target || link.hasAttribute('download') || link === fallback) return;
      const url = new URL(link.href, location.href), destination = info(url);
      if (!destination) return;
      if (!isMessage && !surface.open && destination.mode === 'inbox') return; // Shared query owner handles the rail.
      if (['reply','publish','withdraw'].includes(destination.mode)) return;
      event.preventDefault(); event.stopPropagation();
      if (posting || confirming || scopeInvalid) return;
      if (!isMessage && !surface.open) {
        opener = link; uncertain = false; validationDraft = false;
        surface.showModal(); animate(surface);
      } else if (!await permit()) return;
      if (!isMessage && destination.mode === 'inbox') { close(); return; }
      load(url);
    };
    root.addEventListener('click', click);
    if (!isMessage) {
      surface.addEventListener('click', (event) => {
        if (event.target !== surface) { click(event); return; }
        const bounds = surface.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closeSafely();
      });
      closeButton.addEventListener('click', closeSafely);
      surface.addEventListener('cancel', (event) => { event.preventDefault(); closeSafely(); });
      surface.addEventListener('close', () => { if (opener?.isConnected) opener.focus({preventScroll:true}); });
    }
    retry.addEventListener('click', async () => { if (target && await permit()) load(target); });
    surface.addEventListener('submit', async (event) => {
      const form = event.target;
      if (event.defaultPrevented || form.method.toLowerCase() !== 'post' ||
          !content.contains(form) || isMessage && !form.matches('[data-people-message-form]')) return;
      event.preventDefault(); event.stopPropagation();
      if (posting || confirming || uncertain || scopeInvalid || !isOpen() || !current) return;
      const postURL = new URL(form.action || current.href), from = info(current), post = info(postURL);
      if (!post || !form.querySelector('[name="csrf_token"]') ||
          !form.querySelector(isMessage ? '[name="message_state"]' : '[name="announcement_state"]')) return;
      if (isMessage && (from.mode === 'thread' ? post.mode !== 'reply' || post.id !== from.id :
          post.mode !== 'picker' || form.querySelector('[name="recipient"]')?.value !== content.dataset.personId)) return;
      if (!isMessage && (from.mode === 'detail' ? !['publish','withdraw'].includes(post.mode) || post.id !== from.id : postURL.href !== current.href)) return;
      cancelRead(); posting = true; validationDraft = true;
      const data = new FormData(form);
      if (event.submitter?.name) data.append(event.submitter.name, event.submitter.value);
      const buttons = Array.from(form.querySelectorAll('[type="submit"]'));
      buttons.forEach((button) => { button.disabled = true; });
      if (closeButton) closeButton.disabled = true;
      form.setAttribute('aria-busy','true'); setStatus(isMessage ? 'Sending…' : 'Saving…');
      try {
        const response = await fetch(postURL.href, {method:'POST', body:data, credentials:'same-origin', cache:'no-store', headers:{Accept:'text/html'}});
        if (!response.ok && ![400,422].includes(response.status)) throw new Error([401,403].includes(response.status) ? 'scope' : 'unavailable');
        const loaded = await parsed(response);
        if (!isOpen()) return;
        if (!response.redirected && [200,400,422].includes(response.status) && loaded.url.href === postURL.href) {
          if (isMessage ? loaded.mode !== (from.mode === 'thread' ? 'thread' : 'compose') ||
              (from.id ? loaded.id !== from.id : loaded.node.dataset.personId !== content.dataset.personId) :
              loaded.mode !== from.mode || loaded.id !== (from.id || '')) throw new Error('unavailable');
          install(loaded, 'replace', true); return;
        }
        if (!response.ok || !response.redirected) throw new Error('unavailable');
        if (isMessage) {
          const proof = loaded.node.querySelector('[data-people-message-panel]');
          if (loaded.mode !== 'thread' || from.id && loaded.id !== from.id ||
              proof?.dataset.messageSent !== 'true' || proof.dataset.messageBlocked !== 'false') throw new Error('unavailable');
        } else if (loaded.id !== (from.id || loaded.id) || !['detail',from.mode].includes(loaded.mode)) throw new Error('unavailable');
        if (!isMessage && loaded.mode === 'detail') refreshNeeded = true;
        install(loaded, 'replace', loaded.mode === 'create' || loaded.mode === 'edit');
      } catch (error) {
        if (!isOpen()) return;
        if (error.message === 'scope') scopeInvalid = true;
        uncertain = true; failed(true);
      } finally {
        posting = false;
        if (closeButton) closeButton.disabled = false;
        form.removeAttribute('aria-busy');
        buttons.forEach((button) => { button.disabled = uncertain || scopeInvalid; });
      }
    });
    if (isMessage) {
      const search = (form) => {
        const url = new URL(listURL); url.search = new URLSearchParams(new FormData(form)).toString();
        const input = form.querySelector('input[name="q"]');
        load(url, 'replace', {start:input.selectionStart, end:input.selectionEnd});
      };
      // Own this workspace search in capture phase, including the initial shared live-query form.
      root.addEventListener('input', (event) => {
        const form = event.target.closest('form.messenger-inbox-search');
        if (!form) return;
        event.stopImmediatePropagation(); cancelRead();
        if (!event.isComposing) searchTimer = setTimeout(() => search(form), 300);
      }, true);
      root.addEventListener('compositionstart', (event) => { if (event.target.closest('.messenger-inbox-search')) { event.stopImmediatePropagation(); cancelRead(); } }, true);
      root.addEventListener('compositionend', (event) => { const form = event.target.closest('.messenger-inbox-search'); if (form) { event.stopImmediatePropagation(); cancelRead(); searchTimer = setTimeout(() => search(form), 300); } }, true);
      root.addEventListener('submit', (event) => {
        if (!event.target.matches('.messenger-inbox-search')) return;
        event.preventDefault(); event.stopImmediatePropagation(); search(event.target);
      }, true);
      window.addEventListener('popstate', async () => {
        const url = new URL(location.href);
        if (!info(url)) { location.reload(); return; }
        if (posting || confirming || dirty()) {
          history.replaceState(null, '', committed);
          if (await permit()) load(url);
        } else load(url, 'pop');
      });
    }
    window.addEventListener('pagehide', () => { if (posting) uncertain = true; active = false; cancelRead(); });
    window.addEventListener('pageshow', (event) => {
      active = true;
      if (event.persisted && (posting || uncertain)) {
        uncertain = true; content.querySelectorAll('[type="submit"]').forEach((button) => { button.disabled = true; }); failed(true);
      }
    });
  }
})();
