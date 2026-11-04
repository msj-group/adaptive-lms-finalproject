/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
(() => {
  "use strict";

  const scope = pageScript.dataset.scope;
  const root = document.querySelector("body > main");
  if (!scope || !root) return;

  const storageKey = "aelms.workspace-position.v1";
  const stateKey = "aelmsWorkspacePosition";
  const disclosureSelector = "details, [data-workspace-disclosure][aria-controls]";
  const scrollers = new Set();
  const defaults = new Map();
  let signingOut = false;
  const pageState = () => window.navigation?.currentEntry?.getState() || history.state;
  function writeState(state) {
    if (window.navigation?.currentEntry) navigation.updateCurrentEntry({state});
    else history.replaceState(state, "");
  }

  function readPositions() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey));
      if (saved?.scope === scope && Array.isArray(saved.pages)) return saved.pages;
    } catch (_) { /* History still works when storage is unavailable. */ }
    return [];
  }

  // Prefer stable IDs. For other panes, store their tag and path under the main region.
  function address(element) {
    const path = [];
    while (element !== root && !element.id) {
      const parent = element.parentElement;
      if (!parent) return null;
      path.unshift([Array.prototype.indexOf.call(parent.children, element), element.tagName]);
      element = parent;
    }
    return [element === root ? "" : element.id, path];
  }

  function resolve([id, path]) {
    let element = id ? document.getElementById(id) : root;
    if (!root.contains(element)) return null;
    for (const [index, tag] of path) {
      element = element.children[index];
      if (element?.tagName !== tag) return null;
    }
    return element;
  }

  function expanded(element) {
    return element.tagName === "DETAILS" ? element.open : element.getAttribute("aria-expanded") === "true";
  }

  pageScope.listen(document, "DOMContentLoaded", () => {
    for (const element of root.querySelectorAll(disclosureSelector)) {
      if (!element.closest("[data-account-menu]")) defaults.set(element, expanded(element));
    }
  }, {once: true});

  // These named return links can keep filters from the most recent visit to their destination.
  for (const link of root.querySelectorAll("a[data-workspace-return]")) {
    const target = new URL(link.href);
    const previous = readPositions().findLast(saved => saved.route.split("?")[0] === target.pathname);
    if (previous) link.href = previous.route + previous.hash;
  }

  pageScope.listen(root, "scroll", ({target}) => {
    if (target.scrollTop || target.scrollLeft) scrollers.add(target);
    else scrollers.delete(target);
  }, {capture: true, passive: true});

  pageScope.listen(window, "pagehide", () => {
    const route = location.pathname + location.search;
    if (signingOut || route.length > 4096) return;
    const panes = Array.from(scrollers).filter(pane => root.contains(pane) && pane.getClientRects().length)
      .slice(-20).map(pane => [address(pane), pane.scrollLeft, pane.scrollTop]);
    const disclosures = Array.from(defaults).filter(([element, wasOpen]) =>
      root.contains(element) && expanded(element) !== wasOpen
    ).slice(0, 64).map(([element]) => [address(element), expanded(element)]);
    const position = {route, hash: location.hash, x: window.scrollX, y: window.scrollY, panes, disclosures};
    try {
      writeState({...pageState(), [stateKey]: {scope, position}});
    } catch (_) { /* Session storage still handles native return links. */ }
    try {
      const pages = readPositions().filter(saved => saved.route !== route || saved.hash !== location.hash).slice(-23);
      sessionStorage.setItem(storageKey, JSON.stringify({scope, pages: [...pages, position]}));
    } catch (_) { /* Native scrolling remains available. */ }
  });

  pageScope.listen(document, "DOMContentLoaded", () => {
    const route = location.pathname + location.search;
    const entry = pageState()?.[stateKey];
    const exact = entry?.scope === scope && entry.position.route === route && entry.position.hash === location.hash;
    const bookAnchor = location.hash && document.getElementById(location.hash.slice(1))?.closest("[data-book-page]");
    const pageAnchor = bookAnchor?.id === location.hash.slice(1);
    const position = exact ? entry.position : readPositions().findLast(saved =>
      saved.route === route && (pageAnchor || (!bookAnchor && saved.hash === location.hash))
    );
    if (!position) return;
    pageScope.requestAnimationFrame(() => {
      const html = document.documentElement;
      html.setAttribute("data-workspace-restoring", "");
      for (const [key, open] of position.disclosures) {
        if (!key) continue;
        const element = resolve(key);
        if (!element?.matches(disclosureSelector) || expanded(element) === open) continue;
        if (element.tagName === "DETAILS") element.open = open;
        else element.click();
      }
      for (const [key, left, top] of position.panes) {
        const pane = key && resolve(key);
        if (pane && (exact || !pageAnchor || !pane.matches("[data-pdf-viewer]"))) {
          pane.scrollTo({left, top, behavior: "instant"});
        }
      }
      window.scrollTo({left: position.x, top: position.y, behavior: "instant"});
      const {scrollX, scrollY} = window;
      if (document.readyState !== "complete" && (scrollX !== position.x || scrollY !== position.y)) {
        pageScope.listen(window, "load", () => {
          if (window.scrollX === scrollX && window.scrollY === scrollY) {
            window.scrollTo({left: position.x, top: position.y, behavior: "instant"});
          }
        }, {once: true});
      }
      pageScope.requestAnimationFrame(() => html.removeAttribute("data-workspace-restoring"));
    });
  }, {once: true});

  pageScope.listen(document, "submit", event => {
    if (!event.target.matches("[data-account-signout]")) return;
    queueMicrotask(() => {
      if (event.defaultPrevented) return;
      signingOut = true;
      try {
        sessionStorage.removeItem(storageKey);
        const state = {...pageState()};
        delete state[stateKey];
        writeState(state);
      } catch (_) { /* Optional workspace position. */ }
    });
  }, true);
})();

});
