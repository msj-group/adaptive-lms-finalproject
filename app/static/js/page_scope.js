/* Own the resources of one rendered page inside the persistent application. */
(() => {
  "use strict";
  const request = window.fetch.bind(window);

  window.aelmsCreatePageScope = function () {
    const controller = new AbortController();
    const observers = new Set();
    const timeouts = new Set();
    const intervals = new Set();
    const frames = new Set();

    return {
      signal: controller.signal,
      listen(target, type, listener, options = {}) {
        if (controller.signal.aborted) return;
        const settings = typeof options === "boolean" ? {capture: options} : {...options};
        settings.signal = settings.signal
          ? AbortSignal.any([controller.signal, settings.signal]) : controller.signal;
        target.addEventListener(type, listener, settings);
      },
      fetch(input, options = {}) {
        // The collector's final delivery is allowed to finish after its page leaves.
        if (options.keepalive) return request(input, options);
        const signal = options.signal
          ? AbortSignal.any([controller.signal, options.signal]) : controller.signal;
        return request(input, {...options, signal});
      },
      createObserver(Type, callback, options) {
        const observer = new Type((...args) => {
          if (!controller.signal.aborted) callback.apply(observer, args);
        }, options);
        observers.add(observer);
        return observer;
      },
      setTimeout(callback, delay, ...args) {
        if (controller.signal.aborted) return 0;
        const id = window.setTimeout(() => {
          timeouts.delete(id);
          if (!controller.signal.aborted) callback(...args);
        }, delay);
        timeouts.add(id);
        return id;
      },
      clearTimeout(id) { window.clearTimeout(id); timeouts.delete(id); },
      setInterval(callback, delay, ...args) {
        if (controller.signal.aborted) return 0;
        const id = window.setInterval(() => {
          if (!controller.signal.aborted) callback(...args);
        }, delay);
        intervals.add(id);
        return id;
      },
      clearInterval(id) { window.clearInterval(id); intervals.delete(id); },
      requestAnimationFrame(callback) {
        if (controller.signal.aborted) return 0;
        const id = window.requestAnimationFrame(time => {
          frames.delete(id);
          if (!controller.signal.aborted) callback(time);
        });
        frames.add(id);
        return id;
      },
      cancelAnimationFrame(id) { window.cancelAnimationFrame(id); frames.delete(id); },
      dispose() {
        controller.abort();
        for (const observer of observers) observer.disconnect();
        for (const id of timeouts) window.clearTimeout(id);
        for (const id of intervals) window.clearInterval(id);
        for (const id of frames) window.cancelAnimationFrame(id);
        observers.clear(); timeouts.clear(); intervals.clear(); frames.clear();
      },
    };
  };
  window.aelmsPage = window.aelmsCreatePageScope();
  const modules = new Map();
  window.aelmsModules = {
    register(script, initialize) {
      modules.set(script.src, initialize);
      this.run(script, script.aelmsPageScope || window.aelmsPage);
    },
    run(script, scope = window.aelmsPage) {
      const initialize = modules.get(script.src);
      if (!initialize) return false;
      if (scope.signal.aborted) return true;
      try { initialize(scope, script); }
      catch (error) { window.reportError(error); }
      return true;
    },
  };
})();
