/* Local presentation over server-authorized civil dates; no source writes. */
(function () {
  "use strict";
  var root = document.querySelector("[data-calendar-workspace]");
  if (!root) return;
  var mobile = window.matchMedia("(max-width:767px)");
  var dates = Array.from(root.querySelectorAll("[data-cal-date]"));
  var days = Array.from(root.querySelectorAll("[data-cal-day]"));
  var weeks = Array.from(root.querySelectorAll("[data-cal-week]"));
  var selected = root.dataset.selectedDate;
  var preferred = root.dataset.defaultView;
  var view = mobile.matches && root.dataset.explicitView !== "true" ? "day" : preferred;
  var filter = root.querySelector("[data-cal-filters]");
  var heading = root.querySelector("[data-cal-selected-heading]");
  var countLabel = root.querySelector("[data-cal-selected-count]");
  var periodLabel = root.querySelector("[data-cal-period-label]");
  var initialPeriodLabel = periodLabel.textContent;
  var live = root.querySelector("[data-cal-live]");
  function selectedWeek() {
    return weeks.findIndex(function (week) { return week.dataset.firstDate <= selected && selected <= week.dataset.lastDate; });
  }
  function entryCount(count) {
    return (count === 1 ? root.dataset.entrySingular : root.dataset.entryPlural).replace("%(count)d", String(count));
  }
  function remember() {
    var url = new URL(window.location.href);
    url.searchParams.set("day", selected);
    url.searchParams.set("view", view);
    window.history.replaceState(null, "", url);
  }
  function render(announce) {
    root.classList.add("is-enhanced");
    root.dataset.currentView = view;
    var index = selectedWeek();
    weeks.forEach(function (week, i) { week.hidden = view !== "month" && i !== index; });
    dates.forEach(function (link) { link.setAttribute("aria-current", link.dataset.calDate === selected ? "date" : "false"); });
    var count = 0;
    days.forEach(function (day) {
      var visible = view === "upcoming" ? day.dataset.calDay >= selected && Number(day.dataset.count) > 0 : day.dataset.calDay === selected;
      day.hidden = !visible;
      if (visible) count += Number(day.dataset.count);
    });
    root.querySelector("[data-cal-upcoming-empty]").hidden = view !== "upcoming" || count > 0;
    var active = days.find(function (day) { return day.dataset.calDay === selected; });
    heading.textContent = view === "upcoming" ? root.dataset.upcomingLabel : active.dataset.dateLabel;
    countLabel.textContent = entryCount(count) + (view !== "upcoming" && selected === root.dataset.today ? " · " + root.dataset.todayLabel : "");
    root.querySelectorAll("[data-cal-view]").forEach(function (button) { button.setAttribute("aria-pressed", String(button.dataset.calView === view)); });
    root.querySelector("[data-cal-day-input]").value = selected;
    root.querySelector("[data-cal-view-input]").value = view;
    periodLabel.textContent = view === "month" ? initialPeriodLabel : active.dataset.periodLabel;
    if (announce) live.textContent = heading.textContent + ", " + countLabel.textContent;
  }
  dates.forEach(function (link) {
    link.addEventListener("click", function (event) {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); selected = link.dataset.calDate;
      if (mobile.matches) view = "day";
      render(true); remember();
    });
  });
  root.querySelectorAll("[data-cal-view]").forEach(function (button) {
    button.addEventListener("click", function () { view = button.dataset.calView; preferred = view; render(true); remember(); });
  });
  function navigateWeek(event, delta) {
    if (view === "month" || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    var index = selectedWeek() + delta;
    if (index >= 0 && index < weeks.length) {
      var link = weeks[index].querySelector("[data-cal-date]");
      if (link) { event.preventDefault(); selected = link.dataset.calDate; render(true); remember(); }
      return;
    }
    var url = new URL(event.currentTarget.href);
    var filters = new FormData(root.querySelector(".calendar-filter-form"));
    url.searchParams.set("kind", filters.get("kind"));
    url.searchParams.set("group", filters.get("group"));
    url.searchParams.set("view", view);
    var boundary = url.searchParams.get(delta > 0 ? "from" : "to");
    if (boundary) url.searchParams.set("day", boundary);
    event.currentTarget.href = url.toString();
  }
  root.querySelector("[data-cal-previous]").addEventListener("click", function (event) { navigateWeek(event, -1); });
  root.querySelector("[data-cal-next]").addEventListener("click", function (event) { navigateWeek(event, 1); });
  root.querySelector("[data-cal-today]").addEventListener("click", function (event) {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (days.some(function (day) { return day.dataset.calDay === root.dataset.today; })) {
      event.preventDefault(); selected = root.dataset.today; render(true); remember();
    }
  });
  document.addEventListener("click", function (event) { if (!filter.contains(event.target)) filter.open = false; });
  filter.addEventListener("keydown", function (event) { if (event.key === "Escape") { filter.open = false; filter.querySelector("summary").focus(); } });
  mobile.addEventListener("change", function () { view = mobile.matches ? (view === "upcoming" ? view : "day") : preferred; render(false); });
  render(false);
})();
