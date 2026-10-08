/* Display and unsaved-work protection. Activity-specific clients own saving. */
(function () {
  "use strict";
  var t = window.aelmsUI.t;
  var body = document.body;
  var preferenceKey = "aelms.display.v1";
  var preferences = {};
  try { preferences = JSON.parse(localStorage.getItem(preferenceKey) || "{}"); } catch (_) { preferences = {}; }
  if (!preferences || typeof preferences !== "object") preferences = {};
  function applyPreferences() {
    body.dataset.uiMotion = preferences.motion === "reduce" ? "reduce" : "system";
    body.dataset.uiDensity = preferences.density === "compact" ? "compact" : "comfortable";
  }
  applyPreferences();
  var confirmationDialog = document.createElement("dialog");
  confirmationDialog.className = "workspace-confirmation";
  confirmationDialog.setAttribute("aria-labelledby", "workspace-confirmation-title");
  confirmationDialog.innerHTML = '<h2 id="workspace-confirmation-title"></h2><p data-confirmation-message></p><form method="dialog"><button class="btn btn--secondary" value="cancel" autofocus></button><button class="btn btn--primary" value="continue"></button></form>';
  confirmationDialog.querySelector('h2').textContent = t('Review this action');
  confirmationDialog.querySelector('[value="cancel"]').textContent = t('Stay on this page');
  confirmationDialog.querySelector('[value="continue"]').textContent = t('Continue');
  body.appendChild(confirmationDialog);
  var confirmationResolve = null;
  var confirmationFocus = null;
  function confirmAction(message) {
    if (confirmationResolve) return Promise.resolve(false);
    return new Promise(function (resolve) {
      confirmationResolve = resolve; confirmationFocus = document.activeElement;
      confirmationDialog.querySelector("[data-confirmation-message]").textContent = t(message);
      confirmationDialog.returnValue = "cancel";
      confirmationDialog.showModal();
    });
  }
  confirmationDialog.addEventListener("close", function () {
    var resolve = confirmationResolve; confirmationResolve = null;
    if (confirmationFocus && confirmationFocus.isConnected) confirmationFocus.focus();
    if (resolve) resolve(confirmationDialog.returnValue === "continue");
  });
  window.aelmsConfirm = confirmAction;

  var dirtyForms = new Set();
  function formValue(form) {
    return Array.from(form.querySelectorAll("input:not([type='hidden']):not([type='submit']),textarea,select,[contenteditable='true']")).filter(function (field) { return !field.closest("[data-form-ui]"); }).map(function (field) {
      if (field.matches("[contenteditable]")) return field.innerHTML;
      return field.type === "checkbox" || field.type === "radio" ? field.checked : field.value;
    }).join("\u001f");
  }
  document.querySelectorAll("form").forEach(function (form) {
    if ((form.getAttribute("method") || "get").toLowerCase() !== "post" || form.matches("[data-login-form],[data-appearance-form],[data-form-ui],[data-quiz-submit]") || !form.querySelector("textarea,select,input:not([type='hidden']):not([type='submit']),[contenteditable]")) return;
    var baseline = formValue(form);
    var status = document.createElement("p");
    status.className = "workspace-unsaved";
    status.setAttribute("role", "status");
    status.hidden = true;
    form.prepend(status);
    function changed() {
      var dirty = baseline !== formValue(form);
      if (dirty) dirtyForms.add(form); else dirtyForms.delete(form);
      // The autosave client owns its visible status; retain unload protection.
      status.hidden = !dirty || form.matches("[data-quiz-autosave]");
      status.textContent = form.matches("[data-answer-form]") ? t("Unsaved answer — use Save answer or Save and next.") : t("Unsaved changes — save before leaving this page.");
    }
    form.addEventListener("input", changed);
    form.addEventListener("change", changed);
    form.addEventListener("workspace:saved", function () { baseline = formValue(form); dirtyForms.delete(form); status.hidden = true; });
    form.addEventListener("submit", function (event) {
      queueMicrotask(function () { if (!event.defaultPrevented) dirtyForms.delete(form); });
    });
  });
  var approvedForms = new WeakSet();
  function recordingPending() {
    var recorder = document.querySelector("[data-speaking-recorder]");
    return recorder && ["recording","requesting","preview","uploading"].includes(recorder.dataset.recorderState);
  }
  function discardPending() {
    dirtyForms.clear();
    var recorder = document.querySelector("[data-speaking-recorder]");
    if (recorder) recorder.dataset.recorderState = "discarded";
  }
  // Capture covers separate finish/finalize forms before an edited answer or draft is lost.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if ((form.getAttribute("method") || "get").toLowerCase() === "dialog") return;
    if (approvedForms.has(form)) { approvedForms.delete(form); return; }
    var attendance = Array.from(dirtyForms).find(function (form) { return form.matches("[data-attendance-draft]"); });
    if (attendance && event.target.matches("[data-finalize-attendance]")) {
      event.preventDefault(); event.stopImmediatePropagation();
      var draftStatus = attendance.querySelector(".workspace-unsaved");
      draftStatus.textContent = t("Save the changed draft marks before finalizing attendance.");
      attendance.scrollIntoView({block:"center"}); return;
    }
    var answer = Array.from(dirtyForms).find(function (form) { return form.matches("[data-answer-form]"); });
    if (answer && event.target !== answer) {
      event.preventDefault(); event.stopImmediatePropagation();
      var indicator = answer.querySelector(".workspace-unsaved");
      indicator.textContent = t("Save this answer before finishing the activity.");
      answer.scrollIntoView({block:"center"});
      var input = answer.querySelector("input:not([type='hidden']),textarea");
      if (input) input.focus();
      return;
    }
    var confirmation = event.submitter && event.submitter.dataset.confirm || (!form.matches("[data-speaking-form]") && form.dataset.confirm);
    var otherDirty = Array.from(dirtyForms).some(function (other) { return other !== form; });
    if (confirmation || otherDirty || recordingPending() && !form.matches("[data-speaking-form]")) {
      event.preventDefault(); event.stopImmediatePropagation();
      var submitter = event.submitter;
      confirmAction(confirmation || t("Another form or recording has unsaved work. Continue without saving that work?")).then(function (accepted) {
        if (!accepted) return;
        if (otherDirty || recordingPending() && !form.matches("[data-speaking-form]")) discardPending();
        approvedForms.add(form);
        // Continue the same user attempt after the native confirmation.
        form.setAttribute("data-research-resuming-submit", "");
        try { form.requestSubmit(submitter || undefined); }
        finally { form.removeAttribute("data-research-resuming-submit"); }
      });
    }
  }, true);
  window.addEventListener("beforeunload", function (event) {
    if (dirtyForms.size || recordingPending()) { event.preventDefault(); event.returnValue = ""; }
  });
  document.addEventListener("click", function (event) {
    var link = event.target.closest("a[href]");
    if (!link || link.target === "_blank" || event.ctrlKey || event.metaKey || !dirtyForms.size && !recordingPending()) return;
    var url = new URL(link.href, location.href);
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    event.preventDefault();
    confirmAction(t("You have unsaved work. Leave this page and discard it?")).then(function (accepted) { if (accepted) { discardPending(); location.assign(url.href); } });
  });
  document.querySelectorAll("form[method='post']").forEach(function (form) {
    // The recorder owns its asynchronous confirmation, upload and retry state.
    if (form.matches("[data-speaking-form]")) return;
    var submitting = false;
    form.addEventListener("submit", function (event) {
      if (event.defaultPrevented) return;
      if (submitting) { event.preventDefault(); return; }
      queueMicrotask(function () {
        if (event.defaultPrevented) return;
        submitting = true; form.setAttribute("aria-busy","true");
        if (event.submitter) { event.submitter.classList.add("workspace-submitting"); event.submitter.setAttribute("aria-disabled","true"); }
      });
    });
    window.addEventListener("pageshow", function () { submitting = false; form.removeAttribute("aria-busy"); form.querySelectorAll(".workspace-submitting").forEach(function (button) { button.classList.remove("workspace-submitting"); button.removeAttribute("aria-disabled"); }); });
  });

  document.querySelectorAll(".figma-page table").forEach(function (table) {
    // Editable grade/attendance matrices and compound headers keep their table semantics.
    if (table.querySelector("input,textarea,select,[rowspan],[colspan]") || !table.tHead || !table.tBodies.length) return;
    var headers = Array.from(table.tHead.rows[0].cells).map(function (cell) { return cell.textContent.trim(); });
    if (!headers.length) return;
    table.classList.add("workspace-record-table");
    Array.from(table.tBodies).forEach(function (section) { Array.from(section.rows).forEach(function (row) {
      Array.from(row.cells).forEach(function (cell, index) { cell.dataset.label = headers[index] || ""; });
    }); });
  });
  if (window.matchMedia("(max-width:767px)").matches) document.querySelectorAll(".workspace-filters").forEach(function (panel) { panel.open = false; });

  var errors = Array.from(document.querySelectorAll(".field__error")).filter(function (error) { return error.textContent.trim(); });
  if (errors.length) {
    var summary = document.createElement("section"); summary.className = "workspace-error-summary"; summary.tabIndex = -1; summary.setAttribute("role", "alert");
    var title = document.createElement("h2"); title.textContent = t("Check the highlighted fields"); summary.appendChild(title);
    var list = document.createElement("ul");
    errors.forEach(function (error, i) {
      error.id = error.id || "workspace-field-error-" + i;
      var field = error.closest(".field"); var input = field && field.querySelector("input:not([type='hidden']),select,textarea,[contenteditable]");
      var item = document.createElement("li");
      if (input) { input.id = input.id || "workspace-field-" + i; input.setAttribute("aria-invalid", "true"); input.setAttribute("aria-describedby", ((input.getAttribute("aria-describedby") || "") + " " + error.id).trim()); var anchor = document.createElement("a"); anchor.href = "#" + input.id; anchor.textContent = error.textContent; item.appendChild(anchor); }
      else item.textContent = error.textContent;
      list.appendChild(item);
    }); summary.appendChild(list);
    var page = document.querySelector(".figma-page"); if (page) { page.prepend(summary); summary.focus(); }
  }
  document.querySelectorAll("[data-copy]").forEach(function (button) { button.addEventListener("click", function () {
    if (!navigator.clipboard) { button.textContent = t("Select the value below to copy"); return; }
    navigator.clipboard.writeText(button.dataset.copy).then(function () { button.textContent = t("Copied"); }, function () { button.textContent = t("Select the value below to copy"); });
  }); });
  document.querySelectorAll("[data-print]").forEach(function (button) { button.addEventListener("click", function () { window.print(); }); });
  document.querySelectorAll("[data-finance-form]").forEach(function (form) {
    function fields() {
      var method = form.querySelector("[name='method']");
      var initialMethod = form.querySelector("[name='initial_method']");
      var action = form.querySelector("[name='action']");
      var discount = form.querySelector("[name='discount_kind']");
      ["bank_reference","bank_date","confirmed"].forEach(function (name) { var field = form.querySelector("[data-finance-field='" + name + "']"); if (field && method) field.hidden = method.value !== "bank_transfer"; });
      ["initial_bank_reference","initial_bank_date","initial_confirmed"].forEach(function (name) { var field = form.querySelector("[data-finance-field='" + name + "']"); if (field && initialMethod) field.hidden = initialMethod.value !== "bank_transfer"; });
      ["amount","discount_kind","discount_value"].forEach(function (name) { var field = form.querySelector("[data-finance-field='" + name + "']"); if (field && action) field.hidden = action.value !== "edit"; });
      var value = form.querySelector("[data-finance-field='discount_value']"); if (value && discount && (!action || action.value === "edit")) value.hidden = discount.value === "none";
      form.querySelector("[data-finance-output]").textContent = "";
      form.querySelector("[data-finance-note]").textContent = t("Review again after changing the form. The save performs the final validation.");
    }
    fields(); form.addEventListener("input", fields); form.addEventListener("change", fields);
    form.querySelector("[data-finance-preview]").addEventListener("click", async function (event) {
      var button = event.currentTarget; button.disabled = true;
      var output = form.querySelector("[data-finance-output]"); output.textContent = t("Calculating…");
      try { var response = await fetch(form.dataset.previewUrl, {method:"POST",body:new FormData(form),headers:{"Accept":"application/json"}});
        if (!(response.headers.get("Content-Type") || "").includes("application/json")) throw new Error("unavailable");
        var data = await response.json();
        output.textContent = data.error ? t(data.error) : t("Before: %(before)s → After: %(after)s LYD", {before:data.before,after:data.after});
        if (data.note) form.querySelector("[data-finance-note]").textContent = t(data.note);
      } catch (_) { output.textContent = t("Preview unavailable. Check your session and try again. Nothing was saved."); }
      finally { button.disabled = false; }
    });
  });
  document.querySelectorAll("[data-material-preview]").forEach(function (button) { button.addEventListener("click", async function () {
    var form = button.closest("form"); var output = form.querySelector("[data-material-preview-output]"); output.hidden = false; output.textContent = t("Preparing preview…"); button.disabled = true;
    try { var data = new FormData(); data.set("csrf_token", form.querySelector("[name='csrf_token']").value); data.set("content_html", form.querySelector(".rte-editor").innerHTML);
      var response = await fetch(button.dataset.previewUrl, {method:"POST",body:data,headers:{"Accept":"application/json"}});
      if (!response.ok || !(response.headers.get("Content-Type") || "").includes("application/json")) throw new Error("unavailable");
      var result = await response.json(); if (result.content_html) output.innerHTML = result.content_html; else output.textContent = t("No formatted content yet.");
    } catch (_) { output.textContent = t("Preview unavailable. Check your session and try again. Nothing was saved."); }
    finally { button.disabled = false; }
  }); var materialForm = button.closest("form"); materialForm.addEventListener("input", function () { var previewOutput = materialForm.querySelector("[data-material-preview-output]"); previewOutput.hidden = true; previewOutput.textContent = ""; }); });
  document.querySelectorAll("audio,video").forEach(function (media) {
    media.addEventListener("error", function () { if (media.nextElementSibling && media.nextElementSibling.dataset.mediaError) return;
      var notice = document.createElement("p"); notice.dataset.mediaError = "true"; notice.className = "alert alert--warning"; notice.setAttribute("role","status"); notice.textContent = t("This media could not be loaded. Reload the page or use the available download link."); media.after(notice); });
  });
  var timelineFilter = document.querySelector("[data-timeline-filter]");
  var dayPicker = document.querySelector("[data-calendar-day]");
  if (dayPicker) {
    function chooseDay() { document.querySelectorAll("[data-calendar-date]").forEach(function (day) { day.hidden = day.dataset.calendarDate !== dayPicker.value; }); }
    dayPicker.addEventListener("change", chooseDay); chooseDay();
  }
  if (timelineFilter) timelineFilter.addEventListener("change", function () {
    var count = 0;
    document.querySelectorAll("[data-event]").forEach(function (row) { row.hidden = Boolean(timelineFilter.value && row.dataset.event !== timelineFilter.value); if (!row.hidden) count++; });
    document.querySelector("[data-timeline-count]").textContent = t("%(count)s of the loaded events displayed", {count:count});
  });
  var heading = document.querySelector(".figma-page h1");
  if (heading && document.title === "Adaptive English LMS") {
    document.title = heading.textContent.trim() + " · Youth Centre";
    var label = document.querySelector(".figma-page-label"); if (label) label.textContent = heading.textContent.trim();
  }
})();
