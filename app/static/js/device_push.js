/* Device permission is requested only after an explicit Enable click. */
(() => {
  'use strict';
  if (window.aelmsDevicePush) return;
  const urls = {...document.currentScript.dataset};
  const remembered = new Set();
  const t = value => window.aelmsUI?.t(value) || value;
  let busy = false;
  let state;
  const account = () => document.querySelector('meta[name="aelms-device-account"]');
  const installed = () => matchMedia('(display-mode: standalone), (display-mode: fullscreen)').matches || navigator.standalone === true;
  const supported = () => isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  function preference() { try { return localStorage.getItem('aelms.device.remember') !== '0'; } catch (_) { return true; } }
  function csrf() { return document.querySelector('meta[name="csrf-token"]')?.content || ''; }
  async function send(url, body, token = csrf()) {
    const response = await fetch(url, {method:'POST', credentials:'same-origin', cache:'no-store',
      headers:{'Content-Type':'application/json','X-CSRFToken':token},body:JSON.stringify(body)});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(result.error || t('Please sign in again and try once more.'));
      error.code = result.code;
      throw error;
    }
    return result;
  }
  async function remember() {
    const meta = account();
    if (!meta?.content || meta.dataset.remembered === 'true' || remembered.has(meta.content) || !installed() || !preference()) return;
    const actor = meta.content;
    try { await send(urls.rememberUrl, {}); remembered.add(actor); }
    catch (_) { /* Native sign-in remains available if storage is restricted. */ }
  }
  function paint(message, enabled = false, disabled = false) {
    const panel = document.querySelector('[data-device-notifications]');
    if (!panel) return;
    panel.querySelector('[data-device-status]').textContent = t(message);
    const button = panel.querySelector('[data-device-toggle]');
    button.textContent = t(enabled ? 'Disable on this device' : 'Enable notifications');
    button.disabled = disabled || busy;
    button.setAttribute('aria-busy', String(busy));
    panel.dataset.subscribed = String(enabled);
  }
  async function refresh() {
    const checkbox = document.querySelector('[data-remember-device]');
    if (checkbox && installed() && preference()) checkbox.checked = true;
    remember();
    if (!document.querySelector('[data-device-notifications]')) return;
    if (!supported()) { paint('Device notifications are not supported in this browser.', false, true); return; }
    try {
      const response = await fetch(urls.statusUrl, {credentials:'same-origin', cache:'no-store'});
      state = await response.json();
      if (!response.ok || !state.enabled) { paint('Device notifications are not available yet.', false, true); return; }
      if (Notification.permission === 'denied') { paint('Notifications are blocked. Allow them in your browser or device settings.', state.subscribed, !state.subscribed); return; }
      paint(state.subscribed ? 'Notifications are enabled on this device.' : 'Turn on device notifications to receive your updates.', state.subscribed);
    } catch (_) { paint('Could not check notifications. Reopen this page to try again.', false, true); }
  }
  function publicKey(value) {
    const raw = atob(value.replace(/-/g,'+').replace(/_/g,'/') + '='.repeat((4-value.length%4)%4));
    return Uint8Array.from(raw, char => char.charCodeAt(0));
  }
  async function readyWorker() {
    const registration = await Promise.race([navigator.serviceWorker.ready,
      new Promise((_, reject) => setTimeout(() => reject(new Error(t('Close all Youth Learn windows and reopen the app to finish this update.'))), 6000))]);
    const ready = await new Promise(resolve => {
      const channel = new MessageChannel();
      const timer = setTimeout(() => { channel.port1.close(); resolve(false); }, 2000);
      channel.port1.onmessage = event => { clearTimeout(timer); channel.port1.close(); resolve(event.data?.push === 1); };
      registration.active?.postMessage({type:'push-capability'}, [channel.port2]);
    });
    if (!ready) throw new Error(t('Close all Youth Learn windows and reopen the app to finish this update.'));
    return registration;
  }
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-device-toggle]');
    if (!button || busy) return;
    const panel = button.closest('[data-device-notifications]');
    const disabling = panel.dataset.subscribed === 'true';
    busy = true;
    paint(disabling ? 'Turning off notifications…' : 'Preparing device notifications…', disabling);
    try {
      if (disabling) {
        await send(urls.disableUrl, {});
        const registration = await navigator.serviceWorker.getRegistration('/');
        const subscription = await registration?.pushManager.getSubscription();
        if (subscription) await subscription.unsubscribe().catch(() => {});
      } else {
        // Keep the permission request inside the user's click, before async work.
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') throw new Error(t('Notifications were not enabled. You can choose again in device settings.'));
        const registration = await readyWorker();
        let subscription = await registration.pushManager.getSubscription();
        subscription ||= await registration.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:publicKey(state.publicKey)});
        try { await send(urls.subscribeUrl, subscription.toJSON()); }
        catch (error) {
          if (error.code !== 'renew_subscription') throw error;
          await subscription.unsubscribe();
          subscription = await registration.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:publicKey(state.publicKey)});
          await send(urls.subscribeUrl, subscription.toJSON());
        }
      }
      busy = false;
      await refresh();
    } catch (error) {
      busy = false;
      paint(error.message || 'Device notifications could not be saved. Please try again.', disabling);
    }
  });
  document.addEventListener('change', event => {
    if (!event.target.matches('[data-remember-device]')) return;
    try { localStorage.setItem('aelms.device.remember', event.target.checked ? '1' : '0'); } catch (_) { /* Preference only. */ }
  });
  document.addEventListener('aelms:page-ready', refresh);
  window.addEventListener('pageshow', refresh);
  window.addEventListener('appinstalled', remember);
  window.aelmsDevicePush = {refresh};
  refresh();
})();
