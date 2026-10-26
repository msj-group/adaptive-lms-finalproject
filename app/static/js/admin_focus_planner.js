/* Focus planner presentation only. Native GET filters/management keep their owners. */
(function () {
  "use strict";
  document.querySelectorAll(".fp-page .fp-filters").forEach(function (filter) {
    document.addEventListener("click", function (event) { if (!filter.contains(event.target) && !event.target.closest("[data-fp-mobile-filters]")) filter.open = false; });
    filter.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { filter.open = false; filter.querySelector("summary").focus(); }
    });
  });
  var mobileFilter = document.querySelector("[data-fp-mobile-filters]");
  var filterHost = document.querySelector("[data-fp-mobile-filter-host]");
  var scheduleFilters = document.getElementById("focus-schedule-filters");
  if (mobileFilter && filterHost && scheduleFilters) {
    var filterMedia = window.matchMedia("(max-width:767px)");
    var filterPlace = document.createComment("Native schedule filter location");
    scheduleFilters.before(filterPlace);
    function placeFilters() {
      scheduleFilters.open = false;
      mobileFilter.hidden = !filterMedia.matches;
      if (filterMedia.matches) filterHost.appendChild(scheduleFilters);
      else filterPlace.after(scheduleFilters);
      mobileFilter.setAttribute("aria-expanded", "false");
    }
    mobileFilter.addEventListener("click", function () { scheduleFilters.open = !scheduleFilters.open; });
    scheduleFilters.addEventListener("toggle", function () { mobileFilter.setAttribute("aria-expanded", String(scheduleFilters.open)); });
    scheduleFilters.addEventListener("keydown", function (event) { if (event.key === "Escape" && filterMedia.matches) mobileFilter.focus(); });
    filterMedia.addEventListener("change", placeFilters);
    placeFilters();
  }
  var root = document.querySelector("[data-admin-focus-planner]");
  if (!root) return;
  var dates = Array.from(root.querySelectorAll("[data-fp-date]"));
  var days = Array.from(root.querySelectorAll("[data-fp-day]"));
  var weeks = Array.from(root.querySelectorAll("[data-fp-week]"));
  var mobile = window.matchMedia("(max-width:767px)");
  var selected = root.dataset.selectedDate;
  var view = root.dataset.view === "upcoming" ? "upcoming" : "day";
  function weekIndex() { return weeks.findIndex(function (week) { return week.dataset.firstDate <= selected && selected <= week.dataset.lastDate; }); }
  function ordinaryClick(event) { return event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey; }
  function remember() {
    var url = new URL(window.location.href);
    url.searchParams.set("day", selected); url.searchParams.set("view", view);
    window.history.replaceState(null, "", url);
  }
  function render(announce) {
    var active = days.find(function (day) { return day.dataset.fpDay === selected; });
    if (!active) return;
    root.classList.add("is-enhanced"); root.dataset.view = view;
    var index = weekIndex(), count = 0;
    weeks.forEach(function (week, current) { week.hidden = current !== index; });
    dates.forEach(function (link) { link.setAttribute("aria-current", link.dataset.fpDate === selected ? "date" : "false"); });
    days.forEach(function (day) {
      var visible = view === "upcoming" ? day.dataset.fpDay >= selected && Number(day.dataset.count) > 0 : day.dataset.fpDay === selected;
      day.hidden = !visible;
      if (visible) count += Number(day.dataset.count);
    });
    root.querySelector("[data-fp-upcoming-empty]").hidden = view !== "upcoming" || count > 0;
    root.querySelector("[data-fp-day-number]").textContent = active.dataset.dayNumber;
    root.querySelector("[data-fp-day-number]").hidden = view === "upcoming";
    root.querySelector("[data-fp-heading]").textContent = view === "upcoming" ? root.dataset.upcomingLabel : active.dataset.weekday;
    root.querySelector("[data-fp-heading-meta]").textContent = active.dataset.month + (view === "day" && selected === root.dataset.today ? " · " + root.dataset.todayLabel : "");
    var label = (count === 1 ? root.dataset.entrySingular : root.dataset.entryPlural).replace("%(count)d", String(count));
    root.querySelector("[data-fp-count]").textContent = label;
    root.querySelector("[data-fp-day-input]").value = selected;
    root.querySelector("[data-fp-view-input]").value = view;
    root.querySelectorAll("[data-fp-view]").forEach(function (button) { button.setAttribute("aria-pressed", String(button.dataset.fpView === view)); });
    if (announce) root.querySelector("[data-fp-live]").textContent = root.querySelector("[data-fp-heading]").textContent + ", " + label;
  }
  dates.forEach(function (link) {
    link.addEventListener("click", function (event) {
      if (!ordinaryClick(event)) return;
      event.preventDefault(); selected = link.dataset.fpDate; view = "day"; render(true); remember();
    });
  });
  root.querySelectorAll("[data-fp-view]").forEach(function (button) {
    button.addEventListener("click", function () { view = button.dataset.fpView; render(true); remember(); });
  });
  function navigate(event, delta) {
    if (!ordinaryClick(event)) return;
    if (mobile.matches) {
      var next = weeks[weekIndex() + delta];
      var link = next && next.querySelector("[data-fp-date]");
      if (link) { event.preventDefault(); selected = link.dataset.fpDate; render(true); remember(); return; }
    }
    var url = new URL(event.currentTarget.href); url.searchParams.set("view", view);
    event.currentTarget.href = url.toString();
  }
  root.querySelector("[data-fp-previous]").addEventListener("click", function (event) { navigate(event, -1); });
  root.querySelector("[data-fp-next]").addEventListener("click", function (event) { navigate(event, 1); });
  root.querySelector("[data-fp-today]").addEventListener("click", function (event) {
    if (!ordinaryClick(event)) return;
    if (days.some(function (day) { return day.dataset.fpDay === root.dataset.today; })) {
      event.preventDefault(); selected = root.dataset.today; view = "day"; render(true); remember();
    } else { var url = new URL(event.currentTarget.href); url.searchParams.set("view", "day"); event.currentTarget.href = url.toString(); }
  });
  root.querySelectorAll("[data-fp-entry]").forEach(function (entry) {
    entry.addEventListener("toggle", function () {
      if (!entry.open) return;
      entry.closest(".fp-day").querySelectorAll("[data-fp-entry]").forEach(function (other) { if (other !== entry) other.open = false; });
    });
  });
  window.addEventListener("popstate", function () {
    var url = new URL(window.location.href), day = url.searchParams.get("day");
    if (days.some(function (item) { return item.dataset.fpDay === day; })) { selected = day; view = url.searchParams.get("view") === "upcoming" ? "upcoming" : "day"; render(false); }
  });
  render(false);
})();
