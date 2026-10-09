/* Presentation only: no API requests, learning state, credentials or business
   data in browser storage. Ordinary forms continue to submit to Flask. */
(function () {
  "use strict";
  var t = window.aelmsUI ? window.aelmsUI.t : function (text) { return text; };
  var body = document.body;
  var sidebar = document.getElementById("workspace-navigation");
  var toggles = Array.from(document.querySelectorAll("[data-nav-toggle]"));
  var toggle = toggles[0];
  var workspace = document.querySelector("[data-workspace-body]");
  var backdrop = document.querySelector(".figma-nav-backdrop");
  var compact = window.matchMedia("(max-width: 1023px)");
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  var previousFocus = null;

  // Tab-scoped presentation only: store a pixel offset, never a URL or identity.
  var navigation = sidebar && sidebar.querySelector(".figma-sidebar-nav");
  var navigationKey = sidebar && "aelms.navigation-scroll.v1." + sidebar.dataset.navScope;
  var navigationReset = false;
  function saveNavigationPosition() {
    if (!navigation || navigationReset) return;
    try { sessionStorage.setItem(navigationKey, String(navigation.scrollTop)); } catch (_) { /* Native navigation still works. */ }
  }
  function restoreNavigationPosition() {
    if (!navigation) return;
    try {
      var stored = sessionStorage.getItem(navigationKey);
      var position = stored === null ? 0 : Number(stored);
      if (Number.isFinite(position) && position >= 0) navigation.scrollTop = Math.min(position, Math.max(0, navigation.scrollHeight - navigation.clientHeight));
    } catch (_) { /* Storage can be unavailable. */ }
  }
  if (navigation) {
    navigation.addEventListener("scroll", saveNavigationPosition, {passive:true});
    navigation.addEventListener("click", saveNavigationPosition, true);
    window.addEventListener("pagehide", saveNavigationPosition);
    window.addEventListener("pageshow", restoreNavigationPosition);
  }

  var accountMenu = document.querySelector("[data-account-menu]");
  if (accountMenu) {
    document.addEventListener("click", function (event) {
      if (accountMenu.open && !accountMenu.contains(event.target)) accountMenu.open = false;
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && accountMenu.open) {
        event.preventDefault(); accountMenu.open = false;
        accountMenu.querySelector("summary").focus({preventScroll:true});
      }
    });
    accountMenu.addEventListener("focusout", function (event) {
      if (event.relatedTarget && !accountMenu.contains(event.relatedTarget)) accountMenu.open = false;
    });
    window.addEventListener("pagehide", function () { accountMenu.open = false; });
  }
  document.querySelectorAll("[data-account-signout]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      queueMicrotask(function () {
        if (event.defaultPrevented) return;
        navigationReset = true;
        try { ["student", "teacher", "administrator", "researcher"].forEach(function (role) { sessionStorage.removeItem("aelms.navigation-scroll.v1." + role); }); } catch (_) { /* Optional UI state only. */ }
      });
    });
  });

  if (sidebar && toggle && workspace && backdrop && !document.querySelector("[data-more-panel]")) {
    function setOpen(open, restoreFocus) {
      open = Boolean(open && compact.matches);
      body.dataset.navOpen = String(open);
      toggles.forEach(function (button) { button.setAttribute("aria-expanded", String(open)); });
      backdrop.hidden = !open;
      sidebar.inert = compact.matches && !open;
      workspace.inert = open;
      var bottomNav = document.querySelector(".workspace-mobile-nav");
      if (bottomNav) bottomNav.inert = open;
      if (open) {
        previousFocus = document.activeElement;
        sidebar.setAttribute("role", "dialog");
        sidebar.setAttribute("aria-modal", "true");
        sidebar.querySelector("[data-nav-close]").focus({preventScroll:true});
      } else {
        sidebar.removeAttribute("role");
        sidebar.removeAttribute("aria-modal");
        if (restoreFocus && previousFocus instanceof HTMLElement) previousFocus.focus({preventScroll:true});
      }
    }
    toggles.forEach(function (button) { button.hidden = false; });
    sidebar.querySelector("[data-nav-close]").hidden = false;
    setOpen(false, false);
    body.dataset.uiReady = "true";
    restoreNavigationPosition();
    toggles.forEach(function (button) { button.addEventListener("click", function () { setOpen(body.dataset.navOpen !== "true", true); }); });
    document.querySelectorAll("[data-nav-close]").forEach(function (button) {
      button.addEventListener("click", function () { setOpen(false, true); });
    });
    sidebar.addEventListener("click", function (event) {
      if (event.target.closest("a")) setOpen(false, false);
    });
    compact.addEventListener("change", function () { setOpen(false, true); });
    document.addEventListener("keydown", function (event) {
      if (body.dataset.navOpen !== "true") return;
      if (event.key === "Escape") { event.preventDefault(); setOpen(false, true); }
      if (event.key === "Tab") {
        var focusables = Array.from(sidebar.querySelectorAll("a[href], button, input:not([type='hidden']), [tabindex='0']"))
          .filter(function (element) { return !element.disabled && element.getClientRects().length > 0; });
        var first = focusables[0];
        var last = focusables[focusables.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    // Keep the server's selected navigation state accessible.
    sidebar.querySelectorAll("a.portal-nav__link, a.admin-nav__link").forEach(function (link) {
      if (link.classList.contains("portal-nav__link--active") || link.classList.contains("admin-nav__link--active")) {
        link.setAttribute("aria-current", "page");
      }
    });
  }

  // Keep wide records inside a horizontal scroller, retaining the original
  // table nodes, form ownership, names, events and research identifiers.
  document.querySelectorAll(".figma-page table").forEach(function (table) {
    if (table.parentElement.classList.contains("dash-scroll")) return;
    var scroller = document.createElement("div");
    scroller.className = "figma-table-scroll";
    table.before(scroller);
    scroller.appendChild(table);
  });

  // Shared task content is immediately visible; motion belongs to explicit controls.

  var passwordToggle = document.querySelector("[data-password-toggle]");
  if (passwordToggle) {
    var password = document.getElementById(passwordToggle.getAttribute("aria-controls"));
    passwordToggle.hidden = false;
    passwordToggle.addEventListener("click", function () {
      var show = password.type === "password";
      password.type = show ? "text" : "password";
      passwordToggle.setAttribute("aria-pressed", String(show));
      passwordToggle.setAttribute("aria-label", show ? t("Hide password") : t("Show password"));
    });
  }

  var loginForm = document.querySelector("[data-login-form]");
  if (loginForm) {
    function clearBusy() {
      loginForm.removeAttribute("aria-busy");
      loginForm.querySelector("[data-login-submit]").disabled = false;
      loginForm.querySelector("[data-login-label]").textContent = t("Sign in");
      loginForm.querySelector("[data-login-spinner]").hidden = true;
    }
    loginForm.addEventListener("submit", function () {
      loginForm.setAttribute("aria-busy", "true");
      loginForm.querySelector("[data-login-label]").textContent = t("Signing in…");
      loginForm.querySelector("[data-login-spinner]").hidden = false;
      // Defer disabling until the browser has built the native form submission.
      window.setTimeout(function () { loginForm.querySelector("[data-login-submit]").disabled = true; }, 0);
    });
    window.addEventListener("pageshow", clearBusy);
    loginForm.addEventListener("focusin", function () { body.dataset.writing = "true"; });
    loginForm.addEventListener("focusout", function (event) {
      body.dataset.writing = String(event.relatedTarget instanceof HTMLInputElement);
    });

    var wordElements = Array.from(document.querySelectorAll("[data-learning-word]"));
    var words = [t("Hello"), t("Discover"), t("Confidence"), t("Grow"), t("Listen"), t("Explore"), t("Together"), t("Learn"), t("Speak"), t("Create"), t("Imagine"), t("Believe"), t("Write"), t("Connect"), t("Practice"), t("Achieve")];
    var timers = [];
    var wordMotion = new MutationObserver(function () { syncWords(); });
    function wordsPaused() { return reduceMotion.matches || document.documentElement.dataset.uiMotion === "reduce" || document.hidden || body.dataset.writing === "true"; }
    function stopWords() { timers.forEach(window.clearTimeout); timers = []; }
    function startWords() {
      stopWords();
      if (wordsPaused()) return;
      wordElements.forEach(function (element, slot) {
        var index = slot;
        var length = words[index].length;
        var deleting = true;
        function type() {
          if (wordsPaused()) return;
          var delay = 110;
          {
            length += deleting ? -1 : 1;
            element.textContent = words[index].slice(0, Math.max(0, length));
            if (length <= 0) { index = (index + 4) % words.length; deleting = false; delay = 600; }
            else if (length >= words[index].length) { deleting = true; delay = 3000; }
          }
          timers[slot] = window.setTimeout(type, delay);
        }
        timers[slot] = window.setTimeout(type, 2200 + slot * 700);
      });
    }
    function syncWords() {
      stopWords();
      body.dataset.pageHidden = String(document.hidden);
      if (reduceMotion.matches || document.documentElement.dataset.uiMotion === "reduce") wordElements.forEach(function (element, slot) { element.textContent = words[slot]; });
      if (!wordsPaused()) startWords();
    }
    wordMotion.observe(document.documentElement, {attributes:true, attributeFilter:["data-ui-motion"]});
    wordMotion.observe(body, {attributes:true, attributeFilter:["data-writing"]});
    document.addEventListener("visibilitychange", syncWords);
    syncWords();
    reduceMotion.addEventListener("change", syncWords);
    window.addEventListener("pagehide", stopWords);
    window.addEventListener("pageshow", syncWords);
  }
})();
