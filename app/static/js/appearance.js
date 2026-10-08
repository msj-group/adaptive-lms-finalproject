/* Native POST forms persist language/theme; scripts only add preview and dismissal. */
(function () {
  "use strict";
  var root = document.documentElement;
  document.body.dataset.uiMotion = root.dataset.uiMotion || "system";
  document.body.dataset.uiDensity = root.dataset.uiDensity || "comfortable";
  document.querySelectorAll("[data-appearance-form] select[name='theme']").forEach(function (control) {
    control.addEventListener("change", function () { window.aelmsUI.theme(control.value); });
  });
  var panels = document.querySelectorAll('[data-utility-panel]');
  panels.forEach(function (panel) {
    var nativePopover = 'showPopover' in panel;
    var previous;
    function prepareOpen() {
      document.querySelectorAll('[data-account-menu]').forEach(function (account) { account.open = false; });
      var sidebar = document.getElementById('workspace-navigation');
      // Let the existing drawer release its inert/focus state first.
      if (sidebar && document.body.dataset.navOpen === 'true') {
        var closeDrawer = sidebar.querySelector('[data-nav-close]');
        if (closeDrawer) closeDrawer.click();
      }
    }
    function visible(control) {
      return control && control.isConnected && control.getClientRects().length && !control.closest('[inert]');
    }
    function restoreFocus() {
      setTimeout(function () {
        if (nativePopover ? panel.matches(':popover-open') : !panel.hidden) return;
        var active = document.activeElement;
        // Light dismissal may already have focused another page control.
        if (active !== document.body && !panel.contains(active) && visible(active)) return;
        var destination = visible(previous) ? previous : document.querySelector('[data-account-menu] > summary');
        if (!visible(destination)) destination = document.querySelector('[data-nav-toggle]');
        if (visible(destination)) destination.focus({preventScroll:true});
      }, 0);
    }
    panel.addEventListener('beforetoggle', function (event) {
      if (event.newState !== 'open') return;
      prepareOpen();
    });
    panel.addEventListener('toggle', function (event) { if (event.newState === 'closed') restoreFocus(); });
    if (!nativePopover) { panel.classList.add('utility-panel-fallback'); panel.hidden = true; }
    document.querySelectorAll('[popovertarget="' + panel.id + '"]').forEach(function (button) {
      button.addEventListener('click', function () {
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
    document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && !panel.hidden) { panel.hidden = true; restoreFocus(); } });
    document.addEventListener('click', function (event) {
      if (!panel.hidden && !panel.contains(event.target) && !event.target.closest('[popovertarget]')) { panel.hidden = true; restoreFocus(); }
    });
  });
  var locationUrl = new URL(location.href);
  var requestedPanel = locationUrl.searchParams.get('_utility');
  if (requestedPanel === 'display' || requestedPanel === 'help') {
    var trigger = document.querySelector('[popovertarget="workspace-' + requestedPanel + '-panel"]');
    if (trigger) trigger.click();
    locationUrl.searchParams.delete('_utility');
    history.replaceState(history.state, '', locationUrl.href);
  }
  document.querySelectorAll('[data-ui-preference]').forEach(function (control) {
    var name = control.dataset.uiPreference;
    control.value = document.body.dataset[name === 'motion' ? 'uiMotion' : 'uiDensity'];
    control.addEventListener('change', function () {
      var settings;
      try { settings = JSON.parse(localStorage.getItem('aelms.display.v1') || '{}'); } catch (_) { settings = {}; }
      if (!settings || typeof settings !== 'object') settings = {};
      settings[name] = control.value;
      root.dataset[name === 'motion' ? 'uiMotion' : 'uiDensity'] = control.value;
      document.body.dataset[name === 'motion' ? 'uiMotion' : 'uiDensity'] = control.value;
      document.querySelectorAll('[data-ui-preference="'+name+'"]').forEach(function (other) { other.value = control.value; });
      var message = 'Display preferences saved on this device.';
      try { localStorage.setItem('aelms.display.v1',JSON.stringify({motion:document.body.dataset.uiMotion,density:document.body.dataset.uiDensity})); }
      catch (_) { message = 'Applied for this page. Device storage is unavailable.'; }
      document.querySelectorAll('[data-preference-status]').forEach(function (status) { status.textContent = window.aelmsUI.t(message); });
    });
  });
})();
