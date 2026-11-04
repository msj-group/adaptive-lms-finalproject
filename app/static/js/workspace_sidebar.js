/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
(() => {
  "use strict";
  const toggle = document.querySelector("[data-workspace-sidebar-toggle]");
  if (!toggle) return;
  const sidebar = document.getElementById(toggle.getAttribute("aria-controls"));
  const shell = toggle.closest(".st-shell");
  const root = document.documentElement;
  const desktop = window.matchMedia("(min-width: 1024px)");
  const key = "aelms.sidebar.collapsed";

  function sync() {
    const collapsed = root.dataset.workspaceSidebar === "collapsed";
    sidebar.inert = !desktop.matches || collapsed;
    toggle.hidden = !desktop.matches;
    toggle.setAttribute("aria-expanded", String(desktop.matches && !collapsed));
    const label = collapsed ? toggle.dataset.showLabel : toggle.dataset.hideLabel;
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
  }

  pageScope.listen(toggle, "click", () => {
    const collapsed = root.dataset.workspaceSidebar !== "collapsed";
    shell.classList.add("workspace-sidebar-animated");
    if (collapsed) root.dataset.workspaceSidebar = "collapsed";
    else delete root.dataset.workspaceSidebar;
    try { sessionStorage.setItem(key, String(collapsed)); } catch (_) { /* The current page still responds. */ }
    sync();
  });
  pageScope.listen(desktop, "change", sync);
  pageScope.listen(window, "pageshow", event => {
    if (event.persisted) {
      try {
        if (sessionStorage.getItem(key) === "true") root.dataset.workspaceSidebar = "collapsed";
        else delete root.dataset.workspaceSidebar;
      } catch (_) { /* Preserve the current page state. */ }
    }
    sync();
  });
  sync();
})();

});
