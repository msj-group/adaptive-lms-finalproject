/* Optional Ledger Studio navigation. Authorized native GETs remain the fallback. */
(function () {
  'use strict';
  const root = document.querySelector('[data-ledger-workspace]');
  const dialog = root?.querySelector('[data-ledger-dialog]');
  if (!root || !dialog?.showModal || !window.fetch || !window.AbortController ||
      !window.DOMParser || !Element.prototype.replaceChildren || !history.pushState) return;
  const actor = root.dataset.ledgerActor;
  const slot = dialog.querySelector('[data-ledger-slot]');
  const pageStatus = root.querySelector('[data-ledger-status]');
  const readerStatus = dialog.querySelector('[data-ledger-dialog-status]');
  const pageRecovery = root.querySelector('[data-ledger-page-recovery]');
  const readerRecovery = dialog.querySelector('[data-ledger-reader-recovery]');
  const backButton = dialog.querySelector('[data-ledger-back]');
  const scrollArea = dialog.querySelector('.ls-dialog-scroll');
  const t = window.aelmsUI?.t || ((value) => value);
  if (!actor || !slot || !pageStatus || !readerStatus || !pageRecovery || !readerRecovery) return;
  const uuid = '[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}';
  const escapeRE = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pathRE = (value) => new RegExp('^' + escapeRE(value)
    .replace('__student__', '(?<student>' + uuid + ')')
    .replace('__document__', '(?<document>' + uuid + ')')
    .replace('__kind__', '(?<kind>invoice|payment)') + '$');
  const nativePaths = [
    ['account', root.dataset.ledgerAccountPath], ['history', root.dataset.ledgerHistoryPath],
    ['document', root.dataset.ledgerDocumentPath], ['receipt', root.dataset.ledgerReceiptPath]
  ].map(([mode, path]) => ({mode, re:pathRE(path)}));
  const reportPaths = {accounts:root.dataset.ledgerAccounts, history:root.dataset.ledgerHistory,
    exceptions:root.dataset.ledgerExceptions};
  let view = root.querySelector('[data-ledger-view]');
  let committed = location.href, controller = null, generation = 0, timer = null;
  let active = true, invalid = false, opener = null, originScroll = 0, current = null;
  let pending = null, blobURL = null;
  const trail = [];

  const message = (node, value, error = false) => {
    node.textContent = value ? t(value) : '';
    node.hidden = !value;
    node.setAttribute('role', error ? 'alert' : 'status');
  };
  const cancel = () => {
    generation++; controller?.abort(); controller = null; clearTimeout(timer);
    view?.removeAttribute('aria-busy'); dialog.removeAttribute('aria-busy');
    dialog.querySelectorAll('button:disabled').forEach((button) => { button.disabled = false; });
  };
  const historyState = (reader = null) => ({...(history.state || {}),
    ledger:{actor, report:committed, reader}});
  const clearRecovery = () => {
    pageRecovery.hidden = true; readerRecovery.hidden = true;
    message(pageStatus, ''); message(readerStatus, ''); pending = null;
  };
  const reportInfo = (url, download = false) => {
    if (url.origin !== location.origin || url.hash) return null;
    let key = url.pathname === root.dataset.ledgerIndex && !download ? 'accounts' : null;
    let format = null;
    Object.entries(reportPaths).forEach(([name, path]) => {
      if (!download && url.pathname === path) key = name;
      ['csv', 'pdf'].forEach((extension) => {
        if (download && url.pathname === path + '.' + extension) { key = name; format = extension; }
      });
    });
    if (!key || download && key !== view.dataset.ledgerKey) return null;
    const allowed = new Set(['q', 'start', 'end', 'student', 'page']);
    for (const name of url.searchParams.keys()) {
      if (!allowed.has(name) || url.searchParams.getAll(name).length !== 1) return null;
    }
    const page = url.searchParams.get('page');
    if (page && !/^[1-9]\d*$/.test(page)) return null;
    const student = url.searchParams.get('student');
    if (student && !new RegExp('^' + uuid + '$').test(student)) return null;
    return {key, format};
  };
  const readerInfo = (href) => {
    let url;
    try { url = new URL(href, location.href); } catch (_) { return null; }
    if (url.origin !== location.origin || url.hash) return null;
    for (const path of nativePaths) {
      const match = path.re.exec(url.pathname);
      if (!match) continue;
      const allowed = path.mode === 'account' ? ['invoice_page', 'payment_page'] : path.mode === 'history' ? ['page'] : [];
      for (const name of url.searchParams.keys()) {
        if (!allowed.includes(name) || url.searchParams.getAll(name).length !== 1 ||
            !/^[1-9]\d*$/.test(url.searchParams.get(name))) return null;
      }
      return {url, mode:path.mode, ...match.groups};
    }
    return null;
  };
  const failScope = () => {
    invalid = true; cancel(); trail.length = 0; slot.replaceChildren();
    backButton.hidden = true;
    if (blobURL) { URL.revokeObjectURL(blobURL); blobURL = null; }
    view.replaceChildren(); current = null;
    const node = dialog.open ? readerStatus : pageStatus;
    message(node, 'Your session changed. Reload this page to continue.', true);
    const recovery = dialog.open ? readerRecovery : pageRecovery;
    recovery.hidden = false;
    recovery.querySelector('[data-ledger-retry]').hidden = true;
    const link = recovery.querySelector('[data-ledger-fallback]');
    link.href = committed; link.textContent = t('Reload page');
  };
  const recover = (job, error = false) => {
    if (invalid) return;
    pending = job;
    const inReader = dialog.open && job.kind !== 'report';
    const recovery = inReader ? readerRecovery : pageRecovery;
    message(inReader ? readerStatus : pageStatus, error ?
      'The export could not be prepared. Retry or use the full page.' :
      'Details could not load. Retry or open the full page.', true);
    recovery.hidden = false;
    recovery.querySelector('[data-ledger-retry]').hidden = false;
    const link = recovery.querySelector('[data-ledger-fallback]');
    link.href = job.href; link.textContent = t('Open full page');
  };
  const animate = () => {
    if (!slot.animate || document.body.dataset.uiMotion === 'reduce' ||
        document.documentElement.dataset.uiMotion === 'reduce' || matchMedia('(prefers-reduced-motion:reduce)').matches) return;
    slot.animate([{opacity:.5, transform:'translateY(9px)'}, {opacity:1, transform:'translateY(0)'}],
      {duration:220, easing:'cubic-bezier(.22,1,.36,1)'});
  };
  const openDialog = (token, fromHistory = false) => {
    if (!dialog.open) {
      opener = document.activeElement; originScroll = window.scrollY; trail.length = 0;
      dialog.showModal(); document.body.classList.add('ledger-reader-open');
      if (!fromHistory) history.pushState(historyState(token), '', committed);
    } else if (!fromHistory) history.replaceState(historyState(token), '', committed);
  };
  const closeDirect = () => {
    cancel(); clearRecovery(); trail.length = 0; slot.replaceChildren(); current = null;
    if (dialog.open) dialog.close();
    document.body.classList.remove('ledger-reader-open');
    opener?.isConnected && opener.focus({preventScroll:true});
    window.scrollTo({top:originScroll, behavior:'instant'});
  };
  const close = () => {
    cancel();
    if (invalid) { closeDirect(); failScope(); return; }
    if (history.state?.ledger?.actor === actor && history.state.ledger.reader) history.back();
    else closeDirect();
  };
  const installReader = (node, token, pushTrail = true, fromHistory = false) => {
    if (node.dataset.ledgerActor !== actor) { failScope(); return; }
    if (pushTrail && current && slot.firstChild) {
      trail.push({nodes:Array.from(slot.childNodes), token:current, scroll:scrollArea.scrollTop});
      if (trail.length > 12) trail.splice(1, 1);
    }
    openDialog(token, fromHistory);
    slot.replaceChildren(node); current = token; scrollArea.scrollTop = 0;
    history.replaceState(historyState(token), '', committed);
    backButton.hidden = !trail.length; clearRecovery(); animate();
    dialog.querySelector('[data-ledger-close]').focus({preventScroll:true});
  };
  const localReader = (token, fromHistory = false) => {
    const template = token === 'export' ? view.querySelector('[data-ledger-export-template]') :
      /^local:ledger-(history|exceptions)-[1-9]\d*-[1-9]\d*$/.test(token) ? document.getElementById(token.slice(6)) : null;
    if (!template || !view.contains(template)) return false;
    const node = template.content.querySelector('[data-ledger-content]');
    if (!node || node.querySelector('script,style,iframe,object,embed')) return false;
    cancel(); installReader(document.importNode(node, true), token, !fromHistory, fromHistory);
    return true;
  };
  const loadReader = async (href, fromHistory = false) => {
    const target = readerInfo(href);
    if (!target || invalid || !active) return;
    if (view.hasAttribute('aria-busy') && !dialog.open) {
      message(pageStatus, 'Updating results. Previous results are still shown.'); return;
    }
    cancel(); clearRecovery(); openDialog(target.url.href, fromHistory);
    const mine = generation, request = new AbortController(); controller = request;
    dialog.setAttribute('aria-busy', 'true'); message(readerStatus, 'Loading details…');
    try {
      const response = await fetch(target.url.href, {credentials:'same-origin', cache:'no-store',
        signal:request.signal, headers:{Accept:'text/html', 'X-Ledger-Reader':'1'}});
      if (!response.ok || response.redirected || response.url !== target.url.href ||
          !response.headers.get('Content-Type')?.includes('text/html')) throw new Error('unavailable');
      const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (mine !== generation || request.signal.aborted || !active || !dialog.open) return;
      const nodes = parsed.querySelectorAll('[data-ledger-content]');
      if (nodes.length !== 1) throw new Error('unavailable');
      const node = nodes[0];
      if (node.dataset.ledgerActor !== actor) { failScope(); return; }
      if (node.dataset.ledgerMode !== target.mode || node.dataset.ledgerStudent !== target.student ||
          (node.dataset.ledgerDocument || '') !== (target.document || '') ||
          (node.dataset.ledgerKind || '') !== (target.kind || '') ||
          node.querySelector('script,style,iframe,object,embed,form')) throw new Error('unavailable');
      installReader(document.importNode(node, true), target.url.href, !fromHistory, fromHistory);
    } catch (error) {
      if (mine === generation && !request.signal.aborted && active) recover({kind:'reader', href:target.url.href});
    } finally {
      if (mine === generation) { dialog.removeAttribute('aria-busy'); controller = null; }
    }
  };
  const loadReport = async (href, push = true, restoreSearch = null) => {
    const url = new URL(href, location.href), wanted = reportInfo(url);
    if (!wanted || invalid || !active) return false;
    cancel(); clearRecovery();
    const mine = generation, request = new AbortController(); controller = request;
    view.setAttribute('aria-busy', 'true'); message(pageStatus, 'Updating report…');
    try {
      const response = await fetch(url.href, {credentials:'same-origin', cache:'no-store',
        signal:request.signal, headers:{Accept:'text/html'}});
      if (!response.ok || response.redirected || response.url !== url.href ||
          !response.headers.get('Content-Type')?.includes('text/html')) throw new Error('unavailable');
      const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (mine !== generation || request.signal.aborted || !active) return false;
      const owners = parsed.querySelectorAll('[data-ledger-workspace]');
      if (owners.length !== 1) throw new Error('unavailable');
      if (owners[0].dataset.ledgerActor !== actor) { failScope(); return false; }
      const views = owners[0].querySelectorAll('[data-ledger-view]');
      if (views.length !== 1 || views[0].dataset.ledgerKey !== wanted.key ||
          views[0].querySelector('script,style,iframe,object,embed,form[method=post]')) throw new Error('unavailable');
      const next = document.importNode(views[0], true);
      view.replaceWith(next); view = next; committed = url.href;
      if (push) history.pushState(historyState(), '', committed);
      else history.replaceState(historyState(), '', committed);
      document.title = parsed.title; message(pageStatus, '');
      if (restoreSearch) {
        const input = view.querySelector('input[name=q]');
        input?.focus({preventScroll:true});
        input?.setSelectionRange(restoreSearch.start, restoreSearch.end);
      }
      return true;
    } catch (error) {
      if (mine === generation && !request.signal.aborted && active) recover({kind:'report', href:url.href});
      return false;
    } finally {
      if (mine === generation) { view.removeAttribute('aria-busy'); controller = null; }
    }
  };
  const download = async (href) => {
    const url = new URL(href, location.href), info = reportInfo(url, true);
    if (!info || invalid || !dialog.open) return;
    cancel(); clearRecovery();
    const mine = generation, request = new AbortController(); controller = request;
    const button = slot.querySelector('[type=submit]');
    if (button) button.disabled = true;
    message(readerStatus, 'Preparing download…');
    const result = slot.querySelector('[data-ledger-export-result]');
    if (result) result.hidden = true;
    try {
      const response = await fetch(url.href, {credentials:'same-origin', cache:'no-store', signal:request.signal});
      const expected = info.format === 'csv' ? 'text/csv' : 'application/pdf';
      if (!response.ok || response.redirected || response.url !== url.href ||
          !response.headers.get('Content-Type')?.includes(expected) ||
          !/^attachment;/i.test(response.headers.get('Content-Disposition') || '')) throw new Error('unavailable');
      const blob = await response.blob();
      if (mine !== generation || request.signal.aborted || !active || !dialog.open) return;
      if (blobURL) URL.revokeObjectURL(blobURL);
      blobURL = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = blobURL;
      link.download = 'student-' + info.key + '.' + info.format; link.hidden = true;
      dialog.append(link); link.click(); link.remove();
      message(readerStatus, '');
      if (result) { result.textContent = t('Download prepared. Check your browser downloads.'); result.hidden = false; }
    } catch (error) {
      if (mine === generation && !request.signal.aborted && active) recover({kind:'download', href:url.href}, true);
    } finally {
      if (mine === generation) { if (button) button.disabled = false; controller = null; }
    }
  };
  const back = () => {
    cancel(); clearRecovery();
    const previous = trail.pop();
    if (!previous) { close(); return; }
    slot.replaceChildren(...previous.nodes); current = previous.token;
    scrollArea.scrollTop = previous.scroll; backButton.hidden = !trail.length;
    history.replaceState(historyState(current), '', committed); animate();
  };
  const submitFilter = (form, restoreSearch = null) => {
    const start = form.querySelector('[name=start]'), end = form.querySelector('[name=end]');
    if (start && end) {
      end.setCustomValidity(Boolean(start.value) !== Boolean(end.value) || start.value > end.value ?
        t('Enter both dates in the correct order, or leave both empty.') : '');
      if (!form.reportValidity()) return;
    }
    const url = new URL(form.action, location.href); url.search = new URLSearchParams(new FormData(form)).toString();
    loadReport(url.href, true, restoreSearch);
  };
  root.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (event.target.closest('[data-ledger-close]')) { event.preventDefault(); close(); return; }
    if (event.target.closest('[data-ledger-back]')) { event.preventDefault(); back(); return; }
    if (invalid) return;
    if (event.target.closest('[data-ledger-export-open]')) {
      event.preventDefault(); if (!view.hasAttribute('aria-busy')) localReader('export'); return;
    }
    if (event.target.closest('[data-ledger-print]')) { event.preventDefault(); window.print(); return; }
    if (event.target.closest('[data-ledger-retry]') && pending) {
      const job = pending; event.preventDefault();
      if (job.kind === 'report') loadReport(job.href);
      else if (job.kind === 'download') download(job.href);
      else loadReader(job.href);
      return;
    }
    const link = event.target.closest('a');
    if (!link) return;
    if (link.hasAttribute('data-ledger-nav') && reportInfo(new URL(link.href))) {
      event.preventDefault(); loadReport(link.href); return;
    }
    if (link.dataset.ledgerLocal) {
      if (view.hasAttribute('aria-busy')) { event.preventDefault(); return; }
      if (localReader('local:' + link.dataset.ledgerLocal)) event.preventDefault();
      return;
    }
    if (link.hasAttribute('data-ledger-open') && readerInfo(link.href)) {
      event.preventDefault(); loadReader(link.href);
    }
  });
  root.addEventListener('submit', (event) => {
    const form = event.target;
    if (invalid) { event.preventDefault(); return; }
    if (form.matches('[data-ledger-filter]')) { event.preventDefault(); submitFilter(form); }
    if (form.matches('[data-ledger-export-form]')) {
      event.preventDefault(); const selected = form.querySelector('input[name=format]:checked');
      if (selected) download(selected.dataset.url);
    }
  });
  root.addEventListener('input', (event) => {
    const input = event.target;
    if (input.closest('[data-ledger-export-form]')) {
      cancel(); clearRecovery(); const result = slot.querySelector('[data-ledger-export-result]');
      if (result) result.hidden = true;
    }
    if (input.closest('[data-ledger-filter]') && ['start', 'end'].includes(input.name)) {
      input.form.querySelector('[name=end]')?.setCustomValidity('');
    }
    if (input.name === 'q' && input.closest('[data-ledger-filter]') && !invalid) {
      cancel(); view.setAttribute('aria-busy', 'true');
      timer = setTimeout(() => {
        if (input.isConnected) submitFilter(input.form, {start:input.selectionStart, end:input.selectionEnd});
      }, 350);
    }
  });
  dialog.addEventListener('cancel', (event) => { event.preventDefault(); close(); });
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) {
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
    }
  });
  window.addEventListener('popstate', async (event) => {
    if (!active) return;
    if (invalid) { if (dialog.open) { closeDirect(); failScope(); } return; }
    const destination = event.state?.ledger;
    if (destination?.actor && destination.actor !== actor) { failScope(); return; }
    if (!destination?.reader) {
      closeDirect();
      if (location.href !== committed) await loadReport(location.href, false);
      return;
    }
    if (location.href !== committed && !await loadReport(location.href, false)) return;
    if (destination.reader === 'export' || destination.reader.startsWith('local:')) localReader(destination.reader, true);
    else loadReader(destination.reader, true);
  });
  window.addEventListener('pagehide', () => {
    active = false; closeDirect();
    if (blobURL) URL.revokeObjectURL(blobURL);
  });
  window.addEventListener('pageshow', (event) => { active = true; if (event.persisted) location.reload(); });
  history.replaceState(historyState(), '', committed);
})();
