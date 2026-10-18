/* The desktop popup is part of the current index document. Each opening reads
   the authorized GET fragment afresh; no navigation, prefetch or content cache.
   Phones and unavailable JavaScript retain the ordinary detail link. */
(function () {
  "use strict";
  var root = document.querySelector("[data-announcement-workspace]");
  var popup = root && root.querySelector("[data-announcement-popup]");
  if (!popup || !window.fetch || !window.AbortController || !window.DOMParser ||
      !window.matchMedia || !window.HTMLDialogElement ||
      typeof HTMLDialogElement.prototype.showModal !== "function") return;
  var desktop = window.matchMedia("(min-width: 640px)");
  var t = window.aelmsUI ? window.aelmsUI.t : function (value) { return value; };
  var content = popup.querySelector("[data-announcement-content]");
  var loading = popup.querySelector("[data-announcement-loading]");
  var errorBox = popup.querySelector("[data-announcement-error]");
  var errorMessage = popup.querySelector("[data-announcement-error-message]");
  var pageLink = popup.querySelector("[data-announcement-page]");
  var closeButton = popup.querySelector("[data-announcement-close]");
  var opener = null, current = null, controller = null, sequence = 0;
  function cancelLoad() {
    sequence += 1;
    if (controller) controller.abort();
    controller = null;
  }
  async function loadReader() {
    cancelLoad();
    var request = new AbortController();
    controller = request;
    var revision = sequence;
    var selected = current;
    content.replaceChildren();
    content.setAttribute("aria-busy", "true");
    loading.hidden = false;
    errorBox.hidden = true;
    try {
      var response = await fetch(selected.href, {
        method: "GET", credentials: "same-origin", cache: "no-store",
        headers: { "X-AELMS-Query": "results" }, signal: request.signal
      });
      if (request.signal.aborted || revision !== sequence || !popup.open) return;
      if (response.redirected || response.status === 401 || response.status === 403 || response.status === 404) {
        throw new Error("unavailable");
      }
      if (!response.ok || response.headers.get("X-AELMS-Query") !== "results" ||
          !(response.headers.get("Content-Type") || "").includes("text/html")) throw new Error("load");
      var parsed = new DOMParser().parseFromString(await response.text(), "text/html");
      if (request.signal.aborted || revision !== sequence || !popup.open) return;
      var incoming = parsed.querySelector("[data-announcement-reader]");
      if (!incoming || incoming.dataset.announcementId !== selected.id) throw new Error("load");
      // This server-owned fragment escapes the authored title/body as plain text.
      content.replaceChildren(document.importNode(incoming, true));
    } catch (error) {
      if (request.signal.aborted || revision !== sequence || !popup.open) return;
      errorMessage.textContent = t(error.message === "unavailable" ?
        "This announcement is no longer available." : "The announcement could not be loaded. Try again.");
      errorBox.hidden = false;
    } finally {
      if (revision === sequence && popup.open) {
        loading.hidden = true;
        content.setAttribute("aria-busy", "false");
      }
      if (controller === request) controller = null;
    }
  }
  root.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;
    var link = event.target.closest("a[data-announcement-open]");
    if (!link || !root.contains(link) || !desktop.matches || event.defaultPrevented ||
        event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey ||
        link.hasAttribute("download") || (link.target && link.target !== "_self")) return;
    var url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    try { popup.showModal(); } catch (error) { return; }
    event.preventDefault();
    opener = link;
    current = { href: url.href, id: link.dataset.announcementId };
    pageLink.href = url.href;
    closeButton.focus({ preventScroll: true });
    loadReader();
  });
  closeButton.addEventListener("click", function () { popup.close(); });
  popup.querySelector("[data-announcement-retry]").addEventListener("click", function () {
    if (popup.open && current) loadReader();
  });
  popup.addEventListener("click", function (event) {
    if (event.target !== popup) return;
    var bounds = popup.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right ||
        event.clientY < bounds.top || event.clientY > bounds.bottom) popup.close();
  });
  popup.addEventListener("close", function () {
    if (popup.open) return;
    cancelLoad();
    content.replaceChildren();
    content.setAttribute("aria-busy", "false");
    errorBox.hidden = true;
    current = null;
    pageLink.removeAttribute("href");
    if (opener && opener.isConnected) opener.focus({ preventScroll: true });
    opener = null;
  });
  window.addEventListener("pagehide", cancelLoad);
})();
