/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Shared unread bell preview; the full inbox retains its native forms. */
(function () {
  "use strict";
  var t = window.aelmsUI ? window.aelmsUI.t : function (text) { return text; };
  var bell = document.querySelector("[data-notification-bell]");
  if (!bell || !window.fetch || !window.AbortController) return;
  var retry = bell.querySelector('[data-notification-retry]');
  if (retry) pageScope.listen(retry,'click',load);
  var toggle = bell.querySelector("[data-notification-toggle]");
  var panel = bell.querySelector("[data-notification-preview]");
  var content = bell.querySelector("[data-notification-preview-body]");
  var compact = window.matchMedia("(max-width: 767px)");
  var markForm = panel.querySelector('[data-notification-read-all]');
  var markButton = markForm.querySelector('button');
  var controller, marking = false, unreadCount = markButton.disabled ? 0 : 1;
  toggle.setAttribute("aria-controls", panel.id);
  toggle.setAttribute("aria-expanded", "false");
  toggle.hidden = false;
  function setCount(count) {
    if (!Number.isSafeInteger(count) || count < 0) return;
    unreadCount = count;
    var dot = toggle.querySelector('i');
    if (count && !dot) {
      dot = document.createElement('i'); dot.setAttribute('aria-hidden', 'true'); toggle.appendChild(dot);
    }
    if (!count && dot) dot.remove();
    toggle.setAttribute('aria-label', count ? t('Notifications, %(count)s unread', {count:count}) : t('Notifications'));
    markButton.disabled = marking || unreadCount === 0;
  }
  pageScope.listen(document, 'aelms:notification-count', function (event) { setCount(event.detail); });

  function previewFragment(text) {
    var parsed = new DOMParser().parseFromString(text, "text/html");
    var fragment = parsed.querySelector("[data-notification-fragment]");
    if (!fragment) throw new Error("Preview unavailable");
    return fragment;
  }

  function showFragment(fragment) {
    setCount(Number(fragment.dataset.liveUnread));
    if (panel.hidden) return;
    content.replaceChildren(fragment);
    document.dispatchEvent(new Event('aelms:communication-mounted'));
  }

  pageScope.listen(markForm, 'submit', async function (event) {
    event.preventDefault();
    if (marking || !unreadCount) return;
    marking = true;
    markButton.disabled = true;
    content.setAttribute('aria-busy', 'true');
    var research = window.researchCollector, attempt;
    try { attempt = research?.beginRequest({form:markForm, reuse_reserved:true}); } catch (_) {}
    function finish(detail) {
      try { research?.finishRequest(attempt, detail); } catch (_) {}
    }
    try {
      var response = await pageScope.fetch(markForm.action, {
        method: 'POST', body: new FormData(markForm), credentials: 'same-origin', cache: 'no-store',
        headers: {'X-AELMS-Notification-Preview': '1'}
      });
      finish('finished');
      if (!response.ok || response.redirected || response.headers.get('X-AELMS-Notification-Preview') !== '1') {
        throw new Error('Read request unavailable');
      }
      var fragment = previewFragment(await response.text());
      if (controller) controller.abort();
      showFragment(fragment);
      if (!panel.hidden && document.activeElement === document.body) panel.querySelector('h2').focus({preventScroll:true});
    } catch (_) {
      finish('network_unknown');
      if (!panel.hidden) {
        message('Could not mark notifications as read. Try again.');
        if (retry) retry.hidden = false;
      }
    } finally {
      marking = false;
      markButton.disabled = unreadCount === 0;
      content.removeAttribute('aria-busy');
    }
  });

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
    if (retry) retry.hidden = true;
    message(t("Loading your updates…"));
    content.setAttribute("aria-busy", "true");
    try {
      var response = await pageScope.fetch(bell.dataset.previewUrl, {
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
      var fragment = previewFragment(await response.text());
      if (!request.signal.aborted) showFragment(fragment);
    } catch (error) {
      if (request.signal.aborted) return;
      message(t("Updates could not load. Try again or open all notifications."));
      if (retry) retry.hidden = false;
    } finally {
      if (controller === request) content.removeAttribute("aria-busy");
    }
  }

  pageScope.listen(toggle, "click", function (event) {
    event.preventDefault();
    if (!panel.hidden) { close(true); return; }
    document.querySelectorAll("[data-account-menu]").forEach(function (menu) { menu.open = false; });
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    panel.querySelector("h2").focus({ preventScroll: true });
    load();
  });
  bell.querySelector("[data-notification-close]").addEventListener("click", function () { close(true); });
  pageScope.listen(document, "click", function (event) {
    // Retry replaces its own button before bubbling; the original event path
    // still identifies the click as inside the preview, so do not dismiss it.
    var inside = event.composedPath ? event.composedPath().includes(bell) : bell.contains(event.target);
    if (!panel.hidden && !inside) close(false);
  });
  pageScope.listen(document, "focusin", function (event) { if (!panel.hidden && !bell.contains(event.target)) close(false); });
  pageScope.listen(document, "keydown", function (event) {
    if (!panel.hidden && event.key === "Escape") { event.preventDefault(); close(true); }
  });
  pageScope.listen(compact, "change", function () { close(false); });
  pageScope.listen(window, "pagehide", function () { close(false); });
})();

});
