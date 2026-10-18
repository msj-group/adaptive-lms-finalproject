/* Progressive presentation only. Original feedback routes own every read/write guard. */
(function () {
  "use strict";
  var t = window.aelmsUI ? window.aelmsUI.t : function (text) { return text; };
  var root = document.querySelector("[data-review-workbench]");
  var detail = root && root.querySelector("[data-review-detail]");
  var content = root && root.querySelector("[data-review-content]");
  var status = root && root.querySelector("[data-review-status]");
  var fallback = root && root.querySelector("[data-review-fallback]");
  var activeForm = null, original = "", forceDirty = false, nativeSubmitting = false;
  var saving = false, loading = false, selected = null, controller = null, sequence = 0;
  function dirty() {
    return activeForm && (forceDirty || activeForm.elements.feedback_text.value !== original);
  }
  async function mayLeave() {
    if (saving) return false;
    if (!dirty()) return true;
    return window.aelmsConfirm ? window.aelmsConfirm(t("Discard your unsaved feedback?"), t("Discard changes")) : window.confirm(t("Discard your unsaved feedback?"));
  }
  function bindEditor(editor) {
    activeForm = editor && editor.querySelector("[data-review-form]");
    forceDirty = !!(activeForm && activeForm.querySelector(".field__error"));
    original = activeForm ? activeForm.elements.feedback_text.value : "";
    if (!activeForm) return;
    var draft = activeForm.querySelector("[data-review-draft]");
    function draftState() { draft.hidden = !dirty(); }
    activeForm.elements.feedback_text.addEventListener("input", draftState);
    draftState();
  }
  bindEditor(document.querySelector("[data-review-editor]"));
  window.addEventListener("pageshow", function () { nativeSubmitting = false; });
  window.addEventListener("beforeunload", function (event) {
    if (!nativeSubmitting && (dirty() || saving)) { event.preventDefault(); event.returnValue = ""; }
  });
  document.addEventListener("click", async function (event) {
    var link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || link.target === "_blank" || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || !document.querySelector(".teacher-review")) return;
    if (link.hasAttribute("download") || link.closest(".assignment-file-receipt")) return;
    var destination = new URL(link.href, location.href);
    if (destination.pathname === location.pathname && destination.search === location.search && destination.hash) return;
    if (!saving && !dirty()) return;
    event.preventDefault();
    if (await mayLeave()) { nativeSubmitting = true; location.assign(link.href); }
  });
  document.addEventListener("submit", async function (event) {
    if ((event.target.getAttribute("method") || "get").toLowerCase() === "dialog") return;
    if (nativeSubmitting) return;
    if (!root && event.target.matches("[data-review-form]")) {
      nativeSubmitting = true;
      return;
    }
    if (root && event.target !== activeForm && (saving || dirty())) {
      event.preventDefault();
      if (await mayLeave()) {
        nativeSubmitting = true;
        event.target.setAttribute("data-research-resuming-submit", "");
        try { event.target.requestSubmit(event.submitter || undefined); }
        finally { event.target.removeAttribute("data-research-resuming-submit"); }
      }
    }
  });
  if (!root || !window.fetch || !window.AbortController || !window.DOMParser || !window.ResizeObserver) return;
  root.classList.add("review-enhanced");
  function wide() { return root.getBoundingClientRect().width >= 880; }
  function pauseAudio() { content.querySelectorAll("audio").forEach(function (audio) { audio.pause(); }); }
  function markSelected() {
    var list = root.querySelector("[data-review-list]");
    var rows = Array.from(list.querySelectorAll("[data-review-row]"));
    var chosenRow = null;
    rows.forEach(function (row, index) {
      if (!row.hasAttribute("data-review-order")) row.dataset.reviewOrder = String(index);
      var link = row.querySelector("[data-review-open]");
      var chosen = !!(selected && link && new URL(link.href).pathname === selected.pathname);
      if (chosen) chosenRow = row;
      row.classList.toggle("is-selected", chosen);
      row.querySelectorAll("[data-review-open]").forEach(function (item) {
        if (chosen) item.setAttribute("aria-current", "true"); else item.removeAttribute("aria-current");
      });
    });
    var focused = list.contains(document.activeElement) ? document.activeElement : null;
    rows.sort(function (a, b) {
      if (a === b) return 0;
      if (a === chosenRow) return -1;
      if (b === chosenRow) return 1;
      return Number(a.dataset.reviewOrder) - Number(b.dataset.reviewOrder);
    }).forEach(function (row, index) {
      if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
    });
    if (focused && document.activeElement !== focused) focused.focus({preventScroll:true});
    if (chosenRow) list.scrollTop = 0;
    root.querySelector("[data-review-order-label]").textContent = t(chosenRow ? "Selected first, then oldest" : "Oldest first");
  }
  function parsedEditor(text, expected) {
    var page = new DOMParser().parseFromString(text, "text/html");
    var editor = page.querySelector("[data-review-editor]");
    if (!editor || new URL(editor.dataset.feedbackUrl, location.origin).pathname !== expected.pathname) throw new Error("Unexpected feedback response");
    return editor;
  }
  function showEditor(editor, focus) {
    pauseAudio();
    content.replaceChildren(document.importNode(editor, true));
    bindEditor(content.querySelector("[data-review-editor]"));
    root.dataset.reviewLoaded = "true";
    detail.scrollTop = 0;
    if (activeForm && (focus === "feedback" || forceDirty)) {
      var field = activeForm.elements.feedback_text;
      field.focus({preventScroll:true});
      if (wide()) detail.scrollTop = Math.max(0, field.getBoundingClientRect().top - detail.getBoundingClientRect().top + detail.scrollTop - 56);
      else field.scrollIntoView({block:"nearest"});
    }
    else if (focus) content.querySelector("[data-review-title]").focus({preventScroll:true});
  }
  async function load(url, focus) {
    if (controller) controller.abort();
    var revision = ++sequence;
    var pending = new AbortController();
    controller = pending;
    selected = new URL(url, location.origin);
    if (selected.origin !== location.origin) return;
    var expected = selected;
    loading = true;
    detail.hidden = false;
    root.dataset.reviewLoaded = "true";
    pauseAudio();
    content.replaceChildren();
    bindEditor(null);
    status.textContent = t("Loading submission…");
    fallback.hidden = true;
    fallback.href = selected.href;
    markSelected();
    try {
      var response = await fetch(expected.href, {credentials:"same-origin", cache:"no-store", headers:{"X-Review-Panel":"1"}, signal:pending.signal});
      if (revision !== sequence || pending.signal.aborted) return;
      if (!response.ok || new URL(response.url).pathname !== expected.pathname) throw new Error("Feedback unavailable");
      var editor = parsedEditor(await response.text(), expected);
      if (revision !== sequence || pending.signal.aborted) return;
      showEditor(editor, focus);
      status.textContent = "";
    } catch (error) {
      if (!pending.signal.aborted && revision === sequence) {
        status.textContent = t("The submission could not load. Open its feedback page to try again.");
        fallback.hidden = false;
      }
    } finally {
      if (revision === sequence) loading = false;
    }
  }
  async function refreshQueue() {
    var response = await fetch(location.href, {credentials:"same-origin", cache:"no-store"});
    if (!response.ok || new URL(response.url).pathname !== location.pathname) throw new Error("Queue unavailable");
    var page = new DOMParser().parseFromString(await response.text(), "text/html");
    var list = page.querySelector("[data-review-list]");
    var pages = page.querySelector("[data-review-pagination]");
    if (!list || !pages) throw new Error("Queue unavailable");
    root.querySelector("[data-review-list]").replaceWith(document.importNode(list, true));
    root.querySelector("[data-review-pagination]").replaceWith(document.importNode(pages, true));
    markSelected();
  }
  root.addEventListener("click", async function (event) {
    var edit = event.target.closest("[data-review-edit]");
    if (edit && content.contains(edit) && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && event.button === 0) {
      event.preventDefault();
      if (!await mayLeave()) return;
      load(edit.href, "feedback");
      return;
    }
    var open = event.target.closest("[data-review-open]");
    if (open && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && event.button === 0 && wide()) {
      event.preventDefault();
      if (!await mayLeave()) return;
      forceDirty = false;
      load(open.href, true);
      return;
    }
    var back = event.target.closest("[data-review-back]");
    if (back && content.contains(event.target)) {
      event.preventDefault();
      if (!await mayLeave()) return;
      forceDirty = false;
      if (controller) controller.abort();
      ++sequence;
      loading = false;
      pauseAudio();
      bindEditor(null);
      content.replaceChildren();
      root.dataset.reviewLoaded = "false";
      detail.hidden = true;
      selected = null;
      markSelected();
      var first = root.querySelector("[data-review-open]");
      if (first) first.focus();
    }
  });
  root.addEventListener("submit", async function (event) {
    if (event.target !== activeForm) return;
    event.preventDefault();
    if (saving || loading) return;
    var form = activeForm;
    var expected = new URL(form.action, location.origin);
    if (!selected || expected.origin !== location.origin || expected.pathname !== selected.pathname) return;
    var body = new FormData(form);
    var submit = form.querySelector("[type='submit']");
    saving = true;
    submit.disabled = true;
    form.elements.feedback_text.readOnly = true;
    status.textContent = t("Saving feedback…");
    fallback.href = expected.href;
    try {
      var response = await fetch(expected.href, {method:"POST", body:body, credentials:"same-origin", cache:"no-store", headers:{"X-Review-Panel":"1"}});
      if (!response.ok || new URL(response.url).pathname !== expected.pathname) throw new Error("Save response unavailable");
      var editor = parsedEditor(await response.text(), expected);
      showEditor(editor, true);
      status.textContent = "";
      try { await refreshQueue(); }
      catch (error) { status.textContent = t("The review list could not refresh. Reload the page to see the current queue."); }
    } catch (error) {
      status.textContent = t("The save could not be confirmed. Your text is still here. Open the feedback page to check the saved comment before trying again.");
      fallback.hidden = false;
      // A failed network response may follow a committed POST. Never retry it automatically.
    } finally {
      saving = false;
      if (activeForm !== form) { submit.disabled = false; form.elements.feedback_text.readOnly = false; }
    }
  });
  var dateFilters = root.querySelector("[data-review-filters]");
  document.addEventListener("click", function (event) {
    if (dateFilters.open && !dateFilters.contains(event.target)) dateFilters.open = false;
  });
  dateFilters.addEventListener("keydown", function (event) {
    if (event.key === "Escape") { dateFilters.open = false; dateFilters.querySelector("summary").focus(); }
  });
  function resize() {
    root.dataset.reviewWide = String(wide());
    if (wide() && !selected && !loading) {
      detail.hidden = false;
      var first = root.querySelector("[data-review-open]");
      if (first) load(first.href, false);
      else status.textContent = t("Choose a submission when work is available.");
    }
  }
  new ResizeObserver(resize).observe(root);
  resize();
}());
