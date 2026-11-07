/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Native POST forms persist language/theme; scripts only add preview and dismissal. */
(function () {
  "use strict";
  var root = document.documentElement;
  document.body.dataset.uiDensity = root.dataset.uiDensity || "comfortable";
  document.querySelectorAll("[data-appearance-form] select[name='theme']").forEach(function (control) {
    pageScope.listen(control, "change", function () { window.aelmsUI.theme(control.value); });
  });
  // Separate language/theme forms must retain the latest in-page theme preview.
  function syncThemeFields() {
    var preference = root.dataset.themePreference;
    if (!["system", "light", "dark"].includes(preference)) return;
    document.querySelectorAll("[data-appearance-form] input[name='theme'], [data-appearance-form] select[name='theme']").forEach(function (control) { control.value = preference; });
    document.querySelectorAll('[data-theme-options] button[name="theme"]').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.value === preference));
    });
  }
  document.querySelectorAll('[data-appearance-form]').forEach(function (form) {
    pageScope.listen(form, 'submit', syncThemeFields);
  });
  document.querySelectorAll('[data-theme-options]').forEach(function (form) {
    pageScope.listen(form, 'submit', function (event) {
      var choice = event.submitter;
      if (!choice || !['system', 'light', 'dark'].includes(choice.value)) return;
      event.preventDefault();
      window.aelmsUI.theme(choice.value);
      syncThemeFields();
    });
  });
  syncThemeFields();
  pageScope.createObserver(MutationObserver, syncThemeFields).observe(root, {attributes:true, attributeFilter:['data-theme-preference']});
  var panels = document.querySelectorAll('[data-utility-panel]');
  panels.forEach(function (panel) {
    var nativePopover = 'showPopover' in panel;
    var previous;
    function prepareOpen() {
      syncThemeFields();
      document.querySelectorAll('[data-account-menu]').forEach(function (account) { account.open = false; });
      var sidebar = document.getElementById('workspace-navigation');
      // Let the existing drawer release its inert/focus state first.
      if (sidebar && document.body.dataset.navOpen === 'true') {
        var closeDrawer = sidebar.querySelector('[data-nav-close]');
        if (closeDrawer) closeDrawer.click();
      }
    }
    function visible(control) {
      if (!control || !control.isConnected || !control.getClientRects().length || control.closest('[inert]')) return false;
      var closed = control.closest('details:not([open])');
      return !closed || closed.querySelector('summary') === control;
    }
    function restoreFocus() {
      pageScope.setTimeout(function () {
        if (nativePopover ? panel.matches(':popover-open') : !panel.hidden) return;
        var active = document.activeElement;
        // Light dismissal may already have focused another page control.
        if (active !== document.body && !panel.contains(active) && visible(active)) return;
        var destination = visible(previous) ? previous : null;
        if (!destination && previous && previous.closest('[data-more-panel]')) destination = document.querySelector('.workspace-mobile-nav [data-more-toggle]');
        if (!visible(destination)) destination = document.querySelector('[data-account-menu] > summary');
        if (!visible(destination)) destination = document.querySelector('[data-nav-toggle]');
        if (visible(destination)) destination.focus({preventScroll:true});
      }, 0);
    }
    pageScope.listen(panel, 'beforetoggle', function (event) {
      if (event.newState !== 'open') return;
      prepareOpen();
    });
    pageScope.listen(panel, 'toggle', function (event) { if (event.newState === 'closed') restoreFocus(); });
    if (!nativePopover) { panel.classList.add('utility-panel-fallback'); panel.hidden = true; }
    document.querySelectorAll('[popovertarget="' + panel.id + '"]').forEach(function (button) {
      pageScope.listen(button, 'click', function () {
        var hide = button.getAttribute('popovertargetaction') === 'hide';
        if (!hide) previous = button;
        if (nativePopover) return;
        if (hide || !panel.hidden) { panel.hidden = true; restoreFocus(); return; }
        panels.forEach(function (other) { if (other !== panel) other.hidden = true; });
        prepareOpen(); panel.hidden = false;
        (panel.querySelector('[autofocus]') || panel.querySelector('select,button')).focus({preventScroll:true});
      });
    });
    if (nativePopover) return;
    pageScope.listen(document, 'keydown', function (event) { if (event.key === 'Escape' && !panel.hidden) { panel.hidden = true; restoreFocus(); } });
    pageScope.listen(document, 'click', function (event) {
      if (!panel.hidden && !panel.contains(event.target) && !event.target.closest('[popovertarget]')) { panel.hidden = true; restoreFocus(); }
    });
  });
  var locationUrl = new URL(location.href);
  var requestedPanel = locationUrl.searchParams.get('_utility');
  if (requestedPanel === 'display' || requestedPanel === 'help') {
    var targetId = 'workspace-' + requestedPanel + '-panel';
    var trigger = document.querySelector('[popovertarget="' + targetId + '"]:not([popovertargetaction="hide"])');
    var requested = document.getElementById(targetId);
    if (trigger) trigger.click();
    // Retain legacy utility redirects when a panel has no visible menu entry.
    else if (requested) {
      if ('showPopover' in requested) requested.showPopover();
      else {
        requested.hidden = false;
        (requested.querySelector('[autofocus]') || requested.querySelector('select,button')).focus({preventScroll:true});
      }
    }
    locationUrl.searchParams.delete('_utility');
    pageScope.setTimeout(function () { history.replaceState(history.state, '', locationUrl.href); }, 0);
  }
  var densityControls = document.querySelectorAll('[data-ui-preference="density"]');
  densityControls.forEach(function (control) {
    control.value = document.body.dataset.uiDensity;
    pageScope.listen(control, 'change', function () {
      root.dataset.uiDensity = document.body.dataset.uiDensity = control.value;
      densityControls.forEach(function (other) { other.value = control.value; });
      var message = 'Display preferences saved on this device.';
      try { localStorage.setItem('aelms.display.v1',JSON.stringify({density:control.value})); }
      catch (_) { message = 'Applied for this page. Device storage is unavailable.'; }
      document.querySelectorAll('[data-preference-status]').forEach(function (status) { status.textContent = window.aelmsUI.t(message); });
    });
  });

  function showFlashNotices(container) {
    container.querySelectorAll('[data-app-flash]').forEach(function (source) {
      // Important feedback stays beside the form and never steals focus.
      source.querySelector('template')?.remove();
      source.classList.add('platform-notices');
    });
  }
  window.aelmsUI.showFlashNotices = showFlashNotices;
  pageScope.listen(document, 'DOMContentLoaded', function () { showFlashNotices(document); }, {once:true});
})();

});
