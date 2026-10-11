/* Private browser-only text drafts. Never POST, log, or emit research outcomes. */
(function () {
  "use strict";
  var scope = document.querySelector("[data-assignment-draft-scope]");
  if (!scope) return;
  var t = window.aelmsUI.t;
  var key = "aelms.assignment-draft.v1:" + [scope.dataset.draftStudent, scope.dataset.draftGroup, scope.dataset.draftAssignment].join(":");
  if (scope.dataset.draftSubmitted === "true") {
    try { localStorage.removeItem(key); }
    catch (_) {
      var receiptStatus = scope.querySelector("[data-draft-receipt-status]");
      receiptStatus.hidden = false;
      receiptStatus.textContent = t("Submitted. The saved draft could not be removed from this browser.");
    }
    return;
  }
  var form = scope.querySelector("[data-assignment-draft]");
  var context = scope.dataset.draftContext;
  var stored;
  var readFailed = false;
  function read() {
    var raw = localStorage.getItem(key);
    if (!raw) return null;
    var value = JSON.parse(raw);
    if (!value || value.version !== 1 || typeof value.answer !== "string" || typeof value.context !== "string") {
      throw new Error("Invalid local draft");
    }
    return value;
  }
  try { stored = read(); } catch (_) { readFailed = true; }
  if (!form) {
    if (stored) {
      scope.querySelector("[data-draft-archive]").hidden = false;
      scope.querySelector("[data-draft-archive-answer]").value = stored.answer;
    }
    return;
  }
  var answer = form.querySelector("[data-draft-answer]");
  var tools = form.querySelector("[data-draft-tools]");
  var status = form.querySelector("[data-draft-status]");
  var recovery = form.querySelector("[data-draft-recovery]");
  var recoveryAnswer = form.querySelector("[data-draft-recovery-answer]");
  var paused = false;
  var lastStored = stored || null;
  tools.hidden = false;
  function message(text, failed) {
    status.textContent = t(text);
    status.dataset.draftError = failed ? "true" : "false";
  }
  function markSaved() { form.dispatchEvent(new Event("workspace:saved")); }
  function recover(value, text) {
    paused = true;
    recovery.hidden = false;
    recoveryAnswer.value = value ? value.answer : "";
    form.querySelector("[data-draft-use]").disabled = !value;
    form.querySelector("[data-draft-recovery-message]").textContent = t(text);
    message("Automatic saving is paused. Choose which answer to keep.", true);
  }
  function save() {
    if (paused) return false;
    try {
      var latest = read();
      // Recheck before writing: a second tab may have saved since our last read.
      if (JSON.stringify(latest) !== JSON.stringify(lastStored)) {
        lastStored = latest;
        recover(latest, "The saved draft changed in another tab. Choose which answer to keep.");
        return false;
      }
      var value = {version: 1, context: context, answer: answer.value};
      localStorage.setItem(key, JSON.stringify(value));
      lastStored = value;
      message("Draft saved on this browser. Not submitted.", false);
      markSaved();
      return true;
    } catch (_) {
      message("Draft could not be saved on this browser. Keep this page open and copy your answer before leaving.", true);
      return false;
    }
  }
  if (readFailed) {
    message("Draft could not be loaded on this browser. Keep this page open and copy your answer before leaving.", true);
  } else if (stored && (stored.context !== context || answer.value && answer.value !== stored.answer)) {
    recover(stored, stored.context !== context
      ? "The assignment changed since this draft was saved. Review the instructions before using the saved draft."
      : "A different saved draft is available. Choose which answer to keep.");
  } else if (stored) {
    answer.value = stored.answer;
    markSaved();
    message("Draft restored from this browser. Not submitted.", false);
  } else {
    message("Automatic saving is on. Drafts stay on this browser.", false);
  }
  // Save every edit synchronously so closing/reloading cannot lose a debounce.
  answer.addEventListener("input", save);
  form.querySelector("[data-draft-use]").addEventListener("click", function () {
    answer.value = recoveryAnswer.value;
    paused = false;
    recovery.hidden = true;
    answer.dispatchEvent(new Event("input", {bubbles: true}));
    answer.focus();
  });
  form.querySelector("[data-draft-keep]").addEventListener("click", function () {
    paused = false;
    recovery.hidden = true;
    save();
  });
  window.addEventListener("storage", function (event) {
    if (event.key !== key && event.key !== null) return;
    try {
      var latest = read();
      if (JSON.stringify(latest) === JSON.stringify(lastStored)) return;
      lastStored = latest;
      recover(latest, "The saved draft changed in another tab. Choose which answer to keep.");
    } catch (_) {
      paused = true;
      message("Draft could not be loaded on this browser. Keep this page open and copy your answer before leaving.", true);
    }
  });
  // Re-authorize a cached form after Back, including a newly confirmed receipt.
  window.addEventListener("pageshow", function (event) { if (event.persisted) location.reload(); });
})();
