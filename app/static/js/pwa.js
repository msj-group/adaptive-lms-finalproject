/* Persistent install affordance across the application's native soft navigation. */
(() => {
  'use strict';
  if (window.aelmsInstallApp) return;
  const script = document.currentScript;
  const workerURL = script.dataset.workerUrl;
  let promptEvent = null;
  let installationConfirmed = false;
  function standalone() {
    return matchMedia('(display-mode: standalone), (display-mode: fullscreen)').matches || navigator.standalone === true;
  }
  function refresh() {
    document.querySelectorAll('[data-pwa-install]').forEach(button => {
      button.hidden = !window.isSecureContext || standalone() || installationConfirmed;
    });
  }
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault(); promptEvent = event; installationConfirmed = false; refresh();
  });
  window.addEventListener('appinstalled', () => {
    promptEvent = null; installationConfirmed = true;
    document.querySelectorAll('[data-pwa-install]').forEach(button => { button.hidden = true; });
    document.getElementById('pwa-install-help')?.close();
  });
  const display = matchMedia('(display-mode: standalone), (display-mode: fullscreen)');
  display.addEventListener('change', refresh);
  document.addEventListener('DOMContentLoaded', refresh);
  document.addEventListener('click', async event => {
    const close = event.target.closest('[data-pwa-close]');
    if (close) { document.getElementById('pwa-install-help')?.close(); return; }
    const button = event.target.closest('[data-pwa-install]');
    if (!button) return;
    button.closest('[data-account-menu]')?.removeAttribute('open');
    if (promptEvent) {
      const pending = promptEvent; promptEvent = null;
      try { await pending.prompt(); await pending.userChoice; } catch (_) { /* The browser owns installation. */ }
      return;
    }
    const dialog = document.getElementById('pwa-install-help');
    if (!dialog) return;
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const platform = ios ? 'ios' : /Android/i.test(navigator.userAgent) ? 'android' : 'desktop';
    dialog.querySelectorAll('[data-pwa-guide]').forEach(guide => { guide.hidden = guide.dataset.pwaGuide !== platform; });
    if (!dialog.open) dialog.showModal();
  });
  window.aelmsInstallApp = {refresh};
  if (window.isSecureContext && 'serviceWorker' in navigator) {
    // Registration changes no account, notification or device permission.
    navigator.serviceWorker.register(workerURL, {scope:'/'}).catch(() => {});
  }
  refresh();
})();
