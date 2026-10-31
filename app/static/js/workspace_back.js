/* Native Back links keep all existing save/leave guards. Browser history owns
   the bounded trail; a consumed, two-minute session bridge joins documents.
   Only visited page URLs are retained, never form fields or page content. */
(function () {
  "use strict";
  var shell = document.querySelector('[data-workspace-history]');
  if (!shell) return;
  var config;
  try { config = JSON.parse(shell.dataset.workspaceHistory); } catch (_) { return; }
  if (!config.scope) return;
  var key = 'aelms.navigation-bridge.v1';
  var stateKey = 'aelmsWorkspaceBack';
  var limit = 24;
  var trail = [];
  var fallback = new Map();
  document.querySelectorAll('[data-workspace-back]').forEach(function (link) { fallback.set(link, link.href); });

  function local(value) {
    if (typeof value !== 'string' || value.length > 4096 || !value.startsWith('/') || value.startsWith('//') || /[\\\x00-\x1f]/.test(value)) return null;
    try {
      var url = new URL(value, location.origin);
      if (url.origin !== location.origin) return null;
      return url.pathname + url.search;
    } catch (_) { return null; }
  }
  function here() { return location.pathname + location.search; }
  function clean(items) {
    return Array.isArray(items) ? items.slice(-limit).map(local).filter(Boolean) : [];
  }
  function consumeBridge() {
    try {
      var bridge = JSON.parse(sessionStorage.getItem(key) || 'null');
      sessionStorage.removeItem(key);
      if (bridge && bridge.scope === config.scope && bridge.get &&
          Date.now() >= bridge.time && Date.now() - bridge.time < 120000 &&
          local(config.previous) && local(bridge.url) === local(config.previous)) return bridge;
    } catch (_) { /* Storage-disabled pages retain native parent navigation. */ }
    return null;
  }
  function writeState() {
    try {
      var state = Object.assign({}, history.state);
      state[stateKey] = {scope:config.scope, trail:trail};
      history.replaceState(state, '');
    } catch (_) { /* Keep the native link available. */ }
  }
  function paint() {
    // Returning to any ancestor consumes it and its descendants. A native link
    // may create a browser entry, but repeated workspace Back never ping-pongs.
    var existing = trail.indexOf(here());
    if (existing !== -1) trail = trail.slice(0, existing);
    document.querySelectorAll('[data-workspace-back]').forEach(function (link) {
      link.href = trail.length ? trail[trail.length - 1] : fallback.get(link);
    });
    writeState();
  }
  var bridge = consumeBridge();
  var saved = history.state && history.state[stateKey];
  if (saved && saved.scope === config.scope) trail = clean(saved.trail);
  else {
    if (bridge) trail = clean(bridge.trail).concat([local(bridge.url)]).filter(Boolean).slice(-limit);
  }
  paint();
  window.addEventListener('pageshow', function () {
    consumeBridge();
    var restored = history.state && history.state[stateKey];
    if (restored && restored.scope === config.scope) trail = clean(restored.trail);
    paint();
  });
  window.addEventListener('popstate', function () {
    var restored = history.state && history.state[stateKey];
    if (restored && restored.scope === config.scope) trail = clean(restored.trail);
    paint();
  });
  var leavingAccount = false;
  document.querySelectorAll('[data-account-signout]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      queueMicrotask(function () {
        if (event.defaultPrevented) return;
        leavingAccount = true;
        trail = [];
        writeState();
        try { sessionStorage.removeItem(key); } catch (_) { /* Optional UI state. */ }
      });
    });
  });
  window.addEventListener('pagehide', function () {
    if (leavingAccount) return;
    paint();
    try { sessionStorage.setItem(key, JSON.stringify({scope:config.scope, get:config.get,
      url:here(), trail:trail, time:Date.now()})); } catch (_) { /* Native fallback. */ }
  });
})();
