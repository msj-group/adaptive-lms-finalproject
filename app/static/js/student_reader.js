/* Native pinch and Ctrl+wheel zoom remain under the browser's control. */
window.aelmsModules.register(document.currentScript, function (scope) {
  'use strict';
  const reader = document.querySelector('[data-student-reader]');
  if (!reader) return;
  const outline = reader.querySelector('[data-focus-outline]');
  if (outline) {
    const summary = outline.querySelector('summary');
    scope.listen(reader.querySelector('[data-close-outline]'), 'click', () => { outline.open = false; summary.focus(); });
    scope.listen(document, 'keydown', event => { if (event.key === 'Escape' && outline.open) { outline.open = false; summary.focus(); } });
    scope.listen(reader, 'pointerdown', event => { if (outline.open && !outline.contains(event.target)) outline.open = false; });
  }
  function revealResource() {
    let target;
    try { target = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch (_) { return; }
    const section = target?.closest('.sf-material-section');
    if (section) { section.open = true; scope.requestAnimationFrame(() => target.scrollIntoView({block:'center'})); }
  }
  revealResource(); scope.listen(window, 'hashchange', revealResource);
  const scroller = reader.querySelector('.sf-scroll');
  const ending = reader.querySelector('[data-lesson-end]');
  const finish = reader.querySelector('[data-completion-dialog]');
  let maybeFinish = () => {};
  if (scroller && ending && finish) {
    const images = [...scroller.querySelectorAll('[data-pdf-viewer] img')];
    let armed = false, dismissed = false, closing = false, touchY = null;
    function atEnd() {
      const edge = ending.getBoundingClientRect(), view = scroller.getBoundingClientRect();
      return edge.top >= view.top && edge.bottom <= view.bottom + 2;
    }
    maybeFinish = () => {
      if (!atEnd()) { dismissed = false; return; }
      if (!armed || dismissed || finish.open || document.fullscreenElement === reader ||
          images.some(image => !image.complete || !image.naturalWidth) ||
          document.querySelector('dialog[open]')) return;
      if (outline) outline.open = false;
      finish.showModal();
    };
    function closeFinish() {
      if (closing || !finish.open) return;
      dismissed = true; closing = true; finish.classList.add('is-closing');
      scope.setTimeout(() => {
        finish.close(); finish.classList.remove('is-closing'); closing = false;
        scroller.focus({preventScroll:true});
      }, matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 180);
    }
    scope.listen(finish.querySelector('[data-close-completion]'), 'click', closeFinish);
    scope.listen(finish, 'cancel', event => { event.preventDefault(); closeFinish(); });
    scope.listen(finish, 'click', event => { if (event.target === finish) {
      const box = finish.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) closeFinish();
    } });
    // Restoring a scroll position or loading images cannot arm the prompt.
    // A deliberate downward gesture is required, including when content fits.
    scope.listen(scroller, 'wheel', event => { if (event.deltaY > 0 && !event.ctrlKey) { armed = true; scope.requestAnimationFrame(maybeFinish); } }, {passive:true});
    scope.listen(scroller, 'touchstart', event => { touchY = event.touches.length === 1 ? event.touches[0].clientY : null; }, {passive:true});
    scope.listen(scroller, 'touchmove', event => { if (touchY !== null && event.touches.length === 1 && event.touches[0].clientY < touchY - 8) { armed = true; scope.requestAnimationFrame(maybeFinish); } }, {passive:true});
    scope.listen(reader, 'keydown', event => {
      if (!event.target.closest('button,a,input,select,textarea,summary,dialog') &&
          ['ArrowDown','PageDown','End',' '].includes(event.key) && !event.ctrlKey && !event.altKey && !event.shiftKey) {
        armed = true; scope.requestAnimationFrame(maybeFinish);
      }
    });
    scope.listen(scroller, 'scroll', maybeFinish, {passive:true});
    scope.listen(window, 'resize', maybeFinish, {passive:true});
    for (const image of images) scope.listen(image, 'load', maybeFinish);
    if (window.IntersectionObserver) scope.createObserver(IntersectionObserver, maybeFinish,
      {root:scroller, threshold:1}).observe(ending);
    scope.signal.addEventListener('abort', () => { if (finish.open) finish.close(); }, {once:true});
  }
  const fullscreen = reader.querySelector('[data-reader-fullscreen]');
  if (!fullscreen) return;
  fullscreen.hidden = !reader.requestFullscreen;
  function fullscreenState() {
    const expanded = document.fullscreenElement === reader;
    const label = expanded ? fullscreen.dataset.exitLabel : fullscreen.dataset.enterLabel;
    fullscreen.setAttribute('aria-label', label); fullscreen.setAttribute('title', label);
    const text = fullscreen.querySelector('[data-fullscreen-label]');
    if (text) text.textContent = label;
    const icon = fullscreen.querySelector('[data-fullscreen-icon]');
    if (icon) icon.setAttribute('d', expanded ? 'M3 8h5V3m8 0v5h5M8 21v-5H3m18 0h-5v5' : 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5');
    if (!expanded) maybeFinish();
  }
  scope.listen(fullscreen, 'click', () => {
    if (outline) outline.open = false;
    if (finish?.open) finish.close();
    const request = document.fullscreenElement === reader ? document.exitFullscreen() : reader.requestFullscreen();
    request?.catch(() => {});
  });
  scope.listen(document, 'fullscreenchange', fullscreenState);
  fullscreenState();
  scope.signal.addEventListener('abort', () => { if (document.fullscreenElement === reader) document.exitFullscreen().catch(() => {}); }, {once:true});
});
