/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope) {
  "use strict";
  pageScope.listen(document, "DOMContentLoaded", () => {
    const host = document.querySelector("[data-workspace-page-context]");
    const root = document.getElementById("workspace-content");
    if (!host || !root) return;
    const title = host.querySelector("[data-workspace-header-title]");
    const filterHost = document.querySelector("[data-workspace-header-filters]");
    if (!filterHost) return;
    const fallback = title.textContent.trim();
    const t = window.aelmsUI.t;
    const shells = new Set();
    const defaultIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    Object.entries({viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.7",
      "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false"})
      .forEach(([name, value]) => defaultIcon.setAttribute(name, value));
    defaultIcon.innerHTML = '<path d="M4 7h2m6 0h8M4 17h8m6 0h2"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>';
    let filter;
    let scheduled = false;
    document.body.dataset.workspaceHeaderTitles = "true";
    host.hidden = false;

    function plainTitle(heading) {
      const copy = heading.cloneNode(true);
      const originalChildren = heading.querySelectorAll("*");
      const copiedChildren = copy.querySelectorAll("*");
      originalChildren.forEach((node, index) => {
        if (getComputedStyle(node).display === "none") copiedChildren[index].remove();
      });
      copy.querySelectorAll(".figma-brand-dot,[aria-hidden=true]").forEach(node => node.remove());
      return copy.textContent.replace(/\s+/g, " ").trim();
    }

    function hideDescription(node) {
      if (!node.matches("p") || node.matches('[role=alert],[role=status],[aria-live]') ||
          node.querySelector("a,button,input,select,textarea") || node.closest("[data-header-filter-panel]")) return;
      node.classList.add("workspace-heading-description");
    }

    function updateTitle() {
      const candidates = Array.from(root.querySelectorAll("[data-workspace-page-heading],.st-page-heading h1,.agenda-greeting h1,.cockpit-greeting h1"))
        .filter(node => !node.closest("dialog,[popover]"));
      const primary = candidates.find(node => node.hasAttribute("data-workspace-page-heading")) || candidates[0];
      const headings = candidates.filter(node => node === primary || node.closest(".st-page-heading"));
      const label = fallback || (headings.length ? plainTitle(headings[0]) : "");
      if (title.textContent !== label) title.textContent = label;
      title.title = label;
      const back = document.querySelector('.st-header [data-workspace-back]');
      if (back) root.querySelectorAll('header a').forEach(link => {
        if (link.textContent.trim() === t('Back') && link.href === back.href) {
          link.classList.add('workspace-heading-description');
        }
      });
      for (const heading of headings) {
        heading.classList.add("workspace-heading-source");
        heading.setAttribute("aria-hidden", "true");
        const shell = heading.closest("header,.teacher-groups-heading,.ta-heading,.review-heading,.tat-heading,.record-page-heading,.workspace-heading,.tgb-create-intro");
        if (shell && root.contains(shell)) {
          shells.add(shell);
          for (let parent = heading.parentElement; ; parent = parent.parentElement) {
            Array.from(parent.children).forEach(hideDescription);
            if (parent === shell) break;
            shells.add(parent);
          }
        } else {
          hideDescription(heading.previousElementSibling || heading);
          hideDescription(heading.nextElementSibling || heading);
        }
      }
      root.querySelectorAll(".teacher-groups-intro,.ta-intro,.review-intro,.tat-intro,.tan-intro").forEach(hideDescription);
    }

    function trimShells() {
      for (const shell of shells) {
        if (!root.contains(shell)) { shells.delete(shell); continue; }
        const copy = shell.cloneNode(true);
        copy.querySelectorAll(".workspace-heading-source,.workspace-heading-description,[data-header-filter-source],[data-header-filter-control],[data-header-filter-panel],[hidden],script,template").forEach(node => node.remove());
        const empty = !copy.textContent.trim() && !copy.querySelector("a,button,input,select,textarea,img,svg,.badge");
        shell.classList.toggle("workspace-heading-shell-empty", empty);
        shell.classList.toggle("workspace-heading-shell-compact", !empty && !copy.querySelector("a,button,input,select,textarea,nav,dl"));
      }
    }

    function findFilter() {
      const details = Array.from(root.querySelectorAll("details")).find(node => {
        if (node.closest("dialog")) return false;
        const summary = node.querySelector(":scope > summary");
        return summary && (/filter/i.test(node.className + " " + summary.textContent) ||
          summary.textContent.includes(t("Filters")));
      });
      if (details) {
        const panel = details.querySelector(":scope > form,:scope > div");
        if (panel) return {details, panel, summary: details.querySelector(":scope > summary")};
      }
      const control = root.querySelector("[data-people-filter-toggle]");
      if (control) {
        const panel = document.getElementById(control.getAttribute("aria-controls"));
        if (panel) return {control, panel};
      }
      const panel = Array.from(root.querySelectorAll('form[method="get"]')).find(form =>
        !form.closest("dialog") && form.querySelector("select,input[type=date]") &&
        /filter/.test(form.className));
      return panel ? {panel} : null;
    }

    function positionPanel() {
      if (!filter) return;
      const rect = filter.button.getBoundingClientRect();
      const width = Math.min(440, innerWidth - 24);
      const left = Math.max(12, Math.min(document.documentElement.dir === "rtl" ? rect.right - width : rect.left, innerWidth - width - 12));
      filter.panel.style.setProperty("--header-filter-left", left + "px");
      filter.panel.style.setProperty("--header-filter-top", Math.min(rect.bottom + 8, innerHeight - 80) + "px");
    }

    function syncFilter() {
      if (!filter) return;
      const open = filter.panel.matches(":popover-open");
      filter.button.setAttribute("aria-expanded", String(open));
      if (filter.details && filter.details.open !== open) filter.details.open = open;
      if (filter.control) {
        filter.control.setAttribute("aria-expanded", String(open));
        filter.panel.dataset.peopleFilterOpen = String(open);
      }
      if (open) positionPanel();
    }

    function filterLabel() {
      if (!filter) return;
      const reference = filter.summary || filter.control;
      const icon = reference?.querySelector("svg") || defaultIcon;
      const label = document.createElement("span");
      label.className = "workspace-filter-label";
      label.textContent = t("Filters");
      const content = [icon.cloneNode(true), label];
      const badge = reference?.querySelector('[class*="count"]');
      if (badge && !badge.hidden) {
        const copy = badge.cloneNode(true);
        for (const attribute of Array.from(copy.attributes)) {
          if (attribute.name === "id" || attribute.name.startsWith("data-")) copy.removeAttribute(attribute.name);
        }
        content.push(copy);
      }
      filter.button.replaceChildren(...content);
    }

    function prepareFilter() {
      if (filter && root.contains(filter.panel)) { filterLabel(); return; }
      if (filter?.panel.matches(":popover-open")) filter.panel.hidePopover();
      filterHost.replaceChildren();
      filterHost.hidden = true;
      filter = findFilter();
      if (!filter || typeof filter.panel.showPopover !== "function") { filter = null; return; }
      const current = filter;
      const {panel, details, control} = current;
      if (!panel.id) panel.id = "workspace-header-filter-panel";
      panel.dataset.headerFilterPanel = "";
      panel.popover = "auto";
      panel.hidden = false;
      if (details) {
        details.dataset.headerFilterSource = "";
        details.open = false;
        root.querySelectorAll('[aria-controls="' + details.id + '"]').forEach(button => { button.dataset.headerFilterControl = ""; });
      }
      if (control) control.dataset.headerFilterControl = "";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "workspace-header-filter";
      button.dataset.workspaceFilterToggle = "";
      button.setAttribute("aria-label", t("Filters"));
      button.setAttribute("title", t("Filters"));
      button.setAttribute("aria-controls", panel.id);
      button.setAttribute("aria-expanded", "false");
      button.popoverTargetElement = panel;
      current.button = button;
      filterHost.append(button);
      filterHost.hidden = false;
      filterLabel();
      pageScope.listen(button, "click", event => {
        // Feature clients close their disclosure on outside clicks. The new
        // trigger is part of that disclosure's UI, so keep this click local.
        event.stopPropagation();
        if (!panel.matches(":popover-open")) {
          if (details) details.open = true;
          if (control) { control.setAttribute("aria-expanded", "true"); panel.dataset.peopleFilterOpen = "true"; }
          positionPanel();
        }
      });
      pageScope.listen(panel, "toggle", () => {
        if (filter !== current) return;
        syncFilter();
      });
      if (details) pageScope.listen(details, "toggle", () => {
        if (!details.open && panel.matches(":popover-open")) {
          const restoreFocus = panel.contains(document.activeElement);
          panel.hidePopover();
          if (restoreFocus) button.focus({preventScroll: true});
        }
      });
      if (control) pageScope.createObserver(MutationObserver, () => {
        if (control.getAttribute("aria-expanded") === "false" && panel.matches(":popover-open")) {
          const restoreFocus = panel.contains(document.activeElement);
          panel.hidePopover();
          if (restoreFocus) button.focus({preventScroll: true});
        }
      }).observe(control, {attributes: true, attributeFilter: ["aria-expanded"]});
    }

    function refresh() {
      scheduled = false;
      prepareFilter();
      updateTitle();
      trimShells();
    }
    refresh();
    pageScope.createObserver(MutationObserver, () => {
      if (scheduled) return;
      scheduled = true;
      pageScope.requestAnimationFrame(refresh);
    }).observe(root, {subtree: true, childList: true, characterData: true, attributes: true,
      attributeFilter: ["hidden"]});
    pageScope.listen(window, "resize", positionPanel);
    pageScope.listen(document, "scroll", positionPanel, {capture: true, passive: true});
  }, {once: true});
});
