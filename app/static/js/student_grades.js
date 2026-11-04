/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Progressive presentation only. Server GET owns filtering and authorization. */
(function () {
  "use strict";
  var root = document.querySelector("[data-grade-workspace]");
  if (!root) return;
  var course = root.querySelector("[data-grade-course]");
  var term = root.querySelector("[data-grade-term]");
  var group = root.querySelector("[data-grade-group]");
  var groupField = root.querySelector("[data-grade-group-field]");
  var filters = root.querySelector("[data-grade-filters]");
  var form = root.querySelector("[data-grade-filter-form]");
  var search = root.querySelector("[data-grade-search]");
  var courseSearch = root.querySelector("[data-grade-course-search]");
  function updateGroups(reset) {
    if (!course || !term || !group || !groupField) return;
    if (reset) group.value = "all";
    Array.from(group.options).forEach(function (option) {
      var eligible = option.value === "all" || (option.dataset.course === course.value &&
        (term.value === "all" || option.dataset.term === term.value));
      option.hidden = !eligible;
      option.disabled = !eligible;
    });
    groupField.hidden = course.value === "all";
    group.disabled = course.value === "all";
  }
  if (course && term && group && groupField) {
    updateGroups(false);
    pageScope.listen(course, "change", function () { updateGroups(true); });
    pageScope.listen(term, "change", function () { updateGroups(true); });
  }
  if (courseSearch && course) {
    var options = Array.from(course.options).map(function (option) {
      return { value: option.value, text: option.textContent };
    });
    courseSearch.hidden = false;
    pageScope.listen(courseSearch, "keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        course.focus();
      }
    });
    pageScope.listen(courseSearch, "input", function () {
      var needle = courseSearch.value.trim().toLocaleLowerCase();
      var selected = course.value;
      course.replaceChildren();
      options.forEach(function (item) {
        if (item.value === "all" || item.value === selected || item.text.toLocaleLowerCase().includes(needle)) {
          var option = document.createElement("option");
          option.value = item.value;
          option.textContent = item.text;
          option.selected = item.value === selected;
          course.appendChild(option);
        }
      });
    });
  }
  if (form && search) {
    pageScope.listen(form, "submit", function () {
      form.querySelector("[data-grade-filter-query]").value = search.value;
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
