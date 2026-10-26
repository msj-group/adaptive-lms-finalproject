/* Read-only profile enhancement. Every action remains a native authorized URL/form. */
(function () {
  'use strict';
  const t = window.aelmsUI ? window.aelmsUI.t : (value) => value;
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!form.closest('[data-people-profile]') || !form.dataset.confirm || window.aelmsConfirm) return;
    if (!window.confirm(t(form.dataset.confirm))) event.preventDefault();
  });
  const desk = document.querySelector('[data-people-desk]');
  if (!desk || !window.fetch || !window.AbortController || !window.DOMParser ||
      !window.MutationObserver || !window.matchMedia || !Element.prototype.replaceChildren) return;
  const region = desk.querySelector('[data-people-results]');
  const form = desk.querySelector('form[data-query-form]');
  const status = desk.querySelector('[data-people-status]');
  const recovery = desk.querySelector('[data-people-recovery]');
  if (!region || !form || !status || !recovery) return;
  const actor = desk.dataset.peopleActor;
  const kind = desk.dataset.peopleKind;
  const base = new URL(form.action || location.href, location.href);
  if (base.origin !== location.origin || !actor || !['student', 'teacher'].includes(kind)) return;
  const phone = window.matchMedia('(max-width: 767px)');
  const filterToggle = desk.querySelector('[data-people-filter-toggle]');
  if (filterToggle) {
    filterToggle.hidden = false;
    form.dataset.peopleFilterReady = 'true';
    filterToggle.addEventListener('click', () => {
      const expanded = filterToggle.getAttribute('aria-expanded') !== 'true';
      filterToggle.setAttribute('aria-expanded', String(expanded));
      form.dataset.peopleFilterOpen = String(expanded);
      if (expanded) form.querySelector('input')?.focus({preventScroll: true});
    });
    form.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || !phone.matches) return;
      filterToggle.setAttribute('aria-expanded', 'false');
      form.dataset.peopleFilterOpen = 'false';
      filterToggle.focus();
    });
  }
  let controller = null;
  let revision = 0;
  let active = true;
  let disabled = false;
  let current = null;
  let workspace = null;
  let reader = null;
  let empty = null;
  let listScroll = 0;

  const message = (text, error = false) => {
    status.textContent = text ? t(text) : '';
    status.hidden = !text;
    status.setAttribute('role', error ? 'alert' : 'status');
  };
  const cancel = () => {
    revision += 1;
    if (controller) controller.abort();
    controller = null;
    region.querySelectorAll('[data-people-open][aria-busy]').forEach((row) => row.removeAttribute('aria-busy'));
    reader?.removeAttribute('aria-busy');
  };
  const profileURL = (link) => {
    if (!link || !region.contains(link) || !link.dataset.personId) return null;
    const url = new URL(link.href, location.href);
    const expected = base.pathname.replace(/\/$/, '') + '/' + encodeURIComponent(link.dataset.personId);
    return url.origin === base.origin && url.pathname === expected && !url.search && !url.hash ? url : null;
  };
  const resetReader = () => {
    cancel();
    delete desk.dataset.peopleSelect;
    current = null;
    recovery.hidden = true;
    if (workspace) delete workspace.dataset.reading;
    region.querySelectorAll('[data-people-open][aria-current]').forEach((row) => row.removeAttribute('aria-current'));
    if (reader && empty) reader.replaceChildren(empty.cloneNode(true));
    message('');
  };
  const load = async (link, automatic = false) => {
    const url = profileURL(link);
    if (!url || disabled || !active || !workspace || !reader) return;
    resetReader();
    const mine = revision;
    const targetReader = reader;
    const request = new AbortController();
    controller = request;
    current = link;
    listScroll = window.scrollY;
    link.setAttribute('aria-busy', 'true');
    targetReader.setAttribute('aria-busy', 'true');
    message('Loading profile…');
    try {
      const response = await fetch(url.href, {credentials: 'same-origin', cache: 'no-store',
        signal: request.signal, headers: {'Accept': 'text/html'}});
      if (!response.ok || response.redirected || !response.headers.get('Content-Type')?.includes('text/html') ||
          response.url !== url.href) throw new Error('unavailable');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (mine !== revision || request.signal.aborted || !active || !link.isConnected || !targetReader.isConnected) return;
      const profile = page.querySelector('[data-people-profile]');
      if (!profile || profile.dataset.personId !== link.dataset.personId || profile.dataset.peopleKind !== kind ||
          profile.querySelector('script,iframe,object,embed')) throw new Error('unavailable');
      if (profile.dataset.peopleActor !== actor) {
        disabled = true;
        throw new Error('scope');
      }
      targetReader.replaceChildren(document.importNode(profile, true));
      targetReader.scrollTop = 0;
      workspace.dataset.reading = 'true';
      link.setAttribute('aria-current', 'true');
      targetReader.classList.add('pf-reading-enter');
      targetReader.addEventListener('animationend', () => targetReader.classList.remove('pf-reading-enter'), {once: true});
      message('');
      if (!automatic) {
        if (phone.matches) targetReader.scrollIntoView({block: 'start', behavior: 'instant'});
        targetReader.querySelector('[data-people-profile-heading]')?.focus({preventScroll: true});
      }
    } catch (error) {
      if (mine !== revision || request.signal.aborted || !active) return;
      message(disabled ? 'Your session changed. Reload this page to continue.' :
        'The profile could not load. Retry or open the full profile.', true);
      recovery.hidden = false;
      recovery.querySelector('[data-people-retry]').hidden = disabled;
      recovery.querySelector('[data-people-recovery-link]').href = url.href;
    } finally {
      if (mine === revision) {
        link.removeAttribute('aria-busy');
        targetReader.removeAttribute('aria-busy');
        controller = null;
      }
    }
  };
  const install = () => {
    cancel();
    workspace = region.querySelector('[data-people-workspace]');
    reader = workspace?.querySelector('[data-people-reader]');
    current = null;
    recovery.hidden = true;
    message('');
    if (!workspace || !reader) return;
    if (workspace.dataset.peopleActor !== actor || workspace.dataset.peopleKind !== kind) {
      disabled = true;
      reader.replaceChildren();
      message('Your session changed. Reload this page to continue.', true);
      return;
    }
    empty = reader.firstElementChild?.cloneNode(true);
    const selected = desk.dataset.peopleSelect;
    delete desk.dataset.peopleSelect;
    const preferred = selected && Array.from(region.querySelectorAll('[data-people-open]')).find((link) => link.dataset.personId === selected);
    if (!disabled && (preferred || !phone.matches)) {
      const first = preferred || region.querySelector('[data-people-open]');
      if (first) load(first, true);
    }
  };
  desk.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[data-people-open]');
    if (link && !disabled && profileURL(link)) {
      event.preventDefault();
      load(link);
    }
    const back = event.target.closest('a[data-people-back]');
    if (back && workspace?.contains(back) && phone.matches) {
      event.preventDefault();
      const previous = current;
      cancel();
      delete workspace.dataset.reading;
      message('');
      previous?.focus({preventScroll: true});
      window.scrollTo({top: listScroll, behavior: 'instant'});
    }
    if (event.target.closest('[data-people-retry]') && current) load(current);
  });
  form.addEventListener('input', resetReader);
  form.addEventListener('change', resetReader);
  form.addEventListener('submit', resetReader);
  const observer = new MutationObserver(install);
  observer.observe(region, {childList: true});
  phone.addEventListener('change', () => {
    if (!phone.matches && !current && !disabled) {
      const first = region.querySelector('[data-people-open]');
      if (first) load(first, true);
    }
  });
  window.addEventListener('pagehide', () => {active = false; cancel();});
  window.addEventListener('pageshow', (event) => {
    active = true;
    if (event.persisted) {
      resetReader();
      install();
    }
  });
  install();
})();
