/* Optional native-route Finance readers. No financial calculation or write policy. */
(function () {
  'use strict';
  const root = document.querySelector('[data-finance-workspace]');
  if (!root || !window.fetch || !window.AbortController || !window.DOMParser || !window.FormData ||
      !window.aelmsRegisterForms || !window.aelmsRegisterFinanceForms || !window.aelmsFormHasChanges ||
      !window.aelmsConfirm || !Element.prototype.replaceChildren) return;
  const t = window.aelmsUI.t;
  const origin = root.querySelector('[data-finance-origin]');
  const desk = root.querySelector('[data-finance-desk]');
  const reader = root.querySelector('[data-finance-reader]');
  const stage = root.querySelector('[data-finance-stage]');
  const status = root.querySelector('[data-finance-status]');
  const recovery = root.querySelector('[data-finance-recovery]');
  const retry = root.querySelector('[data-finance-retry]');
  const fallback = root.querySelector('[data-finance-recover]');
  const native = root.querySelector('[data-finance-native]');
  const actor = root.dataset.financeActor;
  const directory = root.dataset.financeRoot === 'accounts';
  const registers = root.dataset.financeRoot === 'register';
  if (!origin || !desk || !reader || !stage || !actor?.endsWith(':administrator')) return;
  const idPattern = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
  const uuid = new RegExp('^' + idPattern + '$', 'i');
  const paths = {};
  const matchers = {};
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  for (const view of ['account', 'new', 'document', 'correct', 'history', 'receipt', 'enrollment']) {
    const url = new URL(root.dataset['finance' + view[0].toUpperCase() + view.slice(1) + 'Url'], location.href);
    if (url.origin !== location.origin) return;
    paths[view] = url.pathname;
    matchers[view] = new RegExp('^' + escape(url.pathname)
      .replace('__student__', '(?<student>' + idPattern + ')')
      .replace('__kind__', '(?<kind>invoice|payment)')
      .replace('__document__', '(?<document>' + idPattern + ')') + '$', 'i');
  }
  let current = null, pending = null, pendingOptions = null, controller = null, revision = 0, serial = 0;
  let posting = false, confirming = false, uncertain = false, validationDraft = false;
  let active = true, blocked = false, originStale = false, returnFresh = false;
  let originFocus = null, originScroll = 0;
  const trail = [];
  const ledgerChoices = new Map();

  const message = (text = '', error = false) => {
    status.textContent = text ? t(text) : '';
    status.hidden = !text;
    status.setAttribute('role', error ? 'alert' : 'status');
  };
  const release = node => node.querySelectorAll('form').forEach(form => form.dispatchEvent(new Event('workspace:saved')));
  const dirty = () => validationDraft || window.aelmsFormHasChanges(stage) || reader.hidden && window.aelmsFormHasChanges(origin);
  const cancel = () => {
    revision += 1;
    controller?.abort(); controller = null;
    reader.removeAttribute('aria-busy');
  };
  const accountURL = student => new URL(paths.account.replace('__student__', student), location.href);
  const classify = href => {
    const url = new URL(href, location.href);
    if (url.origin !== location.origin || url.username || url.password) return null;
    for (const [view, pattern] of Object.entries(matchers)) {
      const match = pattern.exec(url.pathname);
      if (!match) continue;
      const allowed = view === 'account' ? ['invoice_page', 'payment_page'] : view === 'new' ? ['invoice'] : ['history', 'enrollment'].includes(view) ? ['page'] : [];
      for (const key of url.searchParams.keys()) {
        if (!allowed.includes(key) || url.searchParams.getAll(key).length !== 1) return null;
        const value = url.searchParams.get(key);
        if (key === 'invoice' ? !uuid.test(value) : !/^[1-9]\d*$/.test(value)) return null;
      }
      if (url.hash && (view !== 'account' || !['#invoices', '#movements'].includes(url.hash))) return null;
      const section = url.hash.slice(1);
      url.hash = '';
      return {url, view, student: match.groups.student, kind: match.groups.kind || '', document: match.groups.document || '', section};
    }
    return null;
  };
  const permit = async () => {
    if (posting || confirming || blocked) return false;
    confirming = true;
    try {
      return !dirty() || await window.aelmsConfirm(t(uncertain ?
        'Saving was not confirmed. Review the account before recording another movement. Leave these unsaved details?' :
        'Discard these unsaved details?'), t('Discard draft'));
    } finally { confirming = false; }
  };
  const selectAccount = student => origin.querySelectorAll('[data-finance-account]').forEach(link => {
    if (link.dataset.financeAccount === student) link.setAttribute('aria-current', 'true');
    else link.removeAttribute('aria-current');
  });
  const ledger = (node, target) => {
    node.querySelectorAll('[data-finance-ledger]').forEach(panel => {
      const selected = target?.section || ledgerChoices.get(target?.student) || 'movements';
      panel.querySelector('[data-finance-switch]').hidden = false;
      panel.querySelectorAll('[data-finance-section]').forEach(section => section.hidden = section.dataset.financeSection !== selected);
      panel.querySelectorAll('[data-finance-tab]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.financeTab === selected)));
    });
  };
  const verify = (page, target) => {
    const nodes = page.querySelectorAll('[data-finance-content]');
    if (nodes.length !== 1) throw new Error('unavailable');
    const node = nodes[0];
    if (node.dataset.financeActor !== actor) { blocked = true; throw new Error('scope'); }
    if (node.dataset.financeView !== target.view || node.dataset.financeStudent !== target.student ||
        node.dataset.financeDocument !== target.document || node.dataset.financeKind !== target.kind) throw new Error('unavailable');
    if (target.view !== 'enrollment') {
      const workspaces = page.querySelectorAll('[data-finance-workspace]');
      if (workspaces.length !== 1 || workspaces[0].dataset.financeActor !== actor) { blocked = true; throw new Error('scope'); }
    }
    if (node.querySelector('script,style,iframe,object,embed,base,link') ||
        [node, ...node.querySelectorAll('*')].some(item => [...item.attributes].some(attr => /^on/i.test(attr.name) || attr.name === 'srcdoc'))) throw new Error('unavailable');
    const forms = node.querySelectorAll('form');
    if (!['new', 'correct'].includes(target.view)) {
      if (forms.length) throw new Error('unavailable');
      return node;
    }
    const form = node.querySelector('form[data-finance-form]');
    // Native fields are named action/method; they shadow HTMLFormElement
    // properties. Read/write attributes so the signed native form stays intact.
    if (forms.length !== 1 || !form || (form.getAttribute('method') || 'get').toLowerCase() !== 'post' ||
        !form.querySelector('input[name="csrf_token"]')?.value ||
        !form.querySelector('input[name="' + (target.view === 'new' ? 'operation_key' : 'snapshot') + '"]')?.value) throw new Error('unavailable');
    const declared = form.getAttribute('action');
    if (declared && new URL(declared, target.url).href !== target.url.href) throw new Error('unavailable');
    const preview = new URL(root.dataset.financePreviewUrl.replace('__student__', target.student), location.href);
    if (new URL(form.dataset.previewUrl, target.url).href !== preview.href) throw new Error('unavailable');
    if (target.view === 'correct' && (form.querySelector('[name="document_kind"]')?.value !== target.kind ||
        form.querySelector('[name="document_public_id"]')?.value !== target.document)) throw new Error('unavailable');
    return node;
  };
  const parse = async response => {
    if (!response.ok || !response.headers.get('Content-Type')?.includes('text/html')) throw new Error('unavailable');
    const url = new URL(response.url);
    if (url.origin !== location.origin) throw new Error('unavailable');
    return {page: new DOMParser().parseFromString(await response.text(), 'text/html'), url};
  };
  const prepare = (source, target) => {
    const node = document.importNode(source, true);
    const ids = new Map(), prefix = 'ff-inline-' + (++serial) + '-';
    [node, ...node.querySelectorAll('[id]')].forEach(item => {
      if (!item.id) return;
      ids.set(item.id, prefix + item.id); item.id = ids.get(item.id);
    });
    node.querySelectorAll('[for],[aria-describedby],[aria-labelledby],[aria-controls],a[href^="#"]').forEach(item => {
      for (const name of ['for', 'aria-describedby', 'aria-labelledby', 'aria-controls']) {
        if (item.hasAttribute(name)) item.setAttribute(name, item.getAttribute(name).split(' ').map(id => ids.get(id) || id).join(' '));
      }
      if (item.matches('a[href^="#"]')) {
        const id = item.getAttribute('href').slice(1);
        if (ids.has(id)) item.setAttribute('href', '#' + ids.get(id));
      }
    });
    node.querySelectorAll('form[data-finance-form]').forEach(form => form.setAttribute('action', target.url.href));
    return node;
  };
  const install = (node, target, focus = true) => {
    release(stage); stage.replaceChildren(node); current = target;
    validationDraft = false; uncertain = false; pending = null; pendingOptions = null; returnFresh = false;
    origin.hidden = !directory; reader.hidden = false;
    native.href = target.url.href; fallback.href = target.url.href;
    recovery.hidden = true; message(); selectAccount(target.student);
    ledger(node, target);
    window.aelmsRegisterForms(node); window.aelmsRegisterFinanceForms(node);
    window.aelmsEnhanceTables?.(node);
    const errors = [...node.querySelectorAll('.field__error')].filter(error => error.textContent.trim());
    errors.forEach((error, i) => {
      error.id ||= 'ff-error-' + serial + '-' + i;
      const input = error.closest('.field')?.querySelector('input,select,textarea');
      input?.setAttribute('aria-invalid', 'true'); input?.setAttribute('aria-describedby', error.id);
    });
    if (focus) {
      const field = errors[0]?.closest('.field')?.querySelector('input,select,textarea') || node.querySelector('input:not([type="hidden"]):not([type="submit"]),select,textarea');
      (field || root.querySelector('[data-finance-back]')).focus({preventScroll:true});
      const bounds = reader.getBoundingClientRect();
      if (window.matchMedia('(max-width:900px)').matches || bounds.top < 0 || bounds.top > innerHeight - 140) reader.scrollIntoView({block:'start', behavior:'instant'});
    }
  };
  const remember = focus => {
    if (!current || stage.querySelector('form')) return;
    trail.push({target: current, nodes:[...stage.childNodes], focus, scroll:window.scrollY});
    if (trail.length > 8) trail.shift();
  };
  const load = async (target, options = {}) => {
    if (!target || !active || blocked || posting || !options.permitted && !await permit()) return;
    if (options.link?.closest('#query-results[aria-busy="true"]')) { message('Updating results. Previous results are still shown.'); return; }
    const fromOrigin = reader.hidden;
    cancel(); const mine = revision; const request = new AbortController(); controller = request;
    pending = target; pendingOptions = {accountRoot:options.accountRoot, replace:options.replace, link:options.link};
    reader.hidden = false; desk.classList.add('ff-open'); reader.setAttribute('aria-busy', 'true');
    recovery.hidden = true; fallback.href = target.url.href; message('Loading account details…');
    try {
      const response = await fetch(target.url.href, {credentials:'same-origin', cache:'no-store', signal:request.signal, headers:{Accept:'text/html'}});
      const loaded = await parse(response);
      if (response.redirected || loaded.url.href !== target.url.href) throw new Error('unavailable');
      const source = verify(loaded.page, target);
      if (mine !== revision || request.signal.aborted || !active || options.link && !options.link.isConnected) return;
      const node = prepare(source, target);
      if (options.accountRoot) trail.length = 0;
      else if (!options.replace) remember(options.link || document.activeElement);
      if (!current || options.accountRoot) { originFocus = options.link || document.activeElement; originScroll = window.scrollY; }
      if (fromOrigin && !directory && !registers) {
        origin.querySelectorAll('form[method="post"]').forEach(form => form.reset());
        release(origin);
      }
      install(node, target, options.focus !== false);
    } catch (error) {
      if (mine !== revision || error.name === 'AbortError' || !active) return;
      recovery.hidden = false; retry.hidden = blocked;
      message(blocked ? 'Your account context changed. Reload this page before continuing.' : 'Account details could not be loaded. Your current view is still available.', true);
    } finally { if (mine === revision) reader.removeAttribute('aria-busy'); if (controller === request) controller = null; }
  };
  const back = async () => {
    if (!await permit()) return;
    cancel(); pending = null; pendingOptions = null; recovery.hidden = true; message();
    if ((uncertain || returnFresh) && current) {
      trail.length = 0;
      await load(classify(accountURL(current.student).href), {permitted:true, accountRoot:true});
      return;
    }
    const previous = trail.pop();
    if (previous) {
      release(stage); stage.replaceChildren(...previous.nodes); current = previous.target;
      validationDraft = false; uncertain = false; native.href = current.url.href;
      selectAccount(current.student);
      previous.focus?.focus({preventScroll:true}); window.scrollTo({top:previous.scroll, behavior:'instant'});
      return;
    }
    if (originStale && !directory && !registers) { location.assign(accountURL(current.student).href); return; }
    const student = current?.student;
    release(stage); stage.replaceChildren(); current = null; uncertain = false; validationDraft = false;
    reader.hidden = true; desk.classList.remove('ff-open'); origin.hidden = false; selectAccount('');
    const focus = originFocus?.isConnected ? originFocus : [...origin.querySelectorAll('[data-finance-account]')].find(link => link.dataset.financeAccount === student);
    window.scrollTo({top:originScroll, behavior:'instant'}); focus?.focus({preventScroll:true}); focus?.scrollIntoView({block:'nearest'});
  };
  const refreshDirectory = () => {
    originStale = true;
    const form = origin.querySelector('form[data-query-refresh]');
    if (form?.dataset.queryRefreshReady === 'true') form.dispatchEvent(new CustomEvent('aelms:query-refresh', {bubbles:true, cancelable:true}));
  };
  const submit = async (form, submitter) => {
    if (posting || uncertain || blocked || !current || !['new', 'correct'].includes(current.view)) return;
    const target = current;
    if (new URL(form.getAttribute('action'), location.href).href !== target.url.href || !form.querySelector('[name="csrf_token"]')?.value) return;
    const data = new FormData(form);
    if (submitter?.name) data.append(submitter.name, submitter.value);
    cancel(); posting = true; form.inert = true; form.setAttribute('aria-busy', 'true');
    const buttons = [...form.querySelectorAll('button,input[type="submit"]')].map(button => [button,button.disabled]);
    buttons.forEach(([button]) => button.disabled = true);
    recovery.hidden = true; message('Saving financial details…');
    const mine = revision;
    try {
      const response = await fetch(target.url.href, {method:'POST', credentials:'same-origin', cache:'no-store', body:data, headers:{Accept:'text/html'}});
      const loaded = await parse(response);
      if (!active || mine !== revision) return;
      const result = classify(loaded.url.href);
      if (!result || result.student !== target.student) throw new Error('unavailable');
      const source = verify(loaded.page, result);
      if (result.view === target.view && loaded.url.href === target.url.href) {
        // Validation and stale-form redirects keep the server's returned fields
        // and paired snapshot. Never merge an obsolete attempted snapshot.
        install(prepare(source, result), result); validationDraft = !response.redirected;
        if (response.redirected) { trail.length = 0; returnFresh = true; refreshDirectory(); }
        message('Review the returned form and its messages before saving again.');
      } else if (result.view === 'account' && response.redirected && loaded.url.href === accountURL(target.student).href &&
                 source.querySelector('[data-finance-completed="' + target.view + '"]')) {
        trail.length = 0; install(prepare(source, result), result); refreshDirectory();
      } else if (target.view === 'correct' && result.view === 'document' && result.kind === target.kind && result.document === target.document && response.redirected) {
        install(prepare(source, result), result); message('Review the returned record. No completed save was confirmed.');
      } else throw new Error('unavailable');
    } catch (_) {
      if (!active || mine !== revision) return;
      uncertain = true; validationDraft = true; pending = null;
      recovery.hidden = false; retry.hidden = true; fallback.href = accountURL(target.student).href;
      message('Saving was not confirmed. Your details are retained. Review the account before trying another write.', true);
    } finally {
      posting = false; form.inert = false; form.removeAttribute('aria-busy');
      if (!uncertain) buttons.forEach(([button,disabled]) => button.disabled = disabled);
    }
  };
  root.addEventListener('click', event => {
    if (event.defaultPrevented || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const button = event.target.closest('button');
    if (button?.matches('[data-finance-back]')) { event.preventDefault(); back(); return; }
    if (button?.matches('[data-finance-retry]') && pending) { event.preventDefault(); load(pending, {...pendingOptions}); return; }
    if (button?.matches('[data-finance-tab]')) {
      const panel = button.closest('[data-finance-content]');
      ledgerChoices.set(panel.dataset.financeStudent, button.dataset.financeTab);
      ledger(panel, {student:panel.dataset.financeStudent}); return;
    }
    if (button?.matches('[data-print]') && stage.contains(button)) {
      event.preventDefault(); event.stopPropagation(); document.body.classList.add('ff-print-reader'); window.print(); return;
    }
    const link = event.target.closest('a[href]');
    if (!link || link.matches('[data-finance-native],[data-finance-recover]') || link.target || link.hasAttribute('download')) return;
    const target = classify(link.href);
    if (!target) return;
    event.preventDefault(); event.stopPropagation();
    const accountRoot = link.hasAttribute('data-finance-account') || target.view === 'account' && !target.url.search;
    const replace = current?.view === 'account' && target.view === 'account' && current.student === target.student && Boolean(target.url.search);
    load(target, {link, accountRoot, replace});
  }, true);
  root.addEventListener('submit', event => {
    const form = event.target;
    if (event.defaultPrevented || !stage.contains(form) || !form.matches('form[data-finance-form]')) return;
    event.preventDefault(); event.stopPropagation(); submit(form, event.submitter);
  }, true);
  origin.addEventListener('click', event => {
    // The query owner already handles its paging. Keep the global link guard
    // from treating a retained reader draft as a whole-page navigation.
    if (event.defaultPrevented && event.target.closest('[data-query-page]')) event.stopPropagation();
  });
  root.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !reader.hidden && !document.querySelector('dialog[open]')) { event.preventDefault(); back(); }
  });
  const results = origin.querySelector('#query-results');
  const invalidateRead = () => {
    if (posting || !controller && !pending) return;
    cancel(); pending = null; pendingOptions = null; recovery.hidden = true;
    message('Accounts updated. Select an account to continue.');
  };
  origin.querySelectorAll('form[data-query-form]').forEach(form => {
    ['input','change','submit'].forEach(name => form.addEventListener(name, invalidateRead));
  });
  if (results) new MutationObserver(() => {
    invalidateRead();
    if (current) selectAccount(current.student);
  }).observe(results, {childList:true});
  window.addEventListener('afterprint', () => document.body.classList.remove('ff-print-reader'));
  window.addEventListener('popstate', invalidateRead);
  window.addEventListener('pagehide', () => { active = false; cancel(); });
  window.addEventListener('pageshow', event => { active = true; if (event.persisted) location.reload(); });
  desk.classList.add('ff-enabled');
  ledger(origin, classify(location.href));
  if (directory) {
    const first = origin.querySelector('[data-finance-account]');
    if (first) load(classify(first.href), {link:first, accountRoot:true, focus:false, permitted:true});
  }
})();
