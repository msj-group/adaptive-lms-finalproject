/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Progressive live results for ordinary GET search forms. The server remains
   authoritative for filtering, role scope and the rendered result links. */
(function () {
  "use strict";
  if (!window.fetch || !window.AbortController || !window.DOMParser) return;
  var t = window.aelmsUI ? window.aelmsUI.t : function (value) { return value; };
  document.querySelectorAll("form[data-live-query]").forEach(function (form) {
    if (form.hasAttribute("data-query-form") || (form.getAttribute("method") || "get").toLowerCase() !== "get") return;
    var query = form.querySelector("input[name='q']");
    if (!query) return;
    var status = document.createElement("span");
    status.className = "workspace-live-query-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    form.appendChild(status);
    var timer, controller, sequence = 0, composing = false;
    function cancel() {
      pageScope.clearTimeout(timer);
      sequence += 1;
      if (controller) controller.abort();
      controller = null;
    }
    async function load() {
      cancel();
      var request = new AbortController();
      controller = request;
      var revision = sequence;
      var url = new URL(form.getAttribute("action") || location.pathname, location.origin);
      url.search = new URLSearchParams(new FormData(form)).toString();
      status.textContent = t("Searching…");
      try {
        var response = await pageScope.fetch(url.href, {credentials:"same-origin", cache:"no-store", signal:request.signal});
        if (request.signal.aborted || revision !== sequence) return;
        if (response.redirected || response.status === 401 || response.status === 403) {
          status.textContent = t("Your session may have ended. Reload the page to sign in again.");
          return;
        }
        if (!response.ok) throw new Error("Search unavailable");
        var page = new DOMParser().parseFromString(await response.text(), "text/html");
        var incoming = page.querySelector("form[data-live-query]");
        if (!incoming || !incoming.parentNode || request.signal.aborted || revision !== sequence) throw new Error("Search unavailable");
        var actions = form.querySelector("[data-live-query-actions]");
        var incomingActions = incoming.querySelector("[data-live-query-actions]");
        var extraSelector = form.dataset.liveQueryExtra;
        var extraParent = extraSelector && form.closest(".messenger-inbox-list");
        var currentExtra = extraParent && extraParent.querySelector(extraSelector);
        var incomingExtra = extraParent && page.querySelector(extraSelector);
        var fragment = document.createDocumentFragment();
        while (incoming.nextSibling) fragment.appendChild(incoming.nextSibling);
        while (form.nextSibling) form.nextSibling.remove();
        form.parentNode.appendChild(fragment);
        if (actions && incomingActions) actions.replaceChildren(...Array.from(incomingActions.childNodes));
        form.querySelectorAll("[data-live-query-clear]").forEach(function (link) { link.remove(); });
        incoming.querySelectorAll("[data-live-query-clear]").forEach(function (link) { form.insertBefore(link, status); });
        if (extraParent) {
          if (currentExtra && incomingExtra) currentExtra.replaceWith(incomingExtra);
          else if (currentExtra) currentExtra.remove();
          else if (incomingExtra) extraParent.appendChild(incomingExtra);
        }
        if (window.history && window.history.replaceState) history.replaceState(null, "", url.href);
        status.textContent = t("Results updated.");
        document.dispatchEvent(new Event("aelms:communication-mounted"));
      } catch (error) {
        if (!request.signal.aborted && revision === sequence) status.textContent = t("Search could not load. Try again.");
      } finally {
        if (controller === request) controller = null;
      }
    }
    function schedule() {
      cancel();
      if (!composing) timer = pageScope.setTimeout(load, 300);
    }
    pageScope.listen(query, "input", schedule);
    pageScope.listen(query, "compositionstart", function () { composing = true; cancel(); });
    pageScope.listen(query, "compositionend", function () { composing = false; schedule(); });
    pageScope.listen(form, "change", function (event) { if (event.target !== query) load(); });
    pageScope.listen(form, "submit", function (event) { event.preventDefault(); load(); });
    pageScope.listen(window, "pagehide", cancel);
  });
})();

});
