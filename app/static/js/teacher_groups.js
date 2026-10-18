/* Presentation only; native GETs own search/filtering and server scope. */
(function () {
  "use strict";
  var root = document.querySelector("[data-teacher-groups]");
  if (!root) return;
  var filters = root.querySelector("[data-group-filters]");
  var form = root.querySelector("[data-group-filter-form]");
  var search = root.querySelector("[data-group-search]");
  if (form && search) {
    form.addEventListener("submit", function () {
      form.querySelector("[data-group-filter-query]").value = search.value;
    });
  }
  if (filters) {
    document.addEventListener("click", function (event) {
      if (filters.open && !filters.contains(event.target)) filters.open = false;
    });
    filters.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && filters.open) {
        event.preventDefault();
        filters.open = false;
        filters.querySelector("summary").focus();
      }
    });
  }
})();
