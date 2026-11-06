window.aelmsModules.register(document.currentScript, function (pageScope) {
  "use strict";
  const selector = "input[type=file][data-max-file-bytes]";
  function validate(input) {
    const limit = Number(input.dataset.maxFileBytes);
    const oversized = Array.from(input.files || []).some(file => file.size > limit);
    input.setCustomValidity(oversized ? input.dataset.fileSizeError : "");
    return !oversized;
  }
  pageScope.listen(document, "change", event => {
    if (event.target.matches(selector) && !validate(event.target)) event.target.reportValidity();
  });
  pageScope.listen(document, "submit", event => {
    for (const input of event.target.querySelectorAll(selector)) {
      if (!validate(input)) {
        event.preventDefault();
        input.reportValidity();
        break;
      }
    }
  });
});
