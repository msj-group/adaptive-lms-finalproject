/* Scoped, same-origin media controls. Existing HTML media events remain authoritative. */
window.aelmsModules.register(document.currentScript, function (scope) {
  'use strict';
  const controllers = new Set();
  const time = value => {
    if (!Number.isFinite(value) || value < 0) return '0:00';
    const seconds = Math.floor(value);
    return (seconds >= 3600 ? Math.floor(seconds / 3600) + ':' : '') +
      String(Math.floor(seconds / 60) % 60).padStart(seconds >= 3600 ? 2 : 1, '0') + ':' + String(seconds % 60).padStart(2, '0');
  };
  function mount(container = document) {
  const roots = container.matches?.('[data-course-media]') ? [container] : [...container.querySelectorAll('[data-course-media]')];
  for (const root of roots) {
    if (root.dataset.mediaMounted) continue;
    root.dataset.mediaMounted = 'true';
    const elements = [...root.querySelectorAll('audio,video')];
    const controls = root.querySelector('[data-media-controls]');
    if (!elements.length || !controls) { delete root.dataset.mediaMounted; continue; }
    const mediaScope = window.aelmsCreatePageScope();
    const play = root.querySelector('[data-media-play]');
    const seek = root.querySelector('[data-media-seek]'), current = root.querySelector('[data-media-current]');
    const duration = root.querySelector('[data-media-duration]'), buffer = root.querySelector('[data-media-buffer]');
    const volume = root.querySelector('[data-media-volume]'), mute = root.querySelector('[data-media-mute]');
    const speed = root.querySelector('[data-media-speed]'), pip = root.querySelector('[data-media-pip]');
    const speedMenu = root.querySelector('[data-speed-menu]');
    const speedSummary = speedMenu?.querySelector('summary');
    const speedRates = speedMenu ? [...speedMenu.querySelectorAll('[data-speed-rate]')] : [];
    const fullscreen = root.querySelector('[data-media-fullscreen]'), status = root.querySelector('[data-media-status]');
    const rotate = root.querySelector('[data-media-rotate]');
    const captionToggle = root.querySelector('[data-media-captions]');
    const captionLine = root.querySelector('[data-media-caption]');
    let captionsEnabled = true, captionElement = null, captionSource = null, captionText = '';
    let active = elements.find(el => el.hasAttribute('src')) || elements[0];
    let scrubbing = false;
    let orientationLocked = false;
    let nativeFullscreen = false;
    const hasSource = () => Boolean(active.currentSrc || active.getAttribute('src'));
    function positionVideoCaption() {
      if (active.tagName !== 'VIDEO' || captionLine.hidden) return;
      const frame=active.getBoundingClientRect(), box=root.getBoundingClientRect();
      if (!frame.width || !frame.height) return;
      const ratio=active.videoWidth && active.videoHeight ? active.videoWidth/active.videoHeight : frame.width/frame.height;
      const pictureHeight=Math.min(frame.height,frame.width/ratio);
      const pictureTop=frame.top+(frame.height-pictureHeight)/2;
      const pictureBottom=pictureTop+pictureHeight;
      const bar=controls.getBoundingClientRect();
      const bottom=box.bottom-pictureBottom+8;
      const aboveControls=!root.classList.contains('cm-controls-hidden') && bar.top<pictureBottom ? box.bottom-bar.top+8 : bottom;
      const maximum=Math.max(bottom,box.bottom-pictureTop-captionLine.offsetHeight-8);
      root.style.setProperty('--cm-caption-bottom',`${Math.min(Math.max(bottom,aboveControls),maximum)}px`);
    }
    function renderCaption() {
      const available=captionElement && (captionElement.readyState<2 || captionElement.track.cues?.length);
      const text=!captionsEnabled || !available ? '' : Array.from(captionElement.track.activeCues || [],
        cue => cue.getCueAsHTML().textContent).join('\n');
      captionLine.hidden=!captionsEnabled || !available || (active.tagName==='VIDEO' && (!text || nativeFullscreen));
      if (text===captionText) { positionVideoCaption(); return; }
      captionText=text;
      captionLine.replaceChildren();
      if (!text) return;
      const cue=document.createElement('p');
      cue.className='cm-caption-cue';
      cue.textContent=text;
      captionLine.append(cue);
      captionLine.scrollTop=0;
      positionVideoCaption();
    }
    function updateCaptionMode() {
      captionToggle.setAttribute('aria-pressed', String(captionsEnabled));
      if (captionElement) {
        const mode = captionsEnabled ? (nativeFullscreen ? 'showing' : 'hidden') : 'disabled';
        if (captionElement.track.mode !== mode) captionElement.track.mode = mode;
      }
      renderCaption();
    }
    function loadCaptions() {
      if (!root.hasAttribute('data-course-captions') || !captionToggle || !captionLine) return;
      const source = active.getAttribute('src');
      if (source === captionSource && captionElement?.parentNode === active) return;
      if (captionElement) {
        captionElement.track.mode = 'disabled';
        captionElement.remove();
      }
      captionElement = null;
      captionSource = source;
      captionToggle.hidden = true;
      renderCaption();
      if (!source) return;
      const url = new URL(source, location.href);
      if (url.origin !== location.origin || !url.pathname.startsWith('/')) return;
      url.searchParams.set('captions', '1');
      const element = document.createElement('track');
      element.kind = 'captions';
      element.srclang = 'en';
      element.label = 'English';
      element.src = url.href;
      element.default = true;
      captionElement = element;
      element.addEventListener('load', () => {
        if (captionElement !== element) return;
        captionToggle.hidden = !element.track.cues?.length;
        updateCaptionMode();
      }, {once:true});
      element.addEventListener('error', () => {
        if (captionElement === element) {
          captionToggle.hidden = true;
          captionElement = null;
          element.remove();
          renderCaption();
        }
      }, {once:true});
      element.track.addEventListener('cuechange', () => {
        if (captionElement === element) renderCaption();
      });
      active.append(element);
      // A hidden text track loads cues even when the user has switched captions off.
      element.track.mode = 'hidden';
      renderCaption();
    }
    function update() {
      const ready = hasSource(), length = active.duration;
      root.dataset.mediaKind = active.tagName.toLowerCase() === 'video' ? 'video' : 'audio';
      play.disabled = !ready; seek.disabled = !ready || !Number.isFinite(length) || length <= 0;
      root.dataset.mediaPaused = String(active.paused);
      play.setAttribute('aria-label', active.paused ? play.dataset.playLabel : play.dataset.pauseLabel);
      current.textContent = time(active.currentTime); duration.textContent = time(length);
      if (!scrubbing) seek.value = Number.isFinite(length) && length > 0 ? active.currentTime / length * 1000 : 0;
      root.style.setProperty('--cm-played',(Number(seek.value) / 10) + '%');
      seek.setAttribute('aria-valuetext', time(active.currentTime) + ' / ' + time(length));
      buffer.value = active.buffered.length && length > 0 ? Math.min(1, active.buffered.end(active.buffered.length - 1) / length) : 0;
      mute.setAttribute('aria-label', active.muted ? mute.dataset.unmuteLabel : mute.dataset.muteLabel);
      mute.setAttribute('aria-pressed', String(active.muted)); volume.value = active.muted ? 0 : active.volume;
      speed.value = String(active.playbackRate);
      if (speedMenu) {
        speedMenu.hidden = active.tagName !== 'VIDEO';
        if (speedMenu.hidden) speedMenu.open = false;
        else {
          speedMenu.querySelector('[data-speed-current]').textContent = `${active.playbackRate}×`;
          for (const button of speedRates) button.setAttribute('aria-pressed', String(button.dataset.speedRate === speed.value));
        }
      }
      pip.hidden = active.tagName !== 'VIDEO' || !document.pictureInPictureEnabled || !active.requestPictureInPicture;
      fullscreen.hidden = active.tagName !== 'VIDEO' || (!root.requestFullscreen && typeof active.webkitEnterFullscreen !== 'function');
      if (rotate) rotate.hidden=active.tagName!=='VIDEO' || document.fullscreenElement!==root || typeof screen.orientation?.lock!=='function' || !matchMedia('(pointer:coarse)').matches;
      positionVideoCaption();
    }
    for (const media of elements) {
      media.controls = false;
      if (media.tagName==='VIDEO') media.tabIndex=0;
      if (media.tagName === 'VIDEO') {
        // Safari's native video screen owns its caption layer.
        mediaScope.listen(media,'webkitbeginfullscreen',() => { nativeFullscreen=true; updateCaptionMode(); });
        mediaScope.listen(media,'webkitendfullscreen',() => { nativeFullscreen=false; updateCaptionMode(); });
        mediaScope.listen(media, 'click', () => {
          if (speedMenu?.open) speedMenu.open = false;
          overlayHidden(!root.classList.contains('cm-controls-hidden'));
        });
      }
      mediaScope.listen(media, 'loadstart', () => {
        if (media.getAttribute('src')) { active = media; overlayHidden(false); loadCaptions(); update(); }
      });
      mediaScope.listen(media, 'play', () => {
        active = media;
        loadCaptions();
        for (const other of controllers) other.pauseExcept(media);
        for (const other of elements) if (other !== media) other.pause();
        status.textContent = ''; update();
      });
      for (const event of ['pause', 'ended', 'loadedmetadata', 'durationchange', 'timeupdate', 'progress', 'volumechange', 'ratechange', 'emptied']) mediaScope.listen(media, event, () => { if (media === active) { update(); } });
      mediaScope.listen(media, 'waiting', () => { if (media === active) status.textContent = status.dataset.loading; });
      mediaScope.listen(media, 'playing', () => { if (media === active) status.textContent = ''; });
      mediaScope.listen(media, 'error', () => { if (media === active && hasSource()) status.textContent = status.dataset.failed; });
      mediaScope.listen(media, 'emptied', () => {
        if (media === active && !media.hasAttribute('src')) loadCaptions();
      });
    }
    if (captionToggle) mediaScope.listen(captionToggle, 'click', () => {
      captionsEnabled = !captionsEnabled;
      updateCaptionMode();
    });
    mediaScope.listen(play, 'click', () => { if (active.paused) { active.play().catch(() => { status.textContent = status.dataset.failed; }); } else active.pause(); });
    for (const button of root.querySelectorAll('[data-media-skip]')) mediaScope.listen(button, 'click', () => { if (Number.isFinite(active.duration)) active.currentTime = Math.max(0, Math.min(active.duration, active.currentTime + Number(button.dataset.mediaSkip))); });
    mediaScope.listen(seek, 'input', () => { scrubbing = true; if (Number.isFinite(active.duration)) { active.currentTime = Number(seek.value) / 1000 * active.duration; current.textContent = time(active.currentTime); } });
    mediaScope.listen(seek, 'change', () => { scrubbing = false; update(); });
    mediaScope.listen(volume, 'input', () => { active.volume = Number(volume.value); active.muted = false; });
    mediaScope.listen(mute, 'click', () => { active.muted = !active.muted; });
    mediaScope.listen(speed, 'change', () => { active.playbackRate = Number(speed.value); });
    for (const button of speedRates) mediaScope.listen(button, 'click', () => {
      speed.value = button.dataset.speedRate;
      speed.dispatchEvent(new Event('change',{bubbles:true}));
      speedMenu.open = false;
      speedSummary.focus({preventScroll:true});
    });
    if (speedMenu) {
      mediaScope.listen(document, 'click', event => {
        if (speedMenu.open && !speedMenu.contains(event.target)) speedMenu.open = false;
      });
      mediaScope.listen(speedMenu, 'keydown', event => {
        if (event.key !== 'Escape' || !speedMenu.open) return;
        event.preventDefault(); event.stopPropagation(); speedMenu.open = false;
        speedSummary.focus({preventScroll:true});
      });
    }
    function overlayHidden(hidden) {
      root.classList.toggle('cm-controls-hidden',hidden);
      controls.inert=hidden;
      if (hidden) active.focus({preventScroll:true});
      positionVideoCaption();
    }
    mediaScope.listen(root, 'keydown', event => {
      if (active.tagName !== 'VIDEO' || !root.classList.contains('cm-controls-hidden') || !['Enter',' '].includes(event.key)) return;
      event.preventDefault(); overlayHidden(false); play.focus({preventScroll:true});
    });
    mediaScope.listen(document, 'fullscreenchange', () => {
      const expanded = document.fullscreenElement === root;
      overlayHidden(false);
      const label=expanded ? fullscreen.dataset.exitLabel : fullscreen.dataset.enterLabel;
      fullscreen.setAttribute('aria-label',label);
      fullscreen.title=label;
      fullscreen.setAttribute('aria-pressed',String(expanded));
      for (const media of elements) if (media.tagName === 'VIDEO') media.tabIndex=0;
      if (!expanded && orientationLocked) { screen.orientation.unlock(); orientationLocked=false; }
      update();
    });
    mediaScope.listen(fullscreen, 'click', () => { if (!root.requestFullscreen) { active.webkitEnterFullscreen?.(); return; } const request = document.fullscreenElement === root ? document.exitFullscreen() : root.requestFullscreen(); request?.catch(() => {}); });
    if (rotate) mediaScope.listen(rotate,'click',async () => {
      if (document.fullscreenElement!==root || typeof screen.orientation?.lock!=='function') return;
      rotate.disabled=true;
      try {
        await screen.orientation.lock(screen.orientation.type.startsWith('landscape') ? 'portrait' : 'landscape');
        if (document.fullscreenElement!==root || mediaScope.signal.aborted) { screen.orientation.unlock(); return; }
        orientationLocked=true;
      } catch (_) { if (!mediaScope.signal.aborted) status.textContent=rotate.dataset.failed; }
      finally { rotate.disabled=false; }
    });
    if (window.ResizeObserver) mediaScope.createObserver(ResizeObserver,positionVideoCaption).observe(root);
    mediaScope.listen(pip, 'click', () => { const request = document.pictureInPictureElement ? document.exitPictureInPicture() : active.requestPictureInPicture(); request?.catch(() => {}); });
    mediaScope.signal.addEventListener('abort', () => {
      if (orientationLocked) screen.orientation.unlock();
      for (const media of elements) { media.pause(); media.removeAttribute('src'); media.load(); }
    }, {once:true});
    const controller = {root,pauseExcept(media) { for (const element of elements) if (element !== media) element.pause(); },
      dispose() { mediaScope.dispose(); controllers.delete(controller); delete root.dataset.mediaMounted; }};
    controllers.add(controller);
    controls.hidden = false; root.classList.add('is-custom'); loadCaptions(); update();
  }
  }
  window.aelmsMountMedia = mount;
  mount();
  if (window.MutationObserver) scope.createObserver(MutationObserver, records => {
    for (const controller of controllers) if (!controller.root.isConnected) controller.dispose();
    for (const record of records) for (const node of record.addedNodes) if (node.nodeType === 1) mount(node);
  }).observe(document.body,{childList:true,subtree:true});
  scope.signal.addEventListener('abort',()=>{ for (const controller of controllers) controller.dispose(); },{once:true});
});
