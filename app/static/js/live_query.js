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
      clearTimeout(timer);
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
        var response = await fetch(url.href, {credentials:"same-origin", cache:"no-store", signal:request.signal});
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
      } catch (error) {
        if (!request.signal.aborted && revision === sequence) status.textContent = t("Search could not load. Try again.");
      } finally {
        if (controller === request) controller = null;
      }
    }
    function schedule() {
      cancel();
      if (!composing) timer = setTimeout(load, 300);
    }
    query.addEventListener("input", schedule);
    query.addEventListener("compositionstart", function () { composing = true; cancel(); });
    query.addEventListener("compositionend", function () { composing = false; schedule(); });
    form.addEventListener("change", function (event) { if (event.target !== query) load(); });
    form.addEventListener("submit", function (event) { event.preventDefault(); load(); });
    window.addEventListener("pagehide", cancel);
  });
})();
