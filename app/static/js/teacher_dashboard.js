/* Presentation-only tabs: no fetch, storage, history, writes or synthetic events.
   Without this enhancement both ordinary linked sections remain available. */
(function () {
  "use strict";
  var root = document.querySelector("[data-teacher-dashboard]");
  if (!root) return;
  var tabs = root.querySelector("[data-cockpit-tabs]");
  var buttons = Array.from(root.querySelectorAll("[data-cockpit-tab]"));
  var panels = Array.from(root.querySelectorAll("[data-cockpit-panel]"));
  var announcements = root.querySelector("[data-cockpit-announcements]");
  if (!tabs || buttons.length !== 2 || panels.length !== 2) return;
  var selected = "groups", mobile = false;

  function activate(name, focusTab) {
    selected = name;
    buttons.forEach(function (button) {
      var active = button.dataset.cockpitTab === selected;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      if (active && focusTab) button.focus();
    });
    panels.forEach(function (panel) {
      var hidden = mobile && panel.dataset.cockpitPanel !== selected;
      if (hidden && panel.contains(document.activeElement)) {
        buttons.find(function (button) { return button.dataset.cockpitTab === selected; }).focus();
      }
      panel.hidden = hidden;
    });
    if (announcements) announcements.hidden = mobile && selected === "feedback";
  }

  function layout(width) {
    var compact = width <= 740;
    if (compact === mobile && root.hasAttribute("data-tabbed")) return;
    if (compact && !mobile && panels[1].contains(document.activeElement)) selected = "feedback";
    if (compact && !mobile && announcements && announcements.contains(document.activeElement)) selected = "groups";
    mobile = compact;
    root.dataset.tabbed = String(mobile);
    if (mobile) {
      tabs.hidden = false;
      tabs.setAttribute("role", "tablist");
      buttons.forEach(function (button, index) {
        button.setAttribute("role", "tab");
        button.setAttribute("aria-controls", panels[index].id);
        panels[index].setAttribute("role", "tabpanel");
        panels[index].setAttribute("aria-labelledby", button.id);
        panels[index].tabIndex = 0;
      });
      activate(selected, false);
    } else {
      var focusWasTab = tabs.contains(document.activeElement);
      tabs.hidden = true;
      tabs.removeAttribute("role");
      buttons.forEach(function (button) {
        button.removeAttribute("role");
        button.removeAttribute("aria-controls");
        button.removeAttribute("aria-selected");
        button.removeAttribute("tabindex");
      });
      panels.forEach(function (panel) {
        panel.hidden = false;
        panel.removeAttribute("role");
        panel.setAttribute("aria-labelledby", panel.dataset.cockpitPanel === "groups" ? "cockpit-groups-title" : "cockpit-feedback-title");
        panel.removeAttribute("tabindex");
      });
      if (announcements) announcements.hidden = false;
      if (focusWasTab) root.querySelector(selected === "groups" ? "#cockpit-groups-title" : "#cockpit-feedback-title").focus({preventScroll:true});
    }
  }

  buttons.forEach(function (button, index) {
    button.addEventListener("click", function () {
      if (mobile) activate(button.dataset.cockpitTab, false);
    });
    button.addEventListener("keydown", function (event) {
      if (!mobile) return;
      var next;
      if (event.key === "Home") next = 0;
      else if (event.key === "End") next = buttons.length - 1;
      else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        var forward = event.key === "ArrowRight";
        if (getComputedStyle(root).direction === "rtl") forward = !forward;
        next = (index + (forward ? 1 : -1) + buttons.length) % buttons.length;
      } else return;
      event.preventDefault();
      activate(buttons[next].dataset.cockpitTab, true);
    });
  });
  layout(root.getBoundingClientRect().width);
  if (window.ResizeObserver) {
    new ResizeObserver(function (entries) { layout(entries[0].contentRect.width); }).observe(root);
  } else {
    window.addEventListener("resize", function () { layout(root.getBoundingClientRect().width); });
  }
}());
