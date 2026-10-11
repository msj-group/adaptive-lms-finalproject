/* Opt-in read-only GET enhancement. Native forms remain the recovery path. */
(function () {
  "use strict";
  var t = window.aelmsUI ? window.aelmsUI.t : function (s) { return s; };
  if (!window.fetch || !window.AbortController || !window.FormData ||
      !window.URLSearchParams || !window.queueMicrotask || !window.URL ||
      !Element.prototype.replaceChildren || !window.history || !history.replaceState || !history.pushState) return;

  document.querySelectorAll("form[data-query-form]").forEach(function (form) {
    if ((form.getAttribute("method") || "get").toLowerCase() !== "get") return;
    var region = document.getElementById(form.dataset.queryResults);
    if (!region) return;
    var base = new URL(form.action || location.href, location.href);
    if (base.origin !== location.origin) return;
    var submit = form.querySelector("[data-query-submit]");
    if (!submit) return;
    var status = document.createElement("p");
    status.className = "workspace-meta";
    status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    status.hidden = true; form.insertAdjacentElement("afterend", status);
    var retry = document.createElement("button"); retry.type = "button";
    retry.className = "btn btn--secondary"; retry.textContent = t("Retry"); retry.hidden = true;
    status.insertAdjacentElement("afterend", retry);
    var controller, timer, revision = 0, composing = false, queued = false;
    var committed = new URL(location.href).href;
    var inputs = Array.from(form.elements).filter(function (e) { return e.name && !e.disabled; });
    var defaults = {};
    inputs.forEach(function (e) {
      defaults[e.name] = e.tagName === "SELECT" ? e.options[0].value : e.type === "hidden" ? e.value : "";
    });
    function invalidate() {
      revision++; clearTimeout(timer);
      if (controller) controller.abort(); controller = null;
    }
    function pending() {
      region.setAttribute("aria-busy", "true"); status.hidden = false;
      status.textContent = t("Updating results. Previous results are still shown.");
    }
    function urlForForm() {
      var url = new URL(base); url.search = "";
      new FormData(form).forEach(function (v,k) { if (typeof v === "string") url.searchParams.append(k,v); });
      url.searchParams.delete("page");
      return url;
    }
    function validQuery() {
      if (!form.checkValidity()) return false;
      var q = form.querySelector('[name="q"]');
      var minimum = Number(form.dataset.queryMin || 0);
      return !q || !q.value.trim() || Array.from(q.value.trim()).length >= minimum;
    }
    function syncLinks(url) {
      // Type tabs retain the current scope; they remain ordinary native links.
      document.querySelectorAll("[data-query-type]").forEach(function (link) {
        var target = new URL(link.href); ["state","course","group"].forEach(function (k) {
          if (url.searchParams.has(k)) target.searchParams.set(k,url.searchParams.get(k)); else target.searchParams.delete(k);
        }); target.searchParams.delete("page"); link.href = target.href;
      });
      document.querySelectorAll("[data-query-kind-link]").forEach(function (link) {
        var target = new URL(link.href); target.searchParams.set("kind",url.searchParams.get("kind") || ""); link.href = target.href;
      });
      document.querySelectorAll("[data-query-clear-filters]").forEach(function (link) {
        var target = new URL(link.href); target.searchParams.set("q",url.searchParams.get("q") || ""); link.href = target.href;
      });
    }
    async function load(url, mode) {
      var mine = revision;
      if (url.href === committed && mode !== "pop" && retry.hidden) {
        region.removeAttribute("aria-busy"); status.hidden = true; return;
      }
      var request = new AbortController(); controller = request; pending(); retry.hidden = true;
      try {
        var response = await fetch(url.href, {credentials:"same-origin", signal:request.signal,
          headers:{"X-AELMS-Query":"results"}});
        if (mine !== revision) return;
        if (response.redirected) {
          var destination = new URL(response.url);
          if (destination.origin === location.origin) location.assign(destination.href);
          throw new Error("Redirected");
        }
        if (!response.ok || response.headers.get("X-AELMS-Query") !== "results") throw new Error("Unavailable fragment");
        var html = await response.text();
        if (mine !== revision) return;
        var template = document.createElement("template"); template.innerHTML = html;
        if (template.content.querySelector("script,html,body,[data-login-form]")) throw new Error("Unexpected content");
        region.replaceChildren(template.content);
        if (window.aelmsEnhanceTables) window.aelmsEnhanceTables(region);
        syncLinks(url);
        if (mode !== "pop" && url.href !== location.href) {
          if (mode === "replace" || form.dataset.queryHistory === "replace") history.replaceState(null,"",url.href);
          else history.pushState(null,"",url.href);
        }
        committed = url.href; region.removeAttribute("aria-busy");
        var count = region.querySelector("[data-query-count]");
        status.textContent = count ? t("Showing %(count)s results.", {count:count.dataset.queryCount}) : t("Results updated.");
        status.hidden = form.hasAttribute("data-query-quiet-success");
        submit.hidden = true;
      } catch (error) {
        if (mine !== revision || error.name === "AbortError") return;
        region.removeAttribute("aria-busy"); status.hidden = false;
        status.textContent = t("Results could not be updated. Previous results are still shown. Retry or apply the filters.");
        submit.hidden = false; retry.hidden = false;
      } finally { if (controller === request) controller = null; }
    }
    function schedule(mode, delay) {
      invalidate(); pending();
      if (composing) return;
      if (!validQuery()) {
        region.removeAttribute("aria-busy");
        status.textContent = t("Enter at least %(count)s characters to search.", {count:form.dataset.queryMin});
        submit.hidden = false; return;
      }
      timer = setTimeout(function () { load(urlForForm(),mode); }, delay);
    }
    form.addEventListener("compositionstart", function () { composing = true; invalidate(); pending(); });
    form.addEventListener("compositionend", function () { composing = false; schedule("replace",Number(form.dataset.queryDelay || 350)); });
    form.addEventListener("input", function (event) {
      if (event.target.matches("input:not([type=hidden]),textarea")) schedule("replace",Number(form.dataset.queryDelay || 350));
    });
    form.addEventListener("change", function (event) {
      if (!event.target.matches("select")) return;
      invalidate(); pending();
      if (queued) return; queued = true;
      queueMicrotask(function () { queued = false; if (!composing && validQuery()) load(urlForForm(),"push"); });
    });
    form.addEventListener("submit", function (event) {
      if (!submit.hidden && event.submitter === submit) return; // native recovery path
      if (composing) { event.preventDefault(); return; }
      if (!validQuery()) return;
      event.preventDefault(); invalidate(); load(urlForForm(),"push");
    });
    retry.addEventListener("click", function () { invalidate(); if (validQuery()) load(urlForForm(),"push"); });
    region.addEventListener("click", function (event) {
      var link = event.target.closest(".activity-hub-pager a,.figma-pagination a,.pagination a,[data-query-page]");
      if (!link || event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      var url = new URL(link.href); if (url.origin !== base.origin || url.pathname !== base.pathname) return;
      event.preventDefault(); invalidate(); load(url,"push");
    });
    window.addEventListener("popstate", function () {
      invalidate(); var url = new URL(location.href);
      inputs.forEach(function (e) { if (e.type !== "submit") e.value = url.searchParams.has(e.name) ? url.searchParams.get(e.name) : defaults[e.name]; });
      load(url,"pop");
    });
    window.addEventListener("pagehide", invalidate);
    submit.hidden = true; // only after every required element/capability is initialized
  });
})();
