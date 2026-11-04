/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Native disclosure/GET filters remain usable without this enhancement. */
(function () {
  "use strict";
  var root = document.querySelector("[data-record-workspace]");
  if (!root) return;
  var course = root.querySelector("[data-record-course]");
  var enrollment = root.querySelector("[data-record-enrollment]");
  var filters = root.querySelector("[data-record-filters]");
  if (course && enrollment) {
    pageScope.listen(course, "change", function () {
      Array.from(enrollment.options).forEach(function (option) {
        var eligible = option.value === "all" || course.value === "all" || option.dataset.course === course.value;
        option.hidden = !eligible;
        option.disabled = !eligible;
      });
      enrollment.value = "all";
    });
  }
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
