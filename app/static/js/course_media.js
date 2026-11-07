/* Scoped, same-origin media controls. Existing HTML media events remain authoritative. */
window.aelmsModules.register(document.currentScript, function (scope) {
  'use strict';
  const controllers = [];
  const time = value => {
    if (!Number.isFinite(value) || value < 0) return '0:00';
    const seconds = Math.floor(value);
    return (seconds >= 3600 ? Math.floor(seconds / 3600) + ':' : '') +
      String(Math.floor(seconds / 60) % 60).padStart(seconds >= 3600 ? 2 : 1, '0') + ':' + String(seconds % 60).padStart(2, '0');
  };
  for (const root of document.querySelectorAll('[data-course-media]')) {
    if (root.dataset.mediaMounted) continue;
    root.dataset.mediaMounted = 'true';
    const elements = [...root.querySelectorAll('audio,video')];
    const controls = root.querySelector('[data-media-controls]');
    if (!elements.length || !controls) continue;
    const play = root.querySelector('[data-media-play]'), symbol = root.querySelector('[data-play-symbol]');
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
      symbol.textContent = active.paused ? '\u25b6' : '\u2161';
      play.setAttribute('aria-label', active.paused ? play.dataset.playLabel : play.dataset.pauseLabel);
      current.textContent = time(active.currentTime); duration.textContent = time(length);
      if (!scrubbing) seek.value = Number.isFinite(length) && length > 0 ? active.currentTime / length * 1000 : 0;
      seek.setAttribute('aria-valuetext', time(active.currentTime) + ' / ' + time(length));
      buffer.value = active.buffered.length && length > 0 ? Math.min(1, active.buffered.end(active.buffered.length - 1) / length) : 0;
      mute.setAttribute('aria-label', active.muted ? mute.dataset.unmuteLabel : mute.dataset.muteLabel);
      mute.setAttribute('aria-pressed', String(active.muted)); volume.value = active.muted ? 0 : active.volume;
      speed.value = String(active.playbackRate);
      pip.hidden = active.tagName !== 'VIDEO' || !document.pictureInPictureEnabled || !active.requestPictureInPicture;
      fullscreen.hidden = active.tagName !== 'VIDEO' || !root.requestFullscreen;
    }
    for (const media of elements) {
      media.controls = false;
      scope.listen(media, 'loadstart', () => { if (media.getAttribute('src')) { active = media; update(); } });
      scope.listen(media, 'play', () => {
        active = media;
        for (const other of controllers) other.pauseExcept(media);
        for (const other of elements) if (other !== media) other.pause();
        status.textContent = ''; update();
      });
      for (const event of ['pause', 'ended', 'loadedmetadata', 'durationchange', 'timeupdate', 'progress', 'volumechange', 'ratechange', 'emptied']) scope.listen(media, event, () => { if (media === active) { update(); } });
      scope.listen(media, 'waiting', () => { if (media === active) status.textContent = status.dataset.loading; });
      scope.listen(media, 'playing', () => { if (media === active) status.textContent = ''; });
      scope.listen(media, 'error', () => { if (media === active && hasSource()) status.textContent = status.dataset.failed; });
    }
    scope.listen(play, 'click', () => { if (active.paused) { active.play().catch(() => { status.textContent = status.dataset.failed; }); } else active.pause(); });
    for (const button of root.querySelectorAll('[data-media-skip]')) scope.listen(button, 'click', () => { if (Number.isFinite(active.duration)) active.currentTime = Math.max(0, Math.min(active.duration, active.currentTime + Number(button.dataset.mediaSkip))); });
    scope.listen(seek, 'input', () => { scrubbing = true; if (Number.isFinite(active.duration)) { active.currentTime = Number(seek.value) / 1000 * active.duration; current.textContent = time(active.currentTime); } });
    scope.listen(seek, 'change', () => { scrubbing = false; update(); });
    scope.listen(volume, 'input', () => { active.volume = Number(volume.value); active.muted = false; });
    scope.listen(mute, 'click', () => { active.muted = !active.muted; });
    scope.listen(speed, 'change', () => { active.playbackRate = Number(speed.value); });
    scope.listen(fullscreen, 'click', () => { const request = document.fullscreenElement === root ? document.exitFullscreen() : root.requestFullscreen(); request?.catch(() => {}); });
    scope.listen(pip, 'click', () => { const request = document.pictureInPictureElement ? document.exitPictureInPicture() : active.requestPictureInPicture(); request?.catch(() => {}); });
    scope.signal.addEventListener('abort', () => {
      for (const media of elements) { media.pause(); media.removeAttribute('src'); media.load(); }
    }, {once:true});
    controllers.push({pauseExcept(media) { for (const element of elements) if (element !== media) element.pause(); }});
    controls.hidden = false; root.classList.add('is-custom'); update();
  }
});
