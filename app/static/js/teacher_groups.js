/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Presentation only; native GETs own search/filtering and server scope. */
(function () {
  "use strict";
  var root = document.querySelector("[data-teacher-groups]");
  if (!root) return;
  var filters = root.querySelector("[data-group-filters]");
  if (filters) {
    pageScope.listen(document, "click", function (event) {
      if (filters.open && !filters.contains(event.target)) filters.open = false;
    });
    pageScope.listen(filters, "keydown", function (event) {
      if (event.key === "Escape" && filters.open) {
        event.preventDefault();
        filters.open = false;
        filters.querySelector("summary").focus();
      }
    });
  }
})();

});
