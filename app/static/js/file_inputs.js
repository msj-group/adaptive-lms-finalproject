/* Keep the actual file input, form validation and upload contract native. */
(function () {
  "use strict";
  var t = window.aelmsUI.t;
  document.querySelectorAll("[data-file-control]").forEach(function (control) {
    var input = control.querySelector("input[type='file']");
    var button = control.querySelector("[data-file-button]");
    var name = control.querySelector("[data-file-name]");
    if (!input || !button || !name) return;
    var describedBy = (input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
    if (!describedBy.includes(name.id)) describedBy.push(name.id);
    input.setAttribute("aria-describedby", describedBy.join(" "));
    button.textContent = t(input.multiple ? "Choose files" : "Choose file");
    function update() {
      name.textContent = input.files && input.files.length
        ? Array.from(input.files).map(function (file) { return file.name; }).join(", ")
        : t("No file selected");
    }
    input.addEventListener("change", update);
    if (input.form) input.form.addEventListener("reset", function () { setTimeout(update, 0); });
    window.addEventListener("pageshow", update);
    update();
    button.hidden = false;
    name.hidden = false;
    control.dataset.fileEnhanced = "true";
  });
})();
