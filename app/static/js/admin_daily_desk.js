/* Direct enrollment entry. Only GETs are enhanced; the signed save stays native. */
(function () {
  "use strict";
  var dialog = document.querySelector("[data-enrollment-dialog]");
  if (!dialog || !dialog.showModal || !window.fetch || !window.AbortController ||
      !window.aelmsRegisterForms || !window.aelmsRegisterFinanceForms ||
      !window.aelmsFormHasChanges || !window.aelmsConfirm) return;
  var t = window.aelmsUI.t;
  var content = dialog.querySelector("[data-enrollment-content]");
  var status = dialog.querySelector("[data-enrollment-status]");
  var fallback = dialog.querySelector("[data-enrollment-fallback]");
  var entry = new URL(dialog.dataset.entryUrl, location.origin);
  var actor = dialog.dataset.actor;
  var controller = null, revision = 0, opener = null, changing = false;
  var reset = document.createElement("button");
  reset.type = "button"; reset.className = "btn btn--ghost";
  reset.textContent = t("Change student or group"); reset.hidden = true;
  dialog.querySelector(".dd-dialog-foot").prepend(reset);

  function cancelRead() { revision += 1; if (controller) controller.abort(); controller = null; }
  function releaseForms() {
    content.querySelectorAll("form").forEach(function (form) { form.dispatchEvent(new Event("workspace:saved")); });
  }
  async function discardAllowed() {
    return !window.aelmsFormHasChanges(content) || await window.aelmsConfirm(t("Discard the changed enrollment details?"));
  }
  async function load(url, operation, student, group) {
    cancelRead();
    var current = revision, request = new AbortController(); controller = request;
    status.textContent = t("Loading enrollment…");
    fallback.href = url.href;
    dialog.setAttribute("aria-busy", "true");
    try {
      var response = await fetch(url.href, {credentials:"same-origin", cache:"no-store", signal:request.signal,
        headers:{"Accept":"text/html"}});
      var destination = new URL(response.url);
      if (!response.ok || destination.origin !== location.origin ||
          ![entry.pathname, operation].includes(destination.pathname) ||
          !(response.headers.get("Content-Type") || "").includes("text/html")) throw new Error("unavailable");
      var documentCopy = new DOMParser().parseFromString(await response.text(), "text/html");
      var panel = documentCopy.querySelector("[data-enrollment-panel]");
      if (!panel || panel.dataset.enrollmentActor !== actor ||
          !["select","details"].includes(panel.dataset.enrollmentPhase)) throw new Error("unavailable");
      if (panel.dataset.enrollmentPhase === "details") {
        var save = panel.querySelector("form[method='post']");
        if (destination.pathname !== operation || !save ||
            save.querySelector("[name='student_public_id']").value !== student ||
            save.querySelector("[name='target_public_id']").value !== group) throw new Error("unavailable");
        // A form with no action ordinarily posts to its loaded document URL.
        save.action = destination.href;
      } else if (destination.pathname !== entry.pathname) throw new Error("unavailable");
      panel.querySelectorAll("script,style,iframe,object").forEach(function (node) { node.remove(); });
      if (current !== revision || request.signal.aborted || !dialog.open) return;
      // A draft may have changed while this GET was pending.
      if (!await discardAllowed()) { status.textContent = ""; return; }
      if (current !== revision || request.signal.aborted || !dialog.open) return;
      releaseForms();
      content.replaceChildren(document.importNode(panel, true));
      dialog.dataset.phase = panel.dataset.enrollmentPhase;
      reset.hidden = panel.dataset.enrollmentPhase !== "details";
      fallback.href = destination.href;
      window.aelmsRegisterForms(content);
      window.aelmsRegisterFinanceForms(content);
      status.textContent = "";
    } catch (_) {
      if (current === revision && !request.signal.aborted && dialog.open)
        status.textContent = t("Enrollment could not be loaded. Your changes remain here. Try again or open the full page.");
    } finally {
      if (current === revision) { controller = null; dialog.removeAttribute("aria-busy"); }
    }
  }
  async function close() {
    if (changing) return;
    changing = true;
    try {
      if (!await discardAllowed()) return;
      cancelRead(); releaseForms(); dialog.close(); content.replaceChildren();
    } finally { changing = false; }
  }
  document.querySelectorAll("[data-enrollment-open]").forEach(function (link) {
    link.addEventListener("click", function (event) {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.defaultPrevented) return;
      event.preventDefault();
      if (dialog.open) return;
      opener = link; dialog.dataset.phase = "select"; reset.hidden = true;
      dialog.showModal(); document.body.dataset.enrollmentOpen = "true";
      load(entry, null, null, null);
    });
  });
  dialog.querySelectorAll("[data-enrollment-close]").forEach(function (button) { button.addEventListener("click", close); });
  dialog.addEventListener("cancel", function (event) { event.preventDefault(); close(); });
  dialog.addEventListener("click", function (event) {
    if (event.target !== dialog) return;
    var rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
  });
  dialog.addEventListener("close", function () {
    cancelRead(); delete document.body.dataset.enrollmentOpen;
    if (opener && opener.isConnected) opener.focus({preventScroll:true});
  });
  reset.addEventListener("click", async function () {
    if (!await discardAllowed()) return;
    var form = content.querySelector("form[method='post']"), url = new URL(entry.href);
    if (form) {
      url.searchParams.set("student", form.querySelector("[name='student_public_id']").value);
      url.searchParams.set("group", form.querySelector("[name='target_public_id']").value);
    }
    // Discard was approved; release this draft before requesting new choices.
    releaseForms(); load(url, null, null, null);
  });
  dialog.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.matches("[data-enrollment-picker]") || event.defaultPrevented) return;
    event.preventDefault();
    var url = new URL(form.action, location.origin);
    if (url.origin !== entry.origin || url.pathname !== entry.pathname) return;
    url.search = new URLSearchParams(new FormData(form)).toString();
    url.searchParams.set("step", event.submitter && event.submitter.value || "search");
    var selected = form.querySelector("[name='group'] option:checked");
    var operation = selected && selected.dataset.operationUrl ? new URL(selected.dataset.operationUrl, location.origin).pathname : null;
    load(url, operation, form.elements.student.value, form.elements.group.value);
  });
  window.addEventListener("pagehide", function () { cancelRead(); if (dialog.open) dialog.close(); });
})();
