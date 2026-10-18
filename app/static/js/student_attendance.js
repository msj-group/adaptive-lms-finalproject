/* Presentation only: Course-owned Group options and native GET/disclosure. */
(function () {
  "use strict";
  var root = document.querySelector("[data-attendance-workspace]");
  if (!root) return;
  var course = root.querySelector("[data-attendance-course]");
  var group = root.querySelector("[data-attendance-group]");
  var filters = root.querySelector("[data-attendance-filters]");
  if (course && group) {
    course.addEventListener("change", function () {
      Array.from(group.options).forEach(function (option) {
        var eligible = option.value === "all" || course.value === "all" || option.dataset.course === course.value;
        option.hidden = !eligible;
        option.disabled = !eligible;
      });
      group.value = "all";
    });
  }
  var from = root.querySelector('[name="date_from"]');
  var to = root.querySelector('[name="date_to"]');
  if (from && to) {
    from.addEventListener("change", function () { to.min = from.value; });
    to.addEventListener("change", function () { from.max = to.value; });
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
