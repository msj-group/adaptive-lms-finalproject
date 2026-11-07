/* Book-only zoom and explicit end-of-Unit actions, including fullscreen. */
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
  const finish = reader.querySelector('[data-completion-panel]');
  let armed = false, zooming = false;
  let maybeFinish = () => {};
  if (scroller && ending && finish) {
    const images = [...scroller.querySelectorAll('[data-pdf-viewer] img')];
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let touchY = null;
    // Keep the native form available without JavaScript. With enhancement it
    // enters the document flow only after a deliberate end-of-content gesture.
    finish.hidden = true;
    function atEnd() {
      const edge = ending.getBoundingClientRect(), view = scroller.getBoundingClientRect();
      return edge.top >= view.top && edge.bottom <= view.bottom + 2;
    }
    maybeFinish = () => {
      if (!finish.hidden || !armed || zooming || !atEnd() ||
          images.some(image => !image.complete || !image.naturalWidth) ||
          document.querySelector('dialog[open]')) return;
      if (outline) outline.open = false;
      finish.hidden = false;
      finish.classList.add('is-revealed');
      // The card occupies its own space after Course content and Teacher
      // additions. It never overlays a book page, disclosure or media player.
      scope.requestAnimationFrame(() => finish.scrollIntoView({
        block:'end', behavior:reducedMotion ? 'instant' : 'smooth'
      }));
    };
    // Restoring a scroll position or loading images cannot arm the action.
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
  }
  const pages = scroller?.querySelector('[data-pdf-viewer]');
  if (pages) {
    const sheets = [...pages.querySelectorAll('.ce-page')];
    let scale = 1, pinch = null, settleTimer = 0;
    const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
    function viewportCenter() {
      const box = scroller.getBoundingClientRect();
      return {x:box.left + box.width / 2, y:box.top + box.height / 2};
    }
    function anchorAt(point) {
      // Keep the same place on the same page under the wheel/pinch midpoint.
      const bounds = scroller.getBoundingClientRect();
      const x = clamp(point.x, bounds.left, bounds.right);
      const y = clamp(point.y, bounds.top, bounds.bottom);
      let nearest = null, distance = Infinity;
      for (const sheet of sheets) {
        const box = sheet.getBoundingClientRect();
        const delta = Math.max(box.top - y, y - box.bottom, 0);
        if (box.width && box.height && delta < distance) { nearest = {sheet, box}; distance = delta; }
      }
      if (!nearest) return null;
      return {sheet:nearest.sheet, x:(x - nearest.box.left) / nearest.box.width,
        y:(y - nearest.box.top) / nearest.box.height, target:{x,y}};
    }
    function resizePages(value, anchor, point = anchor?.target) {
      const style = getComputedStyle(scroller);
      const available = scroller.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      if (available <= 0) return;
      scale = clamp(value, 1, 4);
      pages.style.setProperty('--sf-page-width', `${Math.min(760, available) * scale}px`);
      pages.setAttribute('data-reader-zoom', '');
      if (anchor && point) {
        const box = anchor.sheet.getBoundingClientRect();
        scroller.scrollLeft += box.left + box.width * anchor.x - point.x;
        scroller.scrollTop += box.top + box.height * anchor.y - point.y;
      }
    }
    function zoomGesture() {
      // A zoom/reflow is not evidence that the Student scrolled to the end.
      armed = false; zooming = true;
      scope.clearTimeout(settleTimer);
      settleTimer = scope.setTimeout(() => { zooming = false; }, 180);
    }
    scope.listen(reader, 'wheel', event => {
      if (!event.ctrlKey || event.target.closest('dialog') || !event.cancelable) return;
      event.preventDefault(); zoomGesture();
      const units = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? scroller.clientHeight : 1;
      const delta = clamp(event.deltaY * units, -120, 120);
      resizePages(scale * Math.exp(-delta * .002), anchorAt({x:event.clientX, y:event.clientY}));
    }, {passive:false});
    const midpoint = touches => ({x:(touches[0].clientX + touches[1].clientX) / 2,
      y:(touches[0].clientY + touches[1].clientY) / 2});
    const separation = touches => Math.hypot(touches[0].clientX - touches[1].clientX,
      touches[0].clientY - touches[1].clientY);
    scope.listen(pages, 'touchstart', event => {
      if (event.touches.length !== 2) { pinch = null; return; }
      if (event.cancelable) event.preventDefault();
      zoomGesture();
      pinch = {distance:Math.max(1, separation(event.touches)), scale,
        anchor:anchorAt(midpoint(event.touches))};
    }, {passive:false});
    scope.listen(pages, 'touchmove', event => {
      if (!pinch || event.touches.length !== 2) return;
      if (event.cancelable) event.preventDefault();
      zoomGesture();
      resizePages(pinch.scale * separation(event.touches) / pinch.distance,
        pinch.anchor, midpoint(event.touches));
    }, {passive:false});
    for (const name of ['touchend','touchcancel']) scope.listen(pages, name, () => { pinch = null; }, {passive:true});
    // Safari's native gesture zoom must not also magnify the surrounding UI.
    for (const name of ['gesturestart','gesturechange']) scope.listen(pages, name, event => {
      if (event.cancelable) event.preventDefault();
    }, {passive:false});
    scope.listen(reader, 'keydown', event => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey ||
          event.target.closest('input,textarea,select,dialog,[contenteditable=true]')) return;
      if (!['+','=','-','0'].includes(event.key)) return;
      event.preventDefault(); zoomGesture();
      resizePages(event.key === '0' ? 1 : scale * (event.key === '-' ? 1 / 1.2 : 1.2), anchorAt(viewportCenter()));
    });
    const fit = () => resizePages(scale, anchorAt(viewportCenter()));
    scope.listen(window, 'resize', fit, {passive:true});
    scope.listen(document, 'fullscreenchange', fit);
    resizePages(1, null);
  }
  const fullscreen = reader.querySelector('[data-reader-fullscreen]');
  if (!fullscreen) return;
  const fullscreenRoot = document.documentElement;
  const expandedReader = () => document.fullscreenElement === fullscreenRoot;
  fullscreen.hidden = !fullscreenRoot.requestFullscreen;
  let advancing = false;
  async function advance(url, data = null) {
    if (advancing) return;
    advancing = true;
    const buttons = [...reader.querySelectorAll('[data-completion-panel] button')];
    buttons.forEach(button => { button.disabled = true; });
    try { await window.aelmsReaderNavigate(url, data); }
    finally { advancing = false; buttons.forEach(button => { button.disabled = false; }); }
  }
  scope.listen(reader, 'submit', event => {
    if (!expandedReader() || !window.aelmsReaderNavigate || event.defaultPrevented ||
        !event.target.matches('[data-research-form=lesson_complete]')) return;
    const submitter = event.submitter;
    if (!['next_lesson','next_unit'].includes(submitter?.value)) return;
    const data = new FormData(event.target, submitter);
    event.preventDefault();
    advance(event.target.action, data);
  });
  scope.listen(reader, 'click', event => {
    const link = event.target.closest('[data-reader-next]');
    if (!link || !expandedReader() || !window.aelmsReaderNavigate || event.defaultPrevented ||
        event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); advance(link.href);
  });
  function fullscreenState() {
    const expanded = expandedReader();
    const label = expanded ? fullscreen.dataset.exitLabel : fullscreen.dataset.enterLabel;
    fullscreen.setAttribute('aria-label', label); fullscreen.setAttribute('title', label);
    const text = fullscreen.querySelector('[data-fullscreen-label]');
    if (text) text.textContent = label;
    const icon = fullscreen.querySelector('[data-fullscreen-icon]');
    if (icon) icon.setAttribute('d', expanded ? 'M3 8h5V3m8 0v5h5M8 21v-5H3m18 0h-5v5' : 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5');
    maybeFinish();
  }
  scope.listen(fullscreen, 'click', () => {
    if (outline) outline.open = false;
    const request = expandedReader() ? document.exitFullscreen() : fullscreenRoot.requestFullscreen();
    request?.catch(() => {});
  });
  scope.listen(document, 'fullscreenchange', fullscreenState);
  fullscreenState();
  scope.signal.addEventListener('abort', () => {
    if (expandedReader() && !fullscreenRoot.hasAttribute('data-reader-transition')) document.exitFullscreen().catch(() => {});
  }, {once:true});
});
