/* Visual feedback only: no wheel/touch interception, scroll emulation or telemetry. */
window.aelmsModules.register(document.currentScript, function (scope) {
  'use strict';
  if (matchMedia('(forced-colors: active)').matches) return;
  let target = null, cue = null, vertical = null, horizontal = null, frame = 0, fade = 0;
  const clamp = (n, min, max) => Math.max(min, Math.min(max, n));
  function render() {
    frame = 0;
    if (!target?.isConnected) { cue?.removeAttribute('data-active'); return; }
    const viewport = target === document.scrollingElement;
    const rect = viewport ? {top:0,left:0,right:innerWidth,bottom:innerHeight} : target.getBoundingClientRect();
    const top = Math.max(0, rect.top), left = Math.max(0, rect.left);
    const right = Math.min(innerWidth, rect.right), bottom = Math.min(innerHeight, rect.bottom);
    if (bottom - top < 24 || right - left < 24) { cue?.removeAttribute('data-active'); return; }
    if (!cue) {
      cue = document.createElement('div'); cue.className = 'platform-scroll-cue';
      cue.setAttribute('aria-hidden', 'true'); cue.inert = true;
      vertical = document.createElement('i'); horizontal = document.createElement('i');
      cue.append(vertical, horizontal);
    }
    const portal = document.fullscreenElement || target.closest('dialog[open],[popover]') || document.body;
    if (cue.parentElement !== portal) portal.append(cue);
    // Top-layer dialogs can establish a containing block while animating.
    const origin = cue.getBoundingClientRect();
    const rtl = getComputedStyle(target).direction === 'rtl';
    const yMax = target.scrollHeight - target.clientHeight, xMax = target.scrollWidth - target.clientWidth;
    vertical.hidden = yMax <= 1; horizontal.hidden = xMax <= 1;
    if (yMax > 1) {
      const track = bottom - top - 16, size = Math.min(track, Math.max(26, track * target.clientHeight / target.scrollHeight));
      vertical.style.cssText = `top:${top - origin.top + 8 + clamp(target.scrollTop / yMax,0,1) * (track-size)}px;left:${(rtl ? left + 2 : right - 5) - origin.left}px;width:3px;height:${size}px`;
    }
    if (xMax > 1) {
      const track = right - left - 16, size = Math.min(track, Math.max(26, track * target.clientWidth / target.scrollWidth));
      const progress = clamp(Math.abs(target.scrollLeft) / xMax,0,1);
      horizontal.style.cssText = `top:${bottom-origin.top-5}px;left:${left-origin.left + 8 + (rtl ? 1-progress : progress) * (track-size)}px;height:3px;width:${size}px`;
    }
    cue.toggleAttribute('data-active', yMax > 1 || xMax > 1);
    scope.clearTimeout(fade);
    fade = scope.setTimeout(() => cue.removeAttribute('data-active'), 750);
  }
  function schedule() { if (target && !frame) frame = scope.requestAnimationFrame(render); }
  scope.listen(document, 'scroll', event => {
    target = event.target === document ? document.scrollingElement : event.target;
    if (target instanceof Element && !target.closest('.platform-scroll-cue')) schedule();
  }, {capture:true, passive:true});
  scope.listen(window, 'resize', schedule, {passive:true});
  scope.listen(document, 'fullscreenchange', schedule);
  if (window.visualViewport) {
    scope.listen(visualViewport, 'resize', schedule, {passive:true});
    scope.listen(visualViewport, 'scroll', schedule, {passive:true});
  }
  scope.signal.addEventListener('abort', () => cue?.remove(), {once:true});
});
