/* Draft form convenience only. No requests, storage, roster or note changes. */
(function () {
  "use strict";
  var form = document.querySelector("[data-attendance-draft]");
  if (!form) return;
  var controls = form.querySelector("[data-attendance-bulk-controls]");
  if (!controls) return;
  var present = Array.from(form.querySelectorAll('input[type="radio"][name^="status__"][value="present"]'));
  if (!present.length) return;
  var radios = Array.from(form.querySelectorAll('input[type="radio"][name^="status__"]'));
  var mark = controls.querySelector("[data-attendance-all-present]");
  var undo = controls.querySelector("[data-attendance-undo]");
  var status = controls.querySelector("[data-attendance-bulk-status]");
  var t = window.aelmsUI.t;
  var previous = null;
  var applying = false;
  controls.hidden = false;
  function changed(inputs, message) {
    applying = true;
    inputs.forEach(function (input) { input.dispatchEvent(new Event("change", {bubbles: true})); });
    applying = false;
    status.textContent = t(message);
  }
  mark.addEventListener("click", function () {
    if (present.every(function (input) { return input.checked; })) return;
    previous = radios.map(function (input) { return input.checked; });
    present.forEach(function (input) { input.checked = true; });
    undo.hidden = false;
    changed(present, "Marks changed on this form only. Save draft to keep them.");
  });
  undo.addEventListener("click", function () {
    if (!previous) return;
    // Clear first, then restore each group to its previous selection.
    radios.forEach(function (input) { input.checked = false; });
    radios.forEach(function (input, index) { input.checked = previous[index]; });
    previous = null;
    undo.hidden = true;
    changed(radios, "Bulk selection undone. Your previous draft marks are restored on this form.");
  });
  form.addEventListener("change", function (event) {
    // A later manual mark takes priority over the bulk undo snapshot.
    if (!applying && event.target.matches('input[type="radio"][name^="status__"]')) {
      previous = null; undo.hidden = true; status.textContent = "";
    }
  });
})();
