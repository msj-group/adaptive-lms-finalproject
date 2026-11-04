/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
(() => {
  "use strict";

  const deviceReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const allowsMotion = () => !deviceReducedMotion.matches;

  function prepareEditor(root) {
    const offline = root.querySelector("[data-editor-offline]");
    const sync = () => { offline.hidden = navigator.onLine; };
    pageScope.listen(window, "online", sync);
    pageScope.listen(window, "offline", sync);
    pageScope.listen(root, "submit", (event) => {
      if (!navigator.onLine) {
        event.preventDefault();
        sync();
      }
    });
    sync();

    const book = root.querySelector("[data-book-browser]");
    if (book) prepareBook(root, book);
    const form = root.querySelector("[data-media-form]");
    if (form) preparePlacement(root, form);
  }

  function prepareBook(root, browser) {
    const viewer = browser.querySelector("[data-pdf-viewer]");
    const pages = Array.from(viewer.querySelectorAll("[data-book-page]"));
    const pageByNumber = new Map(pages.map(page => [Number(page.dataset.bookPage), page]));
    const jump = browser.querySelector("[data-jump-page]");
    const jumpToggle = browser.querySelector("[data-jump-toggle]");
    const jumpInput = jump.elements.namedItem("page");
    const current = browser.querySelector("[data-current-page]");
    const indexScroll = browser.querySelector("[data-index-scroll]");
    const preview = root.querySelector("[data-preview-link]");
    const editorReturn = root.querySelector("[data-editor-return]");
    const thumbs = new Map(Array.from(browser.querySelectorAll(".ce-thumb[data-page-target]"))
      .map(link => [Number(link.dataset.pageTarget), link]));
    const index = browser.querySelector("[data-index-panel]");
    const indexToggle = index.querySelector("[data-index-toggle]");
    const indexBody = index.querySelector("[data-index-body]");
    const narrow = window.matchMedia("(max-width: 840px)");
    let active = null;
    let matched = null;
    let scrollFrame = 0;

    function showIndex(open) {
      indexBody.hidden = !open;
      indexToggle.setAttribute("aria-expanded", String(open));
      if (open && active !== null) revealThumb(matched || thumbs.get(active));
    }
    showIndex(!narrow.matches);
    pageScope.listen(indexToggle, "click", () => showIndex(indexBody.hidden));
    pageScope.listen(narrow, "change", () => showIndex(!narrow.matches));
    for (const scroller of browser.querySelectorAll("[data-image-scroll]")) loadNearbyImages(scroller);

    function revealThumb(thumb, userJump = false) {
      if (!thumb || indexBody.hidden || !thumb.getClientRects().length) return;
      const bounds = thumb.getBoundingClientRect();
      const list = indexScroll.getBoundingClientRect();
      let top = indexScroll.scrollTop;
      let left = indexScroll.scrollLeft;
      if (bounds.top < list.top || bounds.bottom > list.bottom) {
        top += bounds.top - list.top - (list.height - bounds.height) / 2;
      }
      if (bounds.left < list.left || bounds.right > list.right) {
        left += bounds.left - list.left - (list.width - bounds.width) / 2;
      }
      if (top !== indexScroll.scrollTop || left !== indexScroll.scrollLeft) {
        indexScroll.scrollTo({top, left, behavior: userJump && allowsMotion() ? "smooth" : "instant"});
      }
    }

    function matchThumb() {
      matched?.removeAttribute("data-page-match");
      matched = jumpInput.validity.valid ? thumbs.get(jumpInput.valueAsNumber) : null;
      if (!matched) return;
      if (indexBody.hidden) showIndex(true);
      for (let node = matched.parentElement; node !== indexScroll; node = node.parentElement) {
        if (node.tagName === "DETAILS") node.open = true;
      }
      matched.setAttribute("data-page-match", "");
      revealThumb(matched, true);
    }

    function showJump(open) {
      jump.hidden = !open;
      jumpToggle.setAttribute("aria-expanded", String(open));
      if (open) {
        jumpInput.focus({preventScroll: true});
        jumpInput.select();
      }
    }

    function activate(page, keepIndex = false) {
      const number = Number(page.dataset.bookPage);
      if (number === active) return;
      active = number;
      current.textContent = number;
      if (!matched && document.activeElement !== jumpInput) jumpInput.value = number;
      for (const link of browser.querySelectorAll("[data-page-target][aria-current]")) link.removeAttribute("aria-current");
      for (const link of browser.querySelectorAll('[data-page-target="' + number + '"]')) {
        link.setAttribute("aria-current", "page");
      }
      if (!matched && !keepIndex) revealThumb(thumbs.get(number));
      if (preview) {
        const pagePreview = page.querySelector("[data-sheet]").dataset.lessonPreview;
        if (pagePreview) {
          preview.href = pagePreview + "#pdf-page-" + number;
          preview.removeAttribute("aria-disabled");
          preview.removeAttribute("title");
        } else {
          preview.removeAttribute("href");
          preview.setAttribute("aria-disabled", "true");
          preview.title = preview.dataset.unassignedLabel;
        }
      }
      if (editorReturn) editorReturn.hash = "pdf-page-" + number;
    }

    function visiblePage() {
      const position = viewer.scrollTop + Math.min(90, viewer.clientHeight / 3);
      let low = 0;
      let high = pages.length - 1;
      while (low < high) {
        const mid = Math.ceil((low + high) / 2);
        if (pages[mid].offsetTop <= position) low = mid;
        else high = mid - 1;
      }
      return pages[low];
    }

    function goTo(number, immediate = false) {
      const page = pageByNumber.get(Number(number));
      if (!page) return;
      const inset = parseFloat(getComputedStyle(viewer).paddingTop) || 0;
      const top = page.offsetTop - inset;
      const animate = !immediate && allowsMotion();
      const distant = Math.abs(top - viewer.scrollTop) > viewer.clientHeight * 3;
      const instant = !animate || distant;
      viewer.scrollTo({top, behavior: instant ? "instant" : "smooth"});
      if (animate && distant) viewer.animate([{opacity: .65}, {opacity: 1}], {duration: 120, easing: "ease-out"});
      jumpInput.value = number;
      if (instant) activate(page);
    }

    pageScope.listen(viewer, "scroll", () => {
      if (scrollFrame) return;
      const restoring = document.documentElement.hasAttribute("data-workspace-restoring");
      scrollFrame = pageScope.requestAnimationFrame(() => {
        scrollFrame = 0;
        activate(visiblePage(), restoring);
      });
    }, {passive: true});
    (preview || editorReturn)?.addEventListener("click", () => activate(visiblePage()));
    pageScope.listen(jumpToggle, "click", () => showJump(jump.hidden));
    pageScope.listen(jumpInput, "input", matchThumb);
    pageScope.listen(jump, "keydown", event => {
      if (event.key !== "Escape") return;
      showJump(false);
      jumpToggle.focus();
    });
    pageScope.listen(jump, "submit", event => {
      event.preventDefault();
      matchThumb();
    });
    pageScope.listen(root, "click", event => {
      const link = event.target.closest("[data-page-target]");
      if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      matched?.removeAttribute("data-page-match");
      matched = null;
      showJump(false);
      goTo(link.dataset.pageTarget);
    });
    pageScope.requestAnimationFrame(() => {
      const anchor = document.getElementById(location.hash.slice(1));
      const number = anchor?.closest("[data-book-page]")?.dataset.bookPage || browser.dataset.openPage;
      goTo(number, true);
    });

    if (browser.hasAttribute("data-division-browser")) {
      prepareRangeEditor(browser, () => active);
    }
  }

  function loadNearbyImages(scroller) {
    const images = scroller.querySelectorAll("img[data-pdf-src]");
    if (!("IntersectionObserver" in window)) {
      for (const image of images) {
        image.loading = "lazy";
        image.src = image.dataset.pdfSrc;
      }
      return;
    }
    const waiting = new Set();
    let loading = 0;
    const observer = pageScope.createObserver(IntersectionObserver, entries => {
      for (const entry of entries) {
        if (entry.isIntersecting) waiting.add(entry.target);
        else waiting.delete(entry.target);
      }
      load();
    }, {root: scroller, rootMargin: "160px"});
    function load() {
      while (loading < 3 && waiting.size) {
        const image = Array.from(waiting).find(item => item.closest("[data-page-match],[aria-current=page]"))
          || waiting.values().next().value;
        waiting.delete(image);
        observer.unobserve(image);
        loading += 1;
        const done = () => {
          image.removeEventListener("load", done);
          image.removeEventListener("error", done);
          loading -= 1;
          load();
        };
        pageScope.listen(image, "load", done);
        pageScope.listen(image, "error", done);
        image.src = image.dataset.pdfSrc;
      }
    }
    for (const image of images) observer.observe(image);
  }

  function prepareRangeEditor(browser, currentPage) {
    const slot = browser.querySelector("[data-range-slot]");
    const controls = browser.querySelector("[data-range-controls]");
    const panel = browser.querySelector("[data-division-panel]");
    const errorMessage = panel.querySelector("[data-range-load-error]");
    let pending = null;
    let trigger = null;
    let returnTop = 0;
    pageScope.listen(browser, "click", async event => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest("[data-edit-range]");
      const cancel = event.target.closest("[data-cancel-range]");
      const bound = event.target.closest("[data-select-bound]");
      if (bound) {
        const input = slot.querySelector('[name="page_' + bound.dataset.selectBound + '"]');
        const number = currentPage();
        if (input && number >= Number(input.min) && number <= Number(input.max)) {
          input.value = number;
          input.focus({preventScroll: true});
        }
      }
      if (cancel) {
        event.preventDefault();
        pending?.abort();
        const card = slot.firstElementChild;
        const finish = () => {
          if (slot.firstElementChild !== card) return;
          card?.remove();
          panel.scrollTo({top: returnTop, behavior: allowsMotion() ? "smooth" : "instant"});
        };
        if (card && allowsMotion()) {
          card.inert = true;
          const exit = card.animate([{opacity: 1}, {opacity: 0, translate: "0 -.25rem"}],
            {duration: 120, easing: "ease-in", fill: "forwards"});
          exit.onfinish = finish;
        } else {
          finish();
        }
        controls.hidden = true;
        errorMessage.hidden = true;
        trigger?.focus({preventScroll: true});
      }
      if (!link) return;
      event.preventDefault();
      if (!navigator.onLine) return;
      errorMessage.hidden = true;
      pending?.abort();
      const controller = new AbortController();
      const originTop = panel.scrollTop;
      pending = controller;
      const url = new URL(link.href);
      url.searchParams.set("fragment", "form");
      link.setAttribute("aria-busy", "true");
      try {
        const response = await pageScope.fetch(url, {signal: controller.signal});
        if (!response.ok) throw new Error("Range form unavailable");
        const html = new DOMParser().parseFromString(await response.text(), "text/html");
        const card = html.querySelector(".ce-range-card");
        if (!card) throw new Error("Range form unavailable");
        if (controller.signal.aborted) return;
        trigger = link;
        returnTop = originTop;
        slot.replaceChildren(card);
        controls.hidden = false;
        const paneBounds = panel.getBoundingClientRect();
        if (card.getBoundingClientRect().top < paneBounds.top) {
          panel.scrollTo({top: 0, behavior: allowsMotion() ? "smooth" : "instant"});
        }
        card.querySelector('[name="title"]').focus({preventScroll: true});
      } catch (error) {
        if (error.name !== "AbortError" && !controller.signal.aborted) errorMessage.hidden = false;
      } finally {
        link.removeAttribute("aria-busy");
      }
    });
  }

  function preparePlacement(root, form) {
    const viewer = root.querySelector("[data-pdf-viewer]");
    const file = form.elements.namedItem("media");
    const placement = root.querySelector("[data-placement]");
    const confirm = root.querySelector("[data-pin-confirm]");
    const cancel = root.querySelector("[data-pin-cancel]");
    const error = root.querySelector("[data-pin-error]");
    const add = root.querySelector("[data-add-media]");
    const invalid = root.querySelector("[data-invalid-media]");
    const unassigned = root.querySelector("[data-unassigned-page]");
    const savedMaterials = root.querySelector("#ce-saved-materials");
    const savedList = root.querySelector("#ce-material-list");
    let draft = null;
    let drag = null;
    let frame = 0;
    let saving = false;

    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    function canPlace(sheet) {
      const action = sheet.dataset.mediaAction;
      return Boolean(action && (!draft.original
        || draft.original.closest("[data-sheet]").dataset.mediaAction === action));
    }

    function updatePlacement() {
      confirm.disabled = saving || !draft.chosen;
      unassigned.hidden = canPlace(draft.sheet);
    }

    function sheetAt(y) {
      const rect = viewer.getBoundingClientRect();
      const center = rect.left + viewer.clientWidth / 2;
      const at = clamp(y, rect.top + 2, rect.bottom - 2);
      for (const offset of [0, 14, -14]) {
        const sheet = document.elementsFromPoint(center, clamp(at + offset, rect.top + 2, rect.bottom - 2))
          .map(element => element.closest("[data-sheet]")).find(element => element && viewer.contains(element));
        if (sheet) return sheet;
      }
      return draft?.sheet || viewer.querySelector("[data-sheet]");
    }

    function position() {
      if (!draft) return;
      const viewport = viewer.getBoundingClientRect();
      const sheet = draft.sheet.getBoundingClientRect();
      const radius = draft.marker.offsetWidth / 2;
      const x = clamp(sheet.width * draft.x / 10000, radius, Math.max(radius, sheet.width - radius));
      const y = clamp(sheet.height * draft.y / 10000, radius, Math.max(radius, sheet.height - radius));
      const left = sheet.left - viewport.left + viewer.scrollLeft + x;
      const top = sheet.top - viewport.top + viewer.scrollTop + y;
      draft.marker.style.left = Math.round(left) + "px";
      draft.marker.style.top = Math.round(top) + "px";

      const gap = radius + 8;
      const width = placement.offsetWidth;
      const height = placement.offsetHeight;
      const minLeft = viewer.scrollLeft + 4;
      const maxLeft = Math.max(minLeft, viewer.scrollLeft + viewer.clientWidth - width - 4);
      let actionTop = top - height / 2;
      let actionLeft = left + gap;
      if (actionLeft > maxLeft) actionLeft = left - gap - width;
      if (actionLeft < minLeft) {
        actionLeft = clamp(left - width / 2, minLeft, maxLeft);
        const fitsBelow = top + gap + height <= viewer.scrollTop + viewer.clientHeight;
        actionTop = fitsBelow ? top + gap : top - gap - height;
      }
      const bottom = viewer.scrollTop + viewer.clientHeight;
      if (top >= viewer.scrollTop && top <= bottom) {
        const minTop = viewer.scrollTop + 4;
        actionTop = clamp(actionTop, minTop, Math.max(minTop, bottom - height - 4));
      }
      placement.style.left = Math.round(actionLeft) + "px";
      placement.style.top = Math.round(actionTop) + "px";
    }

    function stopDrag() {
      if (!drag) return;
      pageScope.cancelAnimationFrame(frame);
      const {source, pointerId} = drag;
      drag = null;
      draft?.marker.classList.remove("ce-dragging");
      if (source.hasPointerCapture(pointerId)) source.releasePointerCapture(pointerId);
    }

    function clearDraft() {
      stopDrag();
      if (draft) {
        draft.original?.classList.remove("ce-pin--moving");
        draft.marker.remove();
        draft = null;
      }
      placement.hidden = true;
      confirm.disabled = true;
      error.hidden = true;
      unassigned.hidden = true;
    }

    function begin(category, title, original = null) {
      clearDraft();
      const view = viewer.getBoundingClientRect();
      const sheet = original?.closest("[data-sheet]") || sheetAt(view.top + 95);
      const marker = document.createElement("button");
      marker.type = "button";
      marker.className = "ce-pin ce-draft-pin";
      marker.dataset.category = category;
      marker.setAttribute("aria-label", title);
      marker.append(root.querySelector(category === "video" ? "[data-video-icon]" : "[data-audio-icon]")
        .content.cloneNode(true));
      const initialRect = sheet.getBoundingClientRect();
      draft = {
        marker, sheet, original, chosen: Boolean(original),
        x: original ? Number(original.dataset.pinX) : 2500,
        y: original ? Number(original.dataset.pinY)
          : clamp(Math.round((view.top + 95 - initialRect.top) / initialRect.height * 10000), 0, 10000),
      };
      original?.classList.add("ce-pin--moving");
      viewer.append(marker, placement);
      placement.hidden = false;
      updatePlacement();
      position();
    }

    function selectAt(clientX, clientY) {
      draft.sheet = sheetAt(clientY);
      const bounds = draft.sheet.getBoundingClientRect();
      draft.x = clamp(Math.round((clientX - bounds.left) / bounds.width * 10000), 0, 10000);
      draft.y = clamp(Math.round((clientY - bounds.top) / bounds.height * 10000), 0, 10000);
      draft.chosen = canPlace(draft.sheet);
      updatePlacement();
      position();
    }

    function tick() {
      if (!drag) return;
      const bounds = viewer.getBoundingClientRect();
      const edge = 42;
      const top = drag.y - bounds.top;
      const bottom = bounds.bottom - drag.y;
      const speed = top < edge ? -Math.min(17, (edge - top) / 3)
        : bottom < edge ? Math.min(17, (edge - bottom) / 3) : 0;
      if (speed && drag.moved) {
        viewer.scrollTop += speed;
        selectAt(drag.x, drag.y);
      }
      frame = pageScope.requestAnimationFrame(tick);
    }

    pageScope.listen(add, "click", () => file.click());
    pageScope.listen(file, "change", () => {
      const selected = file.files[0];
      if (!selected) return;
      const extension = selected.name.split(".").pop().toLowerCase();
      const audio = ["mp3", "wav"].includes(extension);
      const video = ["mp4", "webm"].includes(extension);
      invalid.hidden = audio || video;
      if (!audio && !video) {
        file.value = "";
        clearDraft();
        return;
      }
      const nonce = crypto.getRandomValues(new Uint8Array(16));
      form.elements.namedItem("nonce").value = Array.from(nonce, byte => byte.toString(16).padStart(2, "0")).join("");
      begin(audio ? "audio" : "video", selected.name);
      draft.marker.focus({ preventScroll: true });
    });

    pageScope.listen(viewer, "pointerdown", (event) => {
      const source = event.target.closest("[data-edit-pin], .ce-draft-pin");
      if (saving || !source || !event.isPrimary || event.button !== 0) return;
      event.preventDefault();
      if (source.hasAttribute("data-edit-pin") && draft?.original !== source) {
        file.value = "";
        begin(source.dataset.category, source.dataset.mediaTitle, source);
      }
      if (!draft) return;
      source.setPointerCapture(event.pointerId);
      drag = {source, pointerId: event.pointerId, x: event.clientX, y: event.clientY,
        startX: event.clientX, startY: event.clientY, moved: false,
        before: {sheet: draft.sheet, x: draft.x, y: draft.y, chosen: draft.chosen}};
      draft.marker.classList.add("ce-dragging");
      frame = pageScope.requestAnimationFrame(tick);
    });
    pageScope.listen(viewer, "pointermove", (event) => {
      if (drag?.pointerId !== event.pointerId) return;
      drag.x = event.clientX;
      drag.y = event.clientY;
      if (Math.hypot(drag.x - drag.startX, drag.y - drag.startY) > 3) drag.moved = true;
      if (drag.moved) selectAt(drag.x, drag.y);
    });
    pageScope.listen(viewer, "pointerup", (event) => {
      if (drag?.pointerId !== event.pointerId) return;
      if (drag.moved) selectAt(event.clientX, event.clientY);
      stopDrag();
    });
    function cancelPointer(event) {
      if (drag?.pointerId !== event.pointerId) return;
      Object.assign(draft, drag.before);
      stopDrag();
      updatePlacement();
      position();
    }
    pageScope.listen(viewer, "pointercancel", cancelPointer);
    pageScope.listen(viewer, "lostpointercapture", cancelPointer);
    pageScope.listen(viewer, "click", (event) => {
      const original = event.target.closest("[data-edit-pin]");
      if (!saving && original && draft?.original !== original) {
        file.value = "";
        begin(original.dataset.category, original.dataset.mediaTitle, original);
        draft.marker.focus({ preventScroll: true });
      }
    });
    pageScope.listen(viewer, "keydown", (event) => {
      if (saving || !draft || event.target !== draft.marker) return;
      const dx = {ArrowLeft: -250, ArrowRight: 250}[event.key] || 0;
      const dy = {ArrowUp: -250, ArrowDown: 250}[event.key] || 0;
      const page = {PageUp: -1, PageDown: 1}[event.key] || 0;
      if (!dx && !dy && !page) return;
      event.preventDefault();
      if (page) {
        const adjacent = viewer.querySelector('[data-sheet="' + (Number(draft.sheet.dataset.sheet) + page) + '"]');
        if (adjacent) draft.sheet = adjacent;
      }
      draft.x = clamp(draft.x + dx, 0, 10000);
      draft.y = clamp(draft.y + dy, 0, 10000);
      draft.chosen = canPlace(draft.sheet);
      updatePlacement();
      position();
      draft.marker.scrollIntoView({block: "nearest", inline: "nearest"});
    });

    pageScope.listen(viewer, "scroll", () => {
      if (!draft || draft.chosen || drag) return;
      const top = viewer.getBoundingClientRect().top;
      draft.sheet = sheetAt(top + 95);
      const rect = draft.sheet.getBoundingClientRect();
      draft.y = clamp(Math.round((top + 95 - rect.top) / rect.height * 10000), 0, 10000);
      updatePlacement();
      position();
    });
    if ("ResizeObserver" in window) pageScope.createObserver(ResizeObserver, position).observe(viewer);
    pageScope.listen(viewer, "load", position, true);

    pageScope.listen(cancel, "click", () => {
      const trigger = draft?.original || add;
      file.value = "";
      clearDraft();
      trigger.focus({preventScroll: true});
    });
    pageScope.listen(confirm, "click", () => {
      if (saving || !draft?.chosen || !canPlace(draft.sheet)) return;
      form.setAttribute("action", draft.sheet.dataset.mediaAction);
      form.elements.namedItem("material").value = draft.original?.dataset.editPin || "";
      form.elements.namedItem("material_version").value = draft.original?.dataset.materialVersion || "";
      form.elements.namedItem("pin_page").value = draft.sheet.dataset.sheet;
      form.elements.namedItem("pin_x").value = draft.x;
      form.elements.namedItem("pin_y").value = draft.y;
      form.requestSubmit();
    });

    pageScope.listen(form, "submit", async event => {
      event.preventDefault();
      if (saving || !navigator.onLine || !draft?.chosen) return;
      const data = new FormData(form);
      let message = placement.dataset.saveFailure;
      error.hidden = true;
      saving = true;
      add.disabled = cancel.disabled = confirm.disabled = true;
      placement.setAttribute("aria-busy", "true");
      try {
        const response = await pageScope.fetch(form.getAttribute("action"), {
          method: "POST", body: data, redirect: "error", headers: {Accept: "application/json"},
        });
        const result = await response.json();
        if (!response.ok) {
          message = result.error || message;
          throw new Error("Media save failed");
        }
        const sheet = viewer.querySelector('[data-sheet="' + Number(result.page) + '"]');
        const fragment = new DOMParser().parseFromString(result.html, "text/html");
        const pin = fragment.querySelector("[data-edit-pin]");
        const row = fragment.querySelector("li[data-order]");
        if (!sheet || !pin || !row) throw new Error("Media update unavailable");

        const previousPin = document.getElementById(pin.id);
        previousPin?.remove();
        sheet.append(pin);
        const previousRow = document.getElementById(row.id);
        const nextRow = Array.from(savedList.children).find(item => Number(item.dataset.order) > Number(row.dataset.order));
        if (previousRow) previousRow.replaceWith(row);
        else savedList.insertBefore(row, nextRow || null);
        if (Number(root.querySelector("[data-current-page]").textContent) === Number(result.page)) {
          row.querySelector("[data-page-target]").setAttribute("aria-current", "page");
        }
        savedMaterials.hidden = false;
        file.value = "";
        clearDraft();
        pin.focus({preventScroll: true});
      } catch (_) {
        error.textContent = message;
        error.hidden = false;
        position();
      } finally {
        saving = false;
        add.disabled = cancel.disabled = false;
        placement.removeAttribute("aria-busy");
        if (draft) updatePlacement();
      }
    });
  }

  for (const root of document.querySelectorAll("[data-curriculum-editor]")) prepareEditor(root);
  for (const dialog of document.querySelectorAll("[data-media-player]")) {
    const viewer = dialog.previousElementSibling;
    const audio = dialog.querySelector("audio");
    const video = dialog.querySelector("video");
    const error = dialog.querySelector("[data-player-error]");
    let playing = null;
    function stopPlayback() {
      if (!playing) return;
      const media = playing;
      playing = null;
      media.pause();
      media.removeAttribute("src");
      media.load();
      media.hidden = true;
    }
    pageScope.listen(viewer, "click", (event) => {
      const pin = event.target.closest("[data-play-pin]");
      if (!pin || dialog.open) return;
      stopPlayback();
      const media = pin.dataset.category === "video" ? video : audio;
      playing = media;
      media.hidden = false;
      error.hidden = true;
      dialog.querySelector("[data-player-title]").textContent = pin.dataset.mediaTitle;
      media.src = pin.dataset.mediaSrc;
      dialog.showModal();
      media.play().catch((failure) => {
        if (dialog.open && playing === media && !["AbortError", "NotAllowedError"].includes(failure.name)) {
          error.hidden = false;
        }
      });
    });
    for (const media of [audio, video]) {
      pageScope.listen(media, "error", () => { if (dialog.open && playing === media) error.hidden = false; });
    }
    dialog.querySelector("[data-close-player]").addEventListener("click", () => dialog.close());
    pageScope.listen(dialog, "click", (event) => {
      const rect = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right
          || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
    pageScope.listen(dialog, "close", () => {
      if (!dialog.open) stopPlayback();
    });
  }
})();

});
