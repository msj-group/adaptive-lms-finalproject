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
    const canvas = root.querySelector('[data-media-wave]'), pen = canvas.getContext('2d');
    const sources = new Map();
    let active = elements.find(el => el.hasAttribute('src')) || elements[0];
    let context = null, analyser = null, samples = null, frame = 0, lastDraw = 0, scrubbing = false;
    const hasSource = () => Boolean(active.currentSrc || active.getAttribute('src'));
    function update() {
      const ready = hasSource(), length = active.duration;
      root.dataset.mediaKind = active.tagName.toLowerCase() === 'video' ? 'video' : 'audio';
      play.disabled = !ready; seek.disabled = !ready || !Number.isFinite(length) || length <= 0;
      symbol.textContent = active.paused ? '▶' : 'Ⅱ';
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
    function graph() {
      try {
        const Type = window.AudioContext || window.webkitAudioContext;
        if (!Type) return;
        if (!context) context = new Type();
        if (!sources.has(active)) {
          const input = context.createMediaElementSource(active), node = context.createAnalyser();
          node.fftSize = 512; input.connect(node); node.connect(context.destination);
          sources.set(active, {input, node});
        }
        analyser = sources.get(active).node; samples = new Uint8Array(analyser.fftSize);
        context.resume().catch(() => {});
      } catch (_) { analyser = null; }
    }
    function draw(now = 0) {
      frame = 0;
      if (scope.signal.aborted || !root.isConnected) return;
      if (now - lastDraw > 40 || active.paused) {
        lastDraw = now;
        const size = canvas.getBoundingClientRect(), ratio = Math.min(2, window.devicePixelRatio || 1);
        const width = Math.max(1, Math.round(size.width * ratio)), height = Math.max(1, Math.round(size.height * ratio));
        if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
        if (pen) {
          pen.clearRect(0, 0, width, height); pen.lineWidth = 2 * ratio;
          const gradient = pen.createLinearGradient(0, 0, width, 0); gradient.addColorStop(0, '#7fa3e1'); gradient.addColorStop(1, '#b4d6ff'); pen.strokeStyle = gradient;
          pen.beginPath();
          if (analyser && samples && !active.paused) {
            analyser.getByteTimeDomainData(samples);
            const peak = samples.reduce((value, sample) => Math.max(value, Math.abs(sample - 128)), 0);
            const scale = Math.min(6, 100 / Math.max(12, peak));
            for (let i = 0; i < samples.length; i++) {
              const x = i / (samples.length - 1) * width, y = height / 2 + (samples[i] - 128) / 128 * height * .46 * scale;
              if (!i) pen.moveTo(x, y); else pen.lineTo(x, y);
            }
          } else { pen.moveTo(0, height / 2); pen.lineTo(width, height / 2); }
          pen.stroke();
        }
      }
      if (!active.paused && !document.hidden && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) frame = scope.requestAnimationFrame(draw);
    }
    function redraw() { if (frame) scope.cancelAnimationFrame(frame); frame = scope.requestAnimationFrame(draw); }
    for (const media of elements) {
      media.controls = false;
      scope.listen(media, 'loadstart', () => { if (media.getAttribute('src')) { active = media; analyser = sources.get(media)?.node || null; update(); } });
      scope.listen(media, 'play', () => {
        active = media;
        for (const other of controllers) other.pauseExcept(media);
        for (const other of elements) if (other !== media) other.pause();
        graph(); status.textContent = ''; update(); redraw();
      });
      for (const event of ['pause', 'ended', 'loadedmetadata', 'durationchange', 'timeupdate', 'progress', 'volumechange', 'ratechange', 'emptied']) scope.listen(media, event, () => { if (media === active) { update(); if (event !== 'timeupdate' && event !== 'progress') redraw(); } });
      scope.listen(media, 'waiting', () => { if (media === active) status.textContent = status.dataset.loading; });
      scope.listen(media, 'playing', () => { if (media === active) status.textContent = ''; });
      scope.listen(media, 'error', () => { if (media === active && hasSource()) status.textContent = status.dataset.failed; });
    }
    scope.listen(play, 'click', () => { if (active.paused) { graph(); active.play().catch(() => { status.textContent = status.dataset.failed; }); } else active.pause(); });
    for (const button of root.querySelectorAll('[data-media-skip]')) scope.listen(button, 'click', () => { if (Number.isFinite(active.duration)) active.currentTime = Math.max(0, Math.min(active.duration, active.currentTime + Number(button.dataset.mediaSkip))); });
    scope.listen(seek, 'input', () => { scrubbing = true; if (Number.isFinite(active.duration)) { active.currentTime = Number(seek.value) / 1000 * active.duration; current.textContent = time(active.currentTime); } });
    scope.listen(seek, 'change', () => { scrubbing = false; update(); });
    scope.listen(volume, 'input', () => { active.volume = Number(volume.value); active.muted = false; });
    scope.listen(mute, 'click', () => { active.muted = !active.muted; });
    scope.listen(speed, 'change', () => { active.playbackRate = Number(speed.value); });
    scope.listen(fullscreen, 'click', () => { const request = document.fullscreenElement ? document.exitFullscreen() : root.requestFullscreen(); request?.catch(() => {}); });
    scope.listen(pip, 'click', () => { const request = document.pictureInPictureElement ? document.exitPictureInPicture() : active.requestPictureInPicture(); request?.catch(() => {}); });
    scope.listen(document, 'visibilitychange', redraw);
    if (window.ResizeObserver) scope.createObserver(ResizeObserver, redraw).observe(canvas);
    scope.signal.addEventListener('abort', () => {
      for (const media of elements) { media.pause(); media.removeAttribute('src'); media.load(); }
      for (const {input, node} of sources.values()) { input.disconnect(); node.disconnect(); }
      if (context) context.close().catch(() => {});
    }, {once:true});
    controllers.push({pauseExcept(media) { for (const element of elements) if (element !== media) element.pause(); }});
    controls.hidden = false; root.classList.add('is-custom'); update(); redraw();
  }
});
