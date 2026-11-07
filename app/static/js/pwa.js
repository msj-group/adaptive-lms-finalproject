/* Persistent install affordance across the application's native soft navigation. */
(() => {
  'use strict';
  if (window.aelmsInstallApp) return;
  const script = document.currentScript;
  const workerURL = script.dataset.workerUrl;
  let promptEvent = null;
  let installationConfirmed = false;
  let prompting = false;
  let installOpener = null;
  function syncSystemColor() {
    const meta = document.querySelector('meta[name="theme-color"]');
    const color = getComputedStyle(document.body).backgroundColor;
    if (meta && color && color !== 'rgba(0, 0, 0, 0)' && color !== 'transparent') meta.content = color;
  }
  function standalone() {
    return matchMedia('(display-mode: standalone), (display-mode: fullscreen)').matches || navigator.standalone === true;
  }
  function refresh() {
    syncSystemColor();
    document.querySelectorAll('[data-pwa-install]').forEach(button => {
      button.hidden = !window.isSecureContext || standalone() || installationConfirmed;
      button.disabled = prompting;
      button.setAttribute('aria-busy', String(prompting));
    });
  }
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault(); promptEvent = event; installationConfirmed = false; refresh();
  });
  window.addEventListener('appinstalled', () => {
    promptEvent = null; installationConfirmed = true;
    document.querySelectorAll('[data-pwa-install]').forEach(button => { button.hidden = true; });
    closeGuide();
  });
  const display = matchMedia('(display-mode: standalone), (display-mode: fullscreen)');
  display.addEventListener('change', refresh);
  document.addEventListener('DOMContentLoaded', refresh);
  document.addEventListener('aelms:page-ready', refresh);
  window.addEventListener('pageshow', refresh);
  new MutationObserver(syncSystemColor).observe(document.documentElement,
    {attributes:true, attributeFilter:['data-theme']});
  function closeGuide() {
    const dialog = document.getElementById('pwa-install-help');
    if (dialog?.hasAttribute('open')) {
      if (typeof dialog.close === 'function') dialog.close();
      else { dialog.removeAttribute('open'); installOpener?.focus(); }
    }
  }
  function showGuide(button, failed = false) {
    const dialog = document.getElementById('pwa-install-help');
    if (!dialog) return;
    installOpener = button.closest('[data-account-menu]')?.querySelector('summary') || button;
    const agent = navigator.userAgent;
    const ios = /iPad|iPhone|iPod/.test(agent) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const safariMac = /Macintosh|Mac OS X/.test(agent) && /Safari/.test(agent) && !/Chrome|Chromium|Edg|OPR/.test(agent);
    const platform = ios ? 'ios' : /Android/i.test(agent) ? 'android' :
      safariMac ? 'mac' : /Windows/.test(agent) && /Firefox\//.test(agent) ? 'firefox' : 'desktop';
    dialog.querySelectorAll('[data-pwa-guide]').forEach(guide => { guide.hidden = guide.dataset.pwaGuide !== platform; });
    const status = dialog.querySelector('[data-pwa-prompt-failed]');
    if (status) status.hidden = !failed;
    if (!dialog.hasAttribute('open')) {
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else { dialog.setAttribute('aria-modal','false'); dialog.setAttribute('open',''); dialog.querySelector('[data-pwa-close]')?.focus(); }
    }
  }
  document.addEventListener('close', event => {
    if (event.target.id === 'pwa-install-help' && installOpener?.isConnected && !installOpener.hidden) installOpener.focus();
  }, true);
  document.addEventListener('keydown', event => {
    const dialog = document.getElementById('pwa-install-help');
    if (event.key === 'Escape' && dialog?.hasAttribute('open') && typeof dialog.close !== 'function') {
      closeGuide();
    }
  });
  document.addEventListener('click', async event => {
    const close = event.target.closest('[data-pwa-close]');
    if (close) { closeGuide(); return; }
    const button = event.target.closest('[data-pwa-install]');
    if (!button || prompting) return;
    button.closest('[data-account-menu]')?.removeAttribute('open');
    if (promptEvent) {
      const pending = promptEvent; promptEvent = null;
      prompting = true; refresh();
      try { await pending.prompt(); await pending.userChoice; }
      catch (_) { if (button.isConnected) showGuide(button, true); }
      finally { prompting = false; refresh(); }
      return;
    }
    showGuide(button);
  });
  window.aelmsInstallApp = {refresh};
  if (window.isSecureContext && 'serviceWorker' in navigator) {
    // Registration changes no account, notification or device permission.
    navigator.serviceWorker.register(workerURL, {scope:'/'}).catch(() => {});
  }
  refresh();
})();
