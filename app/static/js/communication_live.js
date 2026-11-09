/* Refresh only the authorized content regions of the current workspace. */
window.aelmsModules.register(document.currentScript, function (pageScope) {
  'use strict';
  const selector = '[data-live-part][data-live-url]';
  const account = document.querySelector('meta[name="aelms-device-account"]')?.content;
  if (!account) return;
  const sources = new Map();
  let timer, pending = false;

  function visible(node) {
    return node.isConnected && !node.closest('[hidden],dialog:not([open])') && node.getClientRects().length > 0;
  }

  function sourceUrl(part) {
    if (!part.dataset.liveUrl) return null;
    const url = new URL(part.dataset.liveUrl, location.href);
    if (url.origin !== location.origin) return null;
    url.hash = '';
    return url.href;
  }

  function snapshot(node) {
    const copy = node.cloneNode(true);
    for (const field of copy.querySelectorAll('input[name="csrf_token"],input[name="message_state"]')) field.removeAttribute('value');
    return copy.innerHTML;
  }

  function updateTimeline(current, incoming) {
    const atEnd = current.scrollHeight - current.clientHeight - current.scrollTop < 100;
    const scrollTop = current.scrollTop;
    const viewport = current.getBoundingClientRect();
    const anchor = Array.from(current.querySelectorAll('.messenger-bubble')).find(row => !row.hidden && row.getBoundingClientRect().bottom > viewport.top);
    const anchorTop = anchor?.getBoundingClientRect().top;
    const oldRows = new Map(Array.from(current.children).filter(row => row.id).map(row => [row.id, row]));
    const newMessages = Array.from(incoming.children).some(row => row.matches('.messenger-bubble') && !oldRows.has(row.id));
    const desired = Array.from(incoming.children).map(row => {
      const previous = oldRows.get(row.id);
      if (!previous) return row;
      if (previous.querySelector('.messenger-edit-form')) return previous;
      if (previous.dataset.liveRevision === row.dataset.liveRevision && snapshot(previous) === snapshot(row)) return previous;
      return row;
    });
    if (current.querySelector('.messenger-edit-form') && !desired.some(row => row.querySelector('.messenger-edit-form'))) return false;
    // Keep unchanged rows connected so selection, focus and any editor survive.
    let next = current.firstElementChild;
    for (const row of desired) {
      if (row === next) next = next.nextElementSibling;
      else current.insertBefore(row, next);
    }
    while (next) {
      const after = next.nextElementSibling;
      next.remove();
      next = after;
    }
    if (atEnd) current.scrollTop = current.scrollHeight;
    else if (anchor?.isConnected) current.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
    else current.scrollTop = scrollTop;
    current.dispatchEvent(new CustomEvent('aelms:communication-updated', {bubbles:true, detail:{newMessages, following:atEnd}}));
    return true;
  }

  function updatePart(current, incoming) {
    if (current.dataset.livePart === 'message-timeline') {
      if (current.querySelector('details[open],form[aria-busy="true"]')) return false;
      return updateTimeline(current, incoming);
    }
    if (current.getAttribute('aria-busy') === 'true' || current.closest('[aria-busy="true"]')) return false;
    if (current.contains(document.activeElement) && document.activeElement.matches('input,textarea,select,button')) return false;
    const disclosures = new Map(Array.from(current.querySelectorAll('details[data-live-disclosure]')).map(node => [node.dataset.liveDisclosure, node.open]));
    for (const node of incoming.querySelectorAll('details[data-live-disclosure]')) {
      if (disclosures.has(node.dataset.liveDisclosure)) node.open = disclosures.get(node.dataset.liveDisclosure);
    }
    current.replaceChildren(...incoming.childNodes);
    current.dispatchEvent(new CustomEvent('aelms:communication-updated', {bubbles:true}));
    return true;
  }

  async function pull(url, parts) {
    let source = sources.get(url);
    if (!source || source.parts.length !== parts.length || parts.some((part, index) => source.parts[index] !== part)) {
      source = {parts, signatures:new WeakMap(), etag:null, failures:0, retryAt:0};
      sources.set(url, source);
    }
    if (source.stopped || source.retryAt > Date.now()) return;
    const cadence = Math.min(...parts.map(part => Number(part.dataset.liveInterval) || 1500));
    const headers = {'X-AELMS-Communication':'1'};
    if (parts.some(part => part.dataset.liveTail === 'true')) headers['X-AELMS-Communication-Tail'] = '1';
    if (source.etag) headers['If-None-Match'] = source.etag;
    const timeout = new AbortController();
    const deadline = pageScope.setTimeout(() => timeout.abort(), 9000);
    try {
      const response = await pageScope.fetch(url, {headers, credentials:'same-origin', cache:'no-store', signal:timeout.signal});
      if (response.redirected || [401,403,404,410].includes(response.status) ||
          ((response.ok || response.status === 304) && response.headers.get('X-AELMS-Viewer') !== account)) {
        source.stopped = true;
        return;
      }
      if (response.status === 304) {
        source.retryAt = Date.now() + Math.max(0, cadence - 1500);
        return;
      }
      if (!response.ok || response.headers.get('X-AELMS-Communication') !== '1') throw new Error('Communication refresh unavailable');
      const fragment = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (pageScope.signal.aborted || document.hidden) return;
      const regions = new Map(Array.from(fragment.querySelectorAll('[data-live-part]')).map(part => [part.dataset.livePart, part]));
      let applied = true;
      for (const part of parts) {
        const incoming = regions.get(part.dataset.livePart);
        if (!incoming || !visible(part) || sourceUrl(part) !== url) { applied = false; continue; }
        if (incoming.dataset.liveUnread !== undefined) {
          if (part.closest('[aria-busy="true"]')) { applied = false; continue; }
          part.dataset.liveUnread = incoming.dataset.liveUnread;
          part.dispatchEvent(new CustomEvent('aelms:notification-count', {bubbles:true, detail:Number(incoming.dataset.liveUnread)}));
        }
        const signature = snapshot(incoming);
        const previous = source.signatures.has(part) ? source.signatures.get(part) : snapshot(part);
        if (previous !== signature && !updatePart(part, incoming)) { applied = false; continue; }
        source.signatures.set(part, signature);
      }
      source.etag = applied ? response.headers.get('ETag') : null;
      source.failures = 0;
      source.retryAt = Date.now() + Math.max(0, cadence - 1500);
    } catch (_) {
      source.failures += 1;
      source.retryAt = Date.now() + Math.min(30000, 2000 * 2 ** source.failures);
    } finally {
      pageScope.clearTimeout(deadline);
    }
  }

  async function poll() {
    if (pending || document.hidden || pageScope.signal.aborted) return;
    pageScope.clearTimeout(timer);
    const groups = new Map();
    for (const part of document.querySelectorAll(selector)) {
      if (!visible(part)) continue;
      const url = sourceUrl(part);
      if (!url) continue;
      if (!groups.has(url)) groups.set(url, []);
      groups.get(url).push(part);
    }
    for (const url of sources.keys()) if (!groups.has(url)) sources.delete(url);
    if (!groups.size) return;
    pending = true;
    try {
      await Promise.all(Array.from(groups, ([url, parts]) => pull(url, parts)));
    } finally {
      pending = false;
      if (!document.hidden) timer = pageScope.setTimeout(poll, 1500);
    }
  }

  function wake() {
    if (pending || document.hidden) return;
    pageScope.clearTimeout(timer);
    timer = pageScope.setTimeout(poll, 100);
  }
  pageScope.listen(document, 'visibilitychange', () => {
    if (document.hidden) pageScope.clearTimeout(timer);
    else wake();
  });
  pageScope.listen(window, 'online', () => {
    for (const source of sources.values()) source.retryAt = 0;
    wake();
  });
  pageScope.listen(window, 'pageshow', wake);
  pageScope.listen(document, 'aelms:communication-mounted', wake);
  pageScope.listen(document, 'toggle', event => {
    if (event.target.matches('dialog') && event.target.open) wake();
  }, true);
  wake();
});
