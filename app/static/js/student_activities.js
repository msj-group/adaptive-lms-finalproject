/* Presentation sync only; query_filters.js owns the ordinary GET enhancement. */
(function () {
  "use strict";
  var root = document.querySelector("[data-student-activities]");
  if (!root) return;
  var form = root.querySelector("form[data-query-form]");
  var badge = root.querySelector("[data-activity-filter-count]");
  if (!form || !badge || !window.URL) return;
  var t = window.aelmsUI ? window.aelmsUI.t : function (text, values) {
    return text.replace("%(count)d", values.count);
  };
  function sync() {
    var count = Number(Boolean(form.elements.state.value)) + Number(Boolean(form.elements.course.value));
    badge.textContent = String(count);
    badge.hidden = count === 0;
    badge.setAttribute("aria-label", t(count === 1 ? "%(count)d active filter" : "%(count)d active filters", {count:count}));
    root.querySelectorAll("[data-query-type]").forEach(function (link) {
      if ((new URL(link.href)).searchParams.get("type") === (form.elements.type.value || null)) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }
  form.addEventListener("change", sync);
  window.addEventListener("popstate", sync);
  sync();
})();
