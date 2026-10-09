/* Shared, read-only bell preview on every viewport. Owned POSTs remain native. */
(function () {
  "use strict";
  var t = window.aelmsUI ? window.aelmsUI.t : function (text) { return text; };
  var bell = document.querySelector("[data-notification-bell]");
  if (!bell || !window.fetch || !window.AbortController) return;
  var toggle = bell.querySelector("[data-notification-toggle]");
  var panel = bell.querySelector("[data-notification-preview]");
  var content = bell.querySelector("[data-notification-preview-body]");
  var compact = window.matchMedia("(max-width: 767px)");
  var controller;
  toggle.setAttribute("aria-controls", panel.id);
  toggle.setAttribute("aria-expanded", "false");
  toggle.hidden = false;

  function close(restoreFocus) {
    if (controller) controller.abort();
    controller = null;
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
    content.replaceChildren();
    content.removeAttribute("aria-busy");
    if (restoreFocus) toggle.focus({ preventScroll: true });
  }

  function message(text) {
    var paragraph = document.createElement("p");
    paragraph.className = "notif-preview-empty";
    paragraph.setAttribute("role", "status");
    paragraph.textContent = t(text);
    content.replaceChildren(paragraph);
  }

  async function load() {
    if (controller) controller.abort();
    var request = new AbortController();
    controller = request;
    message(t("Loading your updates…"));
    content.setAttribute("aria-busy", "true");
    try {
      var response = await fetch(bell.dataset.previewUrl, {
        credentials: "same-origin", cache: "no-store", signal: request.signal
      });
      if (request.signal.aborted || panel.hidden) return;
      if (response.redirected || response.status === 401 || response.status === 403) {
        message(t("Your session may have ended. Open notifications to sign in again."));
        return;
      }
      if (!response.ok) throw new Error(t("Preview unavailable"));
      // This fragment comes only from our same-origin, role-gated Jinja route.
      // Titles/messages are escaped plain text; stored target paths are absent.
      var documentFragment = new DOMParser().parseFromString(await response.text(), "text/html");
      var fragment = documentFragment.querySelector("[data-notification-fragment]");
      if (!fragment) throw new Error(t("Preview unavailable"));
      if (!request.signal.aborted) content.replaceChildren(fragment);
    } catch (error) {
      if (request.signal.aborted) return;
      message(t("Updates could not load. Try again or open all notifications."));
      var retry = document.createElement("button");
      retry.type = "button";
      retry.className = "btn btn--secondary";
      retry.textContent = t("Try again");
      retry.addEventListener("click", load);
      content.appendChild(retry);
    } finally {
      if (controller === request) content.removeAttribute("aria-busy");
    }
  }

  toggle.addEventListener("click", function (event) {
    event.preventDefault();
    if (!panel.hidden) { close(true); return; }
    document.querySelectorAll("[data-account-menu]").forEach(function (menu) { menu.open = false; });
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    panel.querySelector("h2").focus({ preventScroll: true });
    load();
  });
  bell.querySelector("[data-notification-close]").addEventListener("click", function () { close(true); });
  document.addEventListener("click", function (event) {
    // Retry replaces its own button before bubbling; the original event path
    // still identifies the click as inside the preview, so do not dismiss it.
    var inside = event.composedPath ? event.composedPath().includes(bell) : bell.contains(event.target);
    if (!panel.hidden && !inside) close(false);
  });
  document.addEventListener("focusin", function (event) { if (!panel.hidden && !bell.contains(event.target)) close(false); });
  document.addEventListener("keydown", function (event) {
    if (!panel.hidden && event.key === "Escape") { event.preventDefault(); close(true); }
  });
  compact.addEventListener("change", function () { close(false); });
  window.addEventListener("pagehide", function () { close(false); });
})();
