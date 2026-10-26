/* Optional read-only reader. Native authorized GET URLs remain the fallback. */
(function () {
  'use strict';
  const desk = document.querySelector('[data-learning-workspace]');
  if (!desk || !window.fetch || !window.AbortController || !window.DOMParser ||
      !window.MutationObserver || !Element.prototype.replaceChildren) return;
  const origin = desk.querySelector('[data-learning-content]');
  const reader = desk.querySelector('[data-learning-reader]');
  const slot = desk.querySelector('[data-learning-slot]');
  const status = desk.querySelector('[data-learning-status]');
  const recovery = desk.querySelector('[data-learning-recovery]');
  const actor = desk.dataset.learningActor;
  if (!origin || !reader || !slot || !status || !recovery || !actor) return;
  const t = window.aelmsUI ? window.aelmsUI.t : (value) => value;
  const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
  const paths = {group: desk.dataset.learningGroupUrl, session: desk.dataset.learningSessionUrl,
    gradebook: desk.dataset.learningGradebookUrl};
  const stack = [];
  let controller = null, revision = 0, active = true, disabled = false, pendingLink = null;

  const message = (text, error = false) => {
    status.textContent = text ? t(text) : '';
    status.hidden = !text;
    status.setAttribute('role', error ? 'alert' : 'status');
  };
  const cancel = () => {
    revision += 1;
    controller?.abort();
    controller = null;
    desk.querySelectorAll('[aria-busy]').forEach((node) => {
      if (node === reader || node.matches('[data-learning-open],.al-pager a')) node.removeAttribute('aria-busy');
    });
  };
  const targetFor = (link) => {
    if (!link || !desk.contains(link)) return null;
    let kind = link.dataset.learningOpen, group = link.dataset.learningGroup;
    let session = link.dataset.learningSession || '';
    const content = link.closest('[data-learning-content]');
    const paging = !kind && link.closest('.al-pager') && content?.dataset.learningKind === 'group';
    if (paging) { kind = 'group'; group = content.dataset.learningGroup; }
    if (!paths[kind] || !uuid.test(group || '') || kind === 'session' && !uuid.test(session)) return null;
    const url = new URL(link.href, location.href);
    const expected = paths[kind].replace('__group__', encodeURIComponent(group)).replace('__session__', encodeURIComponent(session));
    if (url.origin !== location.origin || url.pathname !== expected || url.hash) return null;
    if (paging) {
      if ([...url.searchParams.keys()].some((key) => key !== 'page') ||
          url.searchParams.getAll('page').length !== 1 || !/^[1-9]\d*$/.test(url.searchParams.get('page'))) return null;
    } else if (url.search) return null;
    return {url, kind, group, session};
  };
  const reset = () => {
    cancel(); stack.length = 0; slot.replaceChildren(); reader.hidden = true;
    origin.hidden = disabled; pendingLink = null; recovery.hidden = true; message('');
  };
  const back = () => {
    cancel(); pendingLink = null; recovery.hidden = true; message('');
    const previous = stack.pop();
    if (!previous) { reset(); return; }
    if (previous.origin) {
      slot.replaceChildren(); reader.hidden = true; origin.hidden = false;
    } else slot.replaceChildren(...previous.nodes);
    previous.focus?.focus({preventScroll: true});
    window.scrollTo({top: previous.scroll, behavior: 'instant'});
  };
  const load = async (link) => {
    const target = targetFor(link);
    if (!target || disabled || !active || !link.isConnected) return;
    if (link.closest('#query-results[aria-busy="true"]')) {
      message('Updating results. Previous results are still shown.');
      return;
    }
    cancel(); recovery.hidden = true; pendingLink = link;
    const mine = revision, request = new AbortController();
    controller = request;
    link.setAttribute('aria-busy', 'true'); reader.setAttribute('aria-busy', 'true');
    message('Loading details…');
    try {
      const response = await fetch(target.url.href, {credentials: 'same-origin', cache: 'no-store',
        signal: request.signal, headers: {'Accept': 'text/html'}});
      if (!response.ok || response.redirected || response.url !== target.url.href ||
          !response.headers.get('Content-Type')?.includes('text/html')) throw new Error('unavailable');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (mine !== revision || request.signal.aborted || !active || !link.isConnected) return;
      const workspaces = page.querySelectorAll('[data-learning-workspace]');
      if (workspaces.length !== 1) throw new Error('unavailable');
      if (workspaces[0].dataset.learningActor !== actor) {
        disabled = true; origin.hidden = true; slot.replaceChildren(); stack.length = 0;
        reader.hidden = true;
        throw new Error('scope');
      }
      const contents = workspaces[0].querySelectorAll('[data-learning-content]');
      const content = contents[0];
      if (contents.length !== 1 || content.dataset.learningKind !== target.kind ||
          content.dataset.learningGroup !== target.group || content.dataset.learningSession !== target.session ||
          content.querySelector('script,iframe,object,embed,form')) throw new Error('unavailable');
      const imported = document.importNode(content, true);
      // Native context links remain on directly opened pages, outside the inline reader.
      imported.querySelectorAll('[data-learning-native-only]').forEach((node) => node.remove());
      stack.push({origin: !origin.hidden, nodes: Array.from(slot.childNodes), focus: link, scroll: window.scrollY});
      if (stack.length > 6) stack.splice(1, 1); // Keep the original list and a bounded recent trail.
      slot.replaceChildren(imported); origin.hidden = true; reader.hidden = false;
      pendingLink = null; message('');
      reader.scrollIntoView({block: 'start', behavior: 'instant'});
      const heading = slot.querySelector('h2');
      if (heading) { heading.tabIndex = -1; heading.focus({preventScroll: true}); }
    } catch (error) {
      if (mine !== revision || request.signal.aborted || !active) return;
      message(disabled ? 'Your session changed. Reload this page to continue.' :
        'Details could not load. Retry or open the full page.', true);
      recovery.hidden = false;
      recovery.querySelector('[data-learning-retry]').hidden = disabled;
      const nativeLink = recovery.querySelector('[data-learning-recovery-link]');
      nativeLink.href = disabled ? location.href : target.url.href;
      nativeLink.textContent = t(disabled ? 'Reload page' : 'Open full page');
      status.scrollIntoView({block: 'center', behavior: 'instant'});
    } finally {
      if (mine === revision) {
        link.removeAttribute('aria-busy'); reader.removeAttribute('aria-busy'); controller = null;
      }
    }
  };
  desk.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a');
    if (link && !disabled && targetFor(link)) { event.preventDefault(); load(link); }
    else if (event.target.closest('[data-learning-back]')) back();
    else if (event.target.closest('[data-learning-retry]') && pendingLink) load(pendingLink);
  });
  desk.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !reader.hidden) { event.preventDefault(); back(); }
  });
  // Keep the existing filter/fragment owner; stale reads never re-open over new results.
  origin.querySelectorAll('form').forEach((form) => {
    ['input', 'change', 'submit'].forEach((name) => form.addEventListener(name, reset));
  });
  const results = origin.querySelector('#query-results');
  if (results) new MutationObserver(reset).observe(results, {childList: true});
  window.addEventListener('popstate', reset);
  window.addEventListener('pagehide', () => { active = false; reset(); });
  window.addEventListener('pageshow', (event) => {
    active = true;
    if (event.persisted) location.reload(); // Re-authorize restored private content.
  });
})();
