/* Read-only live search: no query storage, collector payload or history change. */
(function () {
  "use strict";
  var dialog = document.querySelector("[data-workspace-search]");
  if (!dialog || !dialog.showModal || !window.fetch || !window.AbortController) return;
  var t = window.aelmsUI ? window.aelmsUI.t : function (value) { return value; };
  var input = dialog.querySelector("[data-workspace-search-query]");
  var results = dialog.querySelector("[data-workspace-search-results]");
  var form = dialog.querySelector("[data-workspace-search-form]");
  var controller, timer, previous, sequence = 0;
  var composing = false;
  function message(text) {
    var node = document.createElement("p");
    node.className = "workspace-search-status";
    node.setAttribute("role", "status");
    node.textContent = t(text);
    results.replaceChildren(node);
  }
  function cancel() {
    clearTimeout(timer);
    sequence += 1;
    if (controller) controller.abort();
    controller = null;
    results.removeAttribute("aria-busy");
  }
  async function load() {
    cancel();
    if (!dialog.open) return;
    var request = new AbortController();
    controller = request;
    var revision = sequence;
    var url = new URL(dialog.dataset.searchUrl, location.origin);
    url.searchParams.set("q", input.value.trim());
    results.setAttribute("aria-busy", "true");
    message("Searching…");
    try {
      var response = await fetch(url.href, {credentials:"same-origin", cache:"no-store", signal:request.signal});
      if (request.signal.aborted || !dialog.open || revision !== sequence) return;
      if (response.redirected || response.status === 401 || response.status === 403) {
        message("Your session may have ended. Reload the page to sign in again."); return;
      }
      if (!response.ok) throw new Error("Search unavailable");
      var documentFragment = new DOMParser().parseFromString(await response.text(), "text/html");
      var fragment = documentFragment.querySelector("[data-workspace-search-fragment]");
      if (!fragment) throw new Error("Search unavailable");
      if (!request.signal.aborted && dialog.open && revision === sequence) results.replaceChildren(fragment);
    } catch (error) {
      if (request.signal.aborted || revision !== sequence) return;
      message("Search could not load. Try again.");
      var retry = document.createElement("button");
      retry.type = "button"; retry.className = "btn btn--secondary"; retry.textContent = t("Try again");
      retry.addEventListener("click", load); results.appendChild(retry);
    } finally {
      if (controller === request) results.removeAttribute("aria-busy");
    }
  }
  function schedule() {
    cancel();
    if (!composing) timer = setTimeout(load, 160);
  }
  document.querySelectorAll("[data-workspace-search-open],a[href='/student/search']").forEach(function (trigger) {
    trigger.addEventListener("click", function (event) {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button > 0) return;
      event.preventDefault();
      previous = trigger;
      document.querySelectorAll("[data-account-menu]").forEach(function (menu) { menu.open = false; });
      document.querySelectorAll("[data-notification-toggle][aria-expanded='true']").forEach(function (button) { button.click(); });
      document.querySelectorAll("[data-more-toggle][aria-expanded='true']").forEach(function (button) { if (button.getClientRects().length) button.click(); });
      dialog.showModal(); input.focus({preventScroll:true}); load();
    });
  });
  input.addEventListener("input", schedule);
  input.addEventListener("compositionstart", function () { composing = true; cancel(); });
  input.addEventListener("compositionend", function () { composing = false; schedule(); });
  form.addEventListener("submit", function (event) { event.preventDefault(); load(); });
  dialog.querySelector("[data-search-close]").addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("keydown", function (event) {
    // Search inputs otherwise consume the first Escape to clear their value.
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dialog.close(); }
    if (event.key === "Tab") {
      var controls = Array.from(dialog.querySelectorAll("a[href],button,input,select,[tabindex='0']"))
        .filter(function (node) { return !node.disabled && node.getClientRects().length; });
      var first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  }, true);
  // Backdrop clicks have the dialog as target; retain clicks within its bounds.
  dialog.addEventListener("click", function (event) {
    if (event.target !== dialog) return;
    var rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  dialog.addEventListener("close", function () {
    cancel(); input.value = ""; results.replaceChildren();
    if (previous && previous.isConnected && previous.getClientRects().length) previous.focus({preventScroll:true});
  });
  window.addEventListener("pagehide", function () { cancel(); if (dialog.open) dialog.close(); });
})();
