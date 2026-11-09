/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Local presentation only. More never changes history, sends requests, or
   emits a page view. The existing collector owns allowlisted click metadata. */
(function () {
  "use strict";
  var panel = document.querySelector('[data-more-panel]');
  var toggles = Array.from(document.querySelectorAll('[data-more-toggle]'));
  var toggle = toggles[0];
  var backdrop = document.querySelector('.workspace-more-backdrop');
  var sidebar = document.getElementById('workspace-navigation');
  var bottomNav = document.querySelector('.workspace-mobile-nav');
  var compact = window.matchMedia('(max-width:1023px)');
  if (!panel || !toggle || !backdrop) return;
  if (bottomNav && !bottomNav.querySelector('a[aria-current="page"]') && panel.querySelector('a[aria-current="page"]')) {
    bottomNav.querySelector('[data-more-toggle]').setAttribute('aria-current', 'page');
  }
  var previousFocus = null;
  function measureNavigation() {
    if (bottomNav) document.body.style.setProperty('--mobile-nav-height', bottomNav.getBoundingClientRect().height + 'px');
  }
  function setOpen(open, restoreFocus) {
    open = Boolean(open && compact.matches);
    if (open && panel.hidden) previousFocus = document.activeElement;
    panel.hidden = !open;
    backdrop.hidden = !open;
    toggles.forEach(function (button) { button.setAttribute('aria-expanded', String(open)); });
    document.body.dataset.moreOpen = String(open);
    if (open) (Array.from(panel.querySelectorAll('a[href],button:not([disabled])')).find(function (control) { return control.getClientRects().length; }) || panel).focus({preventScroll:true});
    else if (restoreFocus && previousFocus && previousFocus.isConnected) previousFocus.focus({preventScroll:true});
  }
  function syncLayout() {
    if (!compact.matches) setOpen(false, true);
    else document.body.dataset.moreOpen = String(!panel.hidden);
    if (sidebar) sidebar.inert = compact.matches || document.documentElement.dataset.workspaceSidebar === "collapsed";
    // CSS owns the breakpoint; never leave More hidden after a page swap.
    toggles.forEach(function (button) { button.hidden = false; });
    document.body.dataset.uiReady = 'true';
    measureNavigation();
  }
  toggles.forEach(function (button) { pageScope.listen(button, 'click', function () { setOpen(panel.hidden, true); }); });
  document.querySelectorAll('[data-more-dismiss]').forEach(function (button) {
    pageScope.listen(button, 'click', function () { setOpen(false, true); });
  });
  pageScope.listen(panel, 'click', function (event) {
    if (event.target.closest('a[href],[popovertarget]')) setOpen(false, false);
  });
  pageScope.listen(document, 'keydown', function (event) {
    if (!panel.hidden && event.key === 'Escape') { event.preventDefault(); setOpen(false, true); }
  });
  // Non-modal dialog: the bottom bar stays available. Keyboard users may leave
  // normally; dismiss without stealing the focus they just moved to.
  pageScope.listen(document, 'focusin', function (event) {
    if (!panel.hidden && !panel.contains(event.target) && !event.target.closest('.workspace-mobile-nav,[data-more-toggle],[data-more-dismiss]')) setOpen(false, false);
  });
  pageScope.listen(compact, 'change', syncLayout);
  if (window.ResizeObserver && bottomNav) pageScope.createObserver(ResizeObserver, measureNavigation).observe(bottomNav);
  pageScope.listen(window, 'pagehide', function () { setOpen(false, false); });
  pageScope.listen(window, 'pageshow', syncLayout);
  pageScope.listen(document, 'aelms:page-ready', syncLayout);
  syncLayout();
})();

/* Direction-aware header. Messages keeps its platform toolbar visible. */
(function () {
  "use strict";
  var header = document.querySelector('.yc-workspace .st-header');
  if (!header) return;
  var body = document.body;
  var keepHeader = body.classList.contains('figma-messages');
  var compact = window.matchMedia('(max-width:1023px)');
  var positions = new WeakMap();
  var hasScrollIntent = false;
  function showHeader() {
    body.dataset.headerHidden = 'false';
    header.inert = false;
  }
  function measureHeader() {
    if (compact.matches) body.style.setProperty('--mobile-header-height', header.offsetHeight + 'px');
  }
  function syncHeader() {
    positions = new WeakMap();
    hasScrollIntent = false;
    body.dataset.scrollHeader = String(compact.matches);
    showHeader();
    measureHeader();
    var root = document.scrollingElement;
    body.dataset.headerScrolled = String(Boolean(root && root.scrollTop > 12));
    if (root) positions.set(root, {position:root.scrollTop, anchor:root.scrollTop, direction:0});
  }
  pageScope.listen(document, 'scroll', function (event) {
    if (!compact.matches) return;
    var target = event.target === document ? document.scrollingElement : event.target;
    if (!target || (target !== document.scrollingElement && !target.matches('.messenger-sidebar__list,.messenger-timeline,.messenger-welcome,.messenger-greeting'))) return;
    var position = Math.max(0, Math.min(target.scrollTop, target.scrollHeight - target.clientHeight));
    body.dataset.headerScrolled = String(position > 12);
    if (keepHeader) return;
    var state = positions.get(target) || {position:0, anchor:0, direction:0};
    var delta = position - state.position;
    var direction = Math.sign(delta);
    if (direction && direction !== state.direction) state.anchor = state.position;
    state.position = position;
    if (direction) state.direction = direction;
    positions.set(target, state);
    // Initial message-history positioning is not a request to hide navigation.
    if (!hasScrollIntent) { state.anchor = position; return; }
    if (position <= 12 || header.contains(document.activeElement) || header.querySelector('details[open],[aria-expanded="true"]') || body.dataset.moreOpen === 'true') {
      showHeader();
      state.anchor = position;
      return;
    }
    if (Math.abs(position - state.anchor) < 10) return;
    var hidden = direction > 0;
    body.dataset.headerHidden = String(hidden);
    header.inert = hidden;
  }, {capture:true, passive:true});
  // Tabbing into the header restores it before the focused control can be obscured.
  ['wheel','touchmove','pointerdown'].forEach(function (name) {
    pageScope.listen(document, name, function () { hasScrollIntent = true; }, {passive:true});
  });
  pageScope.listen(document, 'keydown', function (event) {
    if (event.key === 'Tab') showHeader();
    if (['ArrowDown','ArrowUp','PageDown','PageUp','Home','End',' '].indexOf(event.key) !== -1) hasScrollIntent = true;
  });
  pageScope.listen(header, 'focusin', showHeader);
  pageScope.listen(compact, 'change', syncHeader);
  pageScope.listen(window, 'resize', measureHeader);
  pageScope.listen(window, 'pageshow', syncHeader);
  if (window.ResizeObserver) pageScope.createObserver(ResizeObserver, measureHeader).observe(header);
  syncHeader();
})();

});
