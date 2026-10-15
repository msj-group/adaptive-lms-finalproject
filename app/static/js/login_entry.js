/* Enhance native entry links without changing authentication or storing inputs. */
(function () {
  "use strict";
  var navigation = document.querySelector("[data-entry-tabs]");
  if (!navigation) return;
  var tabs = Array.from(navigation.querySelectorAll("[data-entry-tab]"));
  var panels = tabs.map(function (tab) { return document.getElementById(tab.dataset.entryTab); });
  if (panels.some(function (panel) { return !panel; })) return;

  function select(tab) {
    tabs.forEach(function (item, index) {
      var active = item === tab;
      item.setAttribute("aria-selected", String(active));
      item.tabIndex = active ? 0 : -1;
      panels[index].hidden = !active;
    });
  }

  var initial = tabs.find(function (tab) { return tab.getAttribute("aria-current") === "page"; }) || tabs[1];
  navigation.setAttribute("role", "tablist");
  tabs.forEach(function (tab, index) {
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", panels[index].id);
    tab.removeAttribute("aria-current");
    panels[index].setAttribute("role", "tabpanel");
    panels[index].tabIndex = 0;
    tab.addEventListener("click", function (event) {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      select(tab);
    });
    tab.addEventListener("keydown", function (event) {
      var target;
      // The owner's physical order is always placement left, sign-in right.
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") target = tabs[(index + 1) % tabs.length];
      else if (event.key === "Home") target = tabs[0];
      else if (event.key === "End") target = tabs[tabs.length - 1];
      else if (event.key === " ") target = tab;
      if (!target) return;
      event.preventDefault();
      select(target);
      target.focus();
    });
  });
  select(initial);
}());
