/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Local content selection only. Native links, forms and server guards own operations. */
(function () {
  "use strict";
  document.querySelectorAll("[data-course-studio] .cs-add-material").forEach(function (menu) {
    pageScope.listen(menu, "keydown", function (event) {
      if (event.key !== "Escape" || !menu.open) return;
      event.preventDefault();
      menu.open = false;
      menu.querySelector("summary").focus();
    });
    pageScope.listen(document, "click", function (event) {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
  });
  document.querySelectorAll("[data-course-studio] [data-studio-selection]").forEach(function (root) {
    var links = Array.from(root.querySelectorAll("[data-studio-target]"));
    var panels = Array.from(root.querySelectorAll("[data-studio-pane]"));
    if (!links.length || !panels.length) return;
    function select(id) {
      var selected = panels.find(function (panel) { return panel.id === id; });
      if (!selected) return;
      panels.forEach(function (panel) { panel.hidden = panel !== selected; });
      links.forEach(function (link) {
        if (link.dataset.studioTarget === id) link.setAttribute("aria-current", "true");
        else link.removeAttribute("aria-current");
      });
    }
    var hash = window.location.hash.slice(1);
    select(panels.some(function (panel) { return panel.id === hash; }) ? hash : panels[0].id);
    root.dataset.studioEnhanced = "true";
    links.forEach(function (link) {
      pageScope.listen(link, "click", function (event) {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        select(link.dataset.studioTarget);
      });
    });
    pageScope.listen(window, "hashchange", function () { select(window.location.hash.slice(1)); });
  });
})();

});
