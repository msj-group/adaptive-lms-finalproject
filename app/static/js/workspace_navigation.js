/* Render authorized server pages without replacing the browser document. */
(() => {
  "use strict";
  const scopeSelector = 'meta[name="aelms-workspace-scope"]';
  const persistent = "script[data-app-runtime]";
  const request = window.fetch.bind(window);
  let mountedURL = location.href;
  let bypass = false;
  let ready = false;
  let activeNavigation;

  function stopProgress(event) {
    if (activeNavigation === event) delete document.documentElement.dataset.appNavigating;
  }

  function hardVisit(url) {
    bypass = true;
    location.assign(url);
  }

  function errorDialog(message) {
    document.querySelectorAll(".workspace-submitting").forEach(button => {
      button.classList.remove("workspace-submitting");
      button.removeAttribute("aria-disabled");
    });
    window.dispatchEvent(new PageTransitionEvent("pageshow", {persisted: false}));
    const dialog = document.createElement("dialog");
    dialog.className = "app-flash-dialog";
    const text = document.createElement("p");
    text.textContent = window.aelmsUI?.t(message) || message;
    const form = document.createElement("form");
    form.method = "dialog";
    const close = document.createElement("button");
    close.className = "btn btn--primary";
    close.textContent = window.aelmsUI?.t("Close") || "Close";
    form.append(close);
    dialog.append(text, form);
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), {once: true});
    dialog.showModal();
  }

  function loadStyles(page, signal) {
    const current = Array.from(document.head.querySelectorAll('link[rel="stylesheet"]:not([data-app-pending])'));
    return Promise.all(Array.from(page.head.querySelectorAll('link[rel="stylesheet"]'), async link => {
      const existing = current.find(item => item.href === link.href);
      if (existing) return {node: existing, media: link.getAttribute("media")};
      const node = document.importNode(link, true);
      const media = node.getAttribute("media");
      node.dataset.appPending = "";
      node.media = "not all";
      await new Promise((resolve, reject) => {
        const abort = () => { node.remove(); reject(new DOMException("Navigation canceled", "AbortError")); };
        node.addEventListener("load", () => {
          signal.removeEventListener("abort", abort);
          resolve();
        }, {once: true});
        node.addEventListener("error", () => {
          signal.removeEventListener("abort", abort);
          node.remove(); reject(new Error("Styles unavailable"));
        }, {once: true});
        signal.addEventListener("abort", abort, {once: true});
        document.head.append(node);
      });
      return {node, media};
    }));
  }

  async function prepare(event, from) {
    // Capture the owner before a successful navigation replaces its page scope.
    // A finished HTTP exchange is transport evidence, never a business outcome.
    let research = null, researchAttempt = null;
    if (event.formData) {
      const source = event.researchForm || event.sourceElement;
      const form = source?.matches?.('form') ? source : source?.form || source?.closest?.('form');
      try {
        if (form && window.researchCollector) {
          research = window.researchCollector;
          researchAttempt = research.beginRequest({form, reuse_reserved:true});
          if (researchAttempt) for (const key of ['action_ref','attempt_ref','context_ref','scope_token','context_code']) {
            const field = form.querySelector(`input[name="research_${key}"]`);
            if (field) event.formData.set(`research_${key}`, field.value);
          }
        }
      } catch (_) { /* Research cannot interrupt the native request. */ }
    }
    const options = {credentials: "same-origin", cache: "no-store", signal: event.signal,
      referrer: from, headers: {Accept: "text/html"}};
    if (event.formData) {
      options.method = "POST";
      options.body = event.formData;
    }
    let response;
    try {
      response = await request(event.destination.url, options);
    } catch (error) {
      try { research?.finishRequest(researchAttempt, 'network_unknown'); } catch (_) {}
      throw error;
    }
    const destination = new URL(response.url);
    destination.hash = new URL(event.destination.url).hash;
    if (destination.origin !== location.origin ||
        !(response.headers.get("Content-Type") || "").includes("text/html") ||
        response.headers.get("Content-Disposition")?.includes("attachment")) {
      try { research?.finishRequest(researchAttempt, 'finished'); } catch (_) {}
      hardVisit(destination.href);
      throw new DOMException("Native destination", "AbortError");
    }
    let html;
    try {
      html = await response.text();
      try { research?.finishRequest(researchAttempt, 'finished'); } catch (_) {}
    } catch (error) {
      try { research?.finishRequest(researchAttempt, 'network_unknown'); } catch (_) {}
      throw error;
    }
    const page = new DOMParser().parseFromString(html, "text/html");
    const scope = document.querySelector(scopeSelector)?.content;
    if (!scope || page.querySelector(scopeSelector)?.content !== scope) {
      hardVisit(destination.href);
      throw new DOMException("Authentication boundary", "AbortError");
    }
    const styles = await loadStyles(page, event.signal);
    if (event.signal.aborted) {
      for (const {node} of styles) if (node.hasAttribute("data-app-pending")) node.remove();
    }
    event.signal.throwIfAborted();
    return {page, styles, destination};
  }

  async function runScripts(scripts, signal, scope) {
    for (const original of scripts) {
      signal.throwIfAborted();
      if (original.matches(persistent)) continue;
      if (original.src && window.aelmsModules.run(original, scope)) continue;
      const script = document.createElement("script");
      script.aelmsPageScope = scope;
      for (const attribute of original.attributes) script.setAttribute(attribute.name, attribute.value);
      script.async = false;
      script.removeAttribute("defer");
      script.textContent = original.textContent;
      if (script.src) {
        await new Promise((resolve, reject) => {
          const abort = () => { script.remove(); reject(new DOMException("Navigation canceled", "AbortError")); };
          script.addEventListener("load", () => {
            signal.removeEventListener("abort", abort);
            resolve();
          }, {once: true});
          script.addEventListener("error", () => {
            signal.removeEventListener("abort", abort);
            reject(new Error("Script unavailable"));
          }, {once: true});
          signal.addEventListener("abort", abort, {once: true});
          document.body.append(script);
        });
      } else document.body.append(script);
    }
  }

  async function render(prepared, event) {
    event.signal.throwIfAborted();
    const {page, styles, destination} = prepared;
    const scripts = Array.from(page.querySelectorAll("script"))
      .filter(script => !script.type || ["text/javascript", "application/javascript", "module"].includes(script.type));
    for (const script of scripts) script.remove();

    const previousReader = document.querySelector('[data-student-reader]');
    const nextReader = page.querySelector('[data-student-reader]');
    const fullscreenRoot = document.documentElement;
    const readerExpanded = document.fullscreenElement === fullscreenRoot || fullscreenRoot.hasAttribute('data-reader-expanded');
    const keepReader = readerExpanded && previousReader && nextReader &&
      previousReader.dataset.readerGroup === nextReader.dataset.readerGroup;
    if (keepReader) fullscreenRoot.dataset.readerTransition = 'true';
    else if (previousReader && readerExpanded) {
      fullscreenRoot.removeAttribute('data-reader-expanded');
      if (document.fullscreenElement === fullscreenRoot) await document.exitFullscreen();
    }
    try {

      window.aelmsDisposeMessages?.();
      document.querySelectorAll("audio,video").forEach(media => media.pause());
      document.dispatchEvent(new Event('aelms:page-leaving'));
      window.aelmsPage.dispose();
      document.documentElement.removeAttribute("data-workspace-restoring");
      for (const key of Object.keys(window)) {
        if (/^(aelms(?!CreatePageScope$|Page$|Modules$|ReaderNavigate$|InstallApp$|DevicePush$)|researchCollector$|AELMS_AR$)/.test(key)) delete window[key];
      }
      window.aelmsPage = window.aelmsCreatePageScope();
      for (const item of Array.from(document.head.children)) {
        if (item.matches(persistent) || styles.some(style => style.node === item)) continue;
        item.remove();
      }
      for (const item of Array.from(page.head.children)) {
        if (item.matches('link[rel="stylesheet"]')) continue;
        document.head.append(document.importNode(item, true));
      }
      for (const {node, media} of styles) {
        node.removeAttribute("data-app-pending");
        if (media === null) node.removeAttribute("media");
        else node.media = media;
        if (document.head.moveBefore) document.head.moveBefore(node, null);
        else document.head.append(node);
      }
      await Promise.all(styles.map(({node}) => node.sheet ? undefined : new Promise((resolve, reject) => {
        node.addEventListener("load", resolve, {once: true});
        node.addEventListener("error", () => reject(new Error("Styles unavailable")), {once: true});
      })));
      event.signal.throwIfAborted();
      document.documentElement.lang = page.documentElement.lang;
      document.documentElement.dir = page.documentElement.dir;
      document.body.replaceWith(document.importNode(page.body, true));
      mountedURL = location.href;
      await runScripts(scripts, event.signal, window.aelmsPage);
      event.signal.throwIfAborted();
      document.dispatchEvent(new Event("DOMContentLoaded"));
      window.dispatchEvent(new Event("load"));
      window.dispatchEvent(new PageTransitionEvent("pageshow", {persisted: false}));
      if (location.hash) {
        const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
        if (target && !target.closest("[data-pdf-viewer]")) target.scrollIntoView({block: "start"});
      } else {
        window.scrollTo({top: 0, behavior: "instant"});
      }
      document.dispatchEvent(new CustomEvent("aelms:page-ready"));
      mountedURL = location.href;
      const view = document.querySelector("#workspace-content,[data-curriculum-editor]") || document.querySelector("main");
      if (document.activeElement === document.body) view?.focus({preventScroll: true});
      if (view && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
        view.animate([{opacity: .6, translate: "0 4px"}, {opacity: 1, translate: "0 0"}],
          {duration: 160, easing: "ease-out"});
      }
    } finally { delete fullscreenRoot.dataset.readerTransition; }
  }

  // The root document stays fullscreen while an authorized reader page changes.
  // This path also works in browsers without Navigation API interception.
  let readerRequest = null;
  window.aelmsReaderNavigate = async function (url, formData = null, researchForm = null) {
    if (readerRequest || (document.fullscreenElement !== document.documentElement &&
        !document.documentElement.hasAttribute('data-reader-expanded')) ||
        !document.querySelector('[data-student-reader]')) return;
    const destination = new URL(url, location.href);
    if (destination.origin !== location.origin) return;
    const controller = new AbortController();
    const event = {signal:controller.signal, destination:{url:destination.href}, formData, researchForm};
    readerRequest = controller; activeNavigation = event;
    document.documentElement.dataset.appNavigating = 'true';
    try {
      const prepared = await prepare(event, location.href);
      event.signal.throwIfAborted();
      window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:false}));
      history.pushState(null, '', prepared.destination.href);
      await render(prepared, event);
    } catch (error) {
      if (error.name !== 'AbortError') errorDialog(formData
        ? 'The request could not be confirmed. Review the page before retrying.'
        : 'This page could not be loaded. Check your connection and try again.');
    } finally {
      readerRequest = null; stopProgress(event);
    }
  };

  window.aelmsReaderNavigate.supportsExpandedReader = true;
  if (!window.navigation || !window.NavigationPrecommitController) window.addEventListener('popstate', () => {
    const previous = new URL(mountedURL);
    if (document.querySelector('[data-student-reader]') &&
        (previous.pathname !== location.pathname || previous.search !== location.search)) location.reload();
  });

  if (window.navigation && window.NavigationPrecommitController) navigation.addEventListener("navigate", event => {
    if (readerRequest && !event.destination.sameDocument) readerRequest.abort();
    if (bypass) { bypass = false; return; }
    if (!ready || !event.canIntercept || event.hashChange || event.navigationType === "reload" ||
        event.downloadRequest !== null) return;
    const target = new URL(event.destination.url);
    if (target.origin !== location.origin || !document.querySelector(scopeSelector)?.content ||
        /\.(pdf|csv|zip|mp3|mp4|wav|webm|xlsx|docx)$/i.test(target.pathname) ||
        /\/(logout|login|download|file|audio|photo|stream)(\/|$)/.test(target.pathname)) return;
    if (event.destination.sameDocument && event.navigationType !== "traverse") {
      mountedURL = target.href;
      return;
    }
    const current = new URL(mountedURL);
    if (event.destination.sameDocument && target.pathname === current.pathname &&
        target.search === current.search) return;
    const from = location.href;
    let prepared;
    event.intercept({
      scroll: "manual", focusReset: "manual",
      async precommitHandler(controller) {
        const leaving = new Event("beforeunload", {cancelable: true});
        if (!event.formData) window.dispatchEvent(leaving);
        if (leaving.defaultPrevented) {
          const accepted = await window.aelmsConfirm?.(
            "You have unsaved work. Leave this page and discard it?", window.aelmsUI?.t("Leave this page"));
          if (!accepted) throw new DOMException("Navigation canceled", "AbortError");
        }
        activeNavigation = event;
        document.documentElement.dataset.appNavigating = "true";
        try {
          prepared = await prepare(event, from);
          if (prepared.destination.href !== event.destination.url) controller.redirect(prepared.destination.href);
          window.dispatchEvent(new PageTransitionEvent("pagehide", {persisted: false}));
        } catch (error) {
          stopProgress(event);
          if (error.name !== "AbortError") {
            errorDialog(event.formData
              ? "The request could not be confirmed. Review the page before retrying."
              : "This page could not be loaded. Check your connection and try again.");
          }
          throw error;
        }
      },
      async handler() {
        try { await render(prepared, event); }
        catch (error) {
          if (error.name !== "AbortError") errorDialog("This page could not be loaded. Check your connection and try again.");
        } finally { stopProgress(event); }
      },
    });
  });
  document.addEventListener("DOMContentLoaded", () => { ready = true; }, {once: true});
})();
