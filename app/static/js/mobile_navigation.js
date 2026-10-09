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
  var previousFocus = null;
  function measureNavigation() {
    if (bottomNav) document.body.style.setProperty('--mobile-nav-height', bottomNav.getBoundingClientRect().height + 'px');
  }
  function setOpen(open, restoreFocus) {
    open = Boolean(open);
    if (open && panel.hidden) previousFocus = document.activeElement;
    panel.hidden = !open;
    backdrop.hidden = !open;
    toggles.forEach(function (button) { button.setAttribute('aria-expanded', String(open)); });
    document.body.dataset.moreOpen = String(open);
    if (open) (Array.from(panel.querySelectorAll('a[href],button:not([disabled])')).find(function (control) { return control.getClientRects().length; }) || panel).focus({preventScroll:true});
    else if (restoreFocus && previousFocus && previousFocus.isConnected) previousFocus.focus({preventScroll:true});
  }
  function syncLayout() {
    setOpen(false, true);
    if (sidebar) sidebar.inert = compact.matches;
    toggles.forEach(function (button) { button.hidden = false; });
    document.body.dataset.uiReady = 'true';
    measureNavigation();
  }
  toggles.forEach(function (button) { button.addEventListener('click', function () { setOpen(panel.hidden, true); }); });
  document.querySelectorAll('[data-more-dismiss]').forEach(function (button) {
    button.addEventListener('click', function () { setOpen(false, true); });
  });
  panel.addEventListener('click', function (event) {
    if (event.target.closest('a[href],[popovertarget]')) setOpen(false, false);
  });
  document.addEventListener('keydown', function (event) {
    if (!panel.hidden && event.key === 'Escape') { event.preventDefault(); setOpen(false, true); }
  });
  // Non-modal dialog: the bottom bar stays available. Keyboard users may leave
  // normally; dismiss without stealing the focus they just moved to.
  document.addEventListener('focusin', function (event) {
    if (!panel.hidden && !panel.contains(event.target) && !event.target.closest('.workspace-mobile-nav,[data-more-toggle]')) setOpen(false, false);
  });
  compact.addEventListener('change', syncLayout);
  if (window.ResizeObserver && bottomNav) new ResizeObserver(measureNavigation).observe(bottomNav);
  window.addEventListener('pagehide', function () { setOpen(false, false); });
  window.addEventListener('pageshow', syncLayout);
  syncLayout();
})();
