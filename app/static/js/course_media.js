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
    const fullscreen = root.querySelector('[data-media-fullscreen]'), status = root.querySelector('[data-media-status]');
    let active = elements.find(el => el.hasAttribute('src')) || elements[0];
    let scrubbing = false;
    const hasSource = () => Boolean(active.currentSrc || active.getAttribute('src'));
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
      pip.hidden = active.tagName !== 'VIDEO' || !document.pictureInPictureEnabled || !active.requestPictureInPicture;
      fullscreen.hidden = active.tagName !== 'VIDEO' || (!root.requestFullscreen && typeof active.webkitEnterFullscreen !== 'function');
    }
    for (const media of elements) {
      media.controls = false;
      mediaScope.listen(media, 'loadstart', () => { if (media.getAttribute('src')) { active = media; update(); } });
      mediaScope.listen(media, 'play', () => {
        active = media;
        for (const other of controllers) other.pauseExcept(media);
        for (const other of elements) if (other !== media) other.pause();
        status.textContent = ''; update();
      });
      for (const event of ['pause', 'ended', 'loadedmetadata', 'durationchange', 'timeupdate', 'progress', 'volumechange', 'ratechange', 'emptied']) mediaScope.listen(media, event, () => { if (media === active) { update(); } });
      mediaScope.listen(media, 'waiting', () => { if (media === active) status.textContent = status.dataset.loading; });
      mediaScope.listen(media, 'playing', () => { if (media === active) status.textContent = ''; });
      mediaScope.listen(media, 'error', () => { if (media === active && hasSource()) status.textContent = status.dataset.failed; });
    }
    mediaScope.listen(play, 'click', () => { if (active.paused) { active.play().catch(() => { status.textContent = status.dataset.failed; }); } else active.pause(); });
    for (const button of root.querySelectorAll('[data-media-skip]')) mediaScope.listen(button, 'click', () => { if (Number.isFinite(active.duration)) active.currentTime = Math.max(0, Math.min(active.duration, active.currentTime + Number(button.dataset.mediaSkip))); });
    mediaScope.listen(seek, 'input', () => { scrubbing = true; if (Number.isFinite(active.duration)) { active.currentTime = Number(seek.value) / 1000 * active.duration; current.textContent = time(active.currentTime); } });
    mediaScope.listen(seek, 'change', () => { scrubbing = false; update(); });
    mediaScope.listen(volume, 'input', () => { active.volume = Number(volume.value); active.muted = false; });
    mediaScope.listen(mute, 'click', () => { active.muted = !active.muted; });
    mediaScope.listen(speed, 'change', () => { active.playbackRate = Number(speed.value); });
    mediaScope.listen(fullscreen, 'click', () => { if (!root.requestFullscreen) { active.webkitEnterFullscreen?.(); return; } const request = document.fullscreenElement === root ? document.exitFullscreen() : root.requestFullscreen(); request?.catch(() => {}); });
    mediaScope.listen(pip, 'click', () => { const request = document.pictureInPictureElement ? document.exitPictureInPicture() : active.requestPictureInPicture(); request?.catch(() => {}); });
    mediaScope.signal.addEventListener('abort', () => {
      for (const media of elements) { media.pause(); media.removeAttribute('src'); media.load(); }
    }, {once:true});
    const controller = {root,pauseExcept(media) { for (const element of elements) if (element !== media) element.pause(); },
      dispose() { mediaScope.dispose(); controllers.delete(controller); delete root.dataset.mediaMounted; }};
    controllers.add(controller);
    controls.hidden = false; root.classList.add('is-custom'); update();
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
