/* Native GETs/disclosures work alone. This only refines local presentation. */
(function () {
  "use strict";
  var root = document.querySelector("[data-teacher-activities]");
  if (!root) return;
  var filters = root.querySelector("[data-activity-filters]");
  var course = root.querySelector("[data-activity-course]");
  var group = root.querySelector("[data-activity-group]");
  var create = root.querySelector("[data-activity-create]");
  var wasCompact;
  function fitCreationPanel() {
    if (!create) return;
    // Match the workspace container breakpoint, including sidebar/zoom changes.
    var compact = root.getBoundingClientRect().width <= 960;
    if (compact !== wasCompact) {
      if (!compact || !create.contains(document.activeElement)) create.open = !compact;
      wasCompact = compact;
    }
  }
  fitCreationPanel();
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(fitCreationPanel).observe(root);
  } else {
    window.addEventListener("resize", fitCreationPanel);
  }
  function updateGroups(reset) {
    if (!course || !group) return;
    if (reset) group.value = "";
    Array.from(group.options).forEach(function (option) {
      var eligible = !option.value || !course.value || option.dataset.course === course.value;
      option.hidden = !eligible;
      option.disabled = !eligible;
    });
  }
  updateGroups(false);
  if (course) course.addEventListener("change", function () { updateGroups(true); });
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
