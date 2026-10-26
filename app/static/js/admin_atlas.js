/* Presentation only: label native data cells for the Atlas phone composition.
   Filtering, navigation, signed forms and writes remain with existing owners. */
(function () {
  "use strict";
  var root = document.querySelector("body.admin-atlas #workspace-content");
  if (!root) return;
  function placeTabs() {
    var tabs=root.querySelector('.ax-section-tabs,.ax-context-tabs');
    var heading=root.querySelector('[data-workspace-page-heading]');
    if (!tabs || !heading) return;
    var target=heading.closest('.ad-page-head,.ax-identity') || heading;
    if (target.parentElement && target.parentElement.classList.contains('ds-row')) target=target.parentElement;
    if (target.nextElementSibling !== tabs) target.after(tabs);
  }
  function tables(scope) {
    scope.querySelectorAll("table.ad-table,table.table").forEach(function (table) {
      var headers = Array.from(table.querySelectorAll("thead tr:first-child th"));
      if (!headers.length || headers.some(function (cell) { return cell.colSpan !== 1 || cell.rowSpan !== 1; })) return;
      var rows = Array.from(table.querySelectorAll("tbody tr"));
      var simple = rows.every(function (row) {
        return row.cells.length === headers.length && Array.from(row.cells).every(function (cell) { return cell.colSpan === 1 && cell.rowSpan === 1; });
      });
      if (!simple) return;
      rows.forEach(function (row) {
        Array.from(row.cells).forEach(function (cell,index) { cell.dataset.label=headers[index].textContent.trim(); });
      });
      table.classList.add("ad-table","ax-mobile-records");
    });
  }
  tables(root);
  placeTabs();
  var scheduled = false;
  new MutationObserver(function (changes) {
    if (scheduled || !changes.some(function (change) { return change.addedNodes.length; })) return;
    scheduled=true;
    window.requestAnimationFrame(function () { scheduled=false; tables(root); placeTabs(); });
  }).observe(root,{childList:true,subtree:true});
})();
