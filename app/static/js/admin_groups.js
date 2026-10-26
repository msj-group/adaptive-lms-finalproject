/* Presentation only. Move one existing server-rendered panel/form between its
   native disclosure, a desktop dialog and an in-flow phone reader. No requests,
   writes, cloned forms or business-state decisions belong to this enhancement. */
(function () {
  "use strict";
  var root = document.querySelector("[data-admin-groups]");
  if (!root || !window.matchMedia) return;
  var region = root.querySelector("[data-groups-results]");
  var reader = root.querySelector("[data-groups-reader]");
  var readerContent = root.querySelector("[data-groups-reader-content]");
  var dialog = root.querySelector("[data-groups-dialog]");
  var dialogContent = root.querySelector("[data-groups-dialog-content]");
  if (!region || !reader || !readerContent || !dialog || !dialogContent) return;
  var phone = window.matchMedia("(max-width:767px)");
  var current = null;
  var editing = false;
  var directory = region.querySelector("[data-groups-directory]");

  function restore(focus) {
    var previous = current;
    current = null;
    root.classList.remove("agroups-reading");
    reader.hidden = true;
    if (dialog.open) dialog.close();
    if (!previous) return;
    previous.opener.setAttribute("aria-expanded", "false");
    if (previous.record.isConnected) {
      previous.record.appendChild(previous.panel);
      previous.record.open = false;
    } else previous.panel.remove();
    if (focus && previous.opener.isConnected) previous.opener.focus();
  }
  function decorate() {
    region.querySelectorAll("[data-group-row]").forEach(function (row) {
      var panel = row.querySelector("[data-group-panel]");
      if (!panel) return;
      row.querySelectorAll("[data-groups-open]").forEach(function (opener) {
        opener.setAttribute("aria-controls", panel.id);
        opener.setAttribute("aria-expanded", "false");
        if (phone.matches || !dialog.showModal) opener.removeAttribute("aria-haspopup");
        else opener.setAttribute("aria-haspopup", "dialog");
      });
    });
  }
  function open(row, opener) {
    var record = row.querySelector("[data-group-record]");
    var panel = record && record.querySelector("[data-group-panel]");
    if (!panel || (!phone.matches && !dialog.showModal)) return false;
    restore(false);
    region.querySelectorAll(".agroups-selected").forEach(function (item) { item.classList.remove("agroups-selected"); });
    row.classList.add("agroups-selected");
    current = {record:record, panel:panel, opener:opener, row:row, host:phone.matches ? "phone" : "dialog"};
    opener.setAttribute("aria-expanded", "true");
    if (phone.matches) {
      readerContent.appendChild(panel);
      reader.hidden = false;
      root.classList.add("agroups-reading");
    } else {
      dialogContent.appendChild(panel);
      try { dialog.showModal(); }
      catch (error) { restore(false); return false; }
    }
    var heading = panel.querySelector("h2");
    if (heading) heading.focus();
    return true;
  }
  root.addEventListener("click", function (event) {
    var close = event.target.closest("[data-groups-close]");
    if (close) { restore(true); return; }
    if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var opener = event.target.closest("[data-groups-open]");
    var row = event.target.closest("[data-group-row]");
    if (!opener && row && phone.matches && !event.target.closest("a,button,input,select,textarea,summary,[data-group-panel]")) opener = row.querySelector("summary[data-groups-open]");
    if (opener && row && open(row,opener)) event.preventDefault();
  });
  root.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && current && phone.matches && !dialog.open && !root.querySelector('[data-group-create-dialog][open]')) {
      event.preventDefault(); restore(true);
    }
  });
  dialog.addEventListener("close", function () { if (!editing && !dialog.open && current && current.host === "dialog") restore(true); });
  dialog.addEventListener("cancel", function (event) { event.preventDefault(); restore(true); });
  root.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.matches(".group-status-form[data-confirm]") && !window.aelmsConfirm && !window.confirm(form.dataset.confirm)) event.preventDefault();
  });
  new MutationObserver(function () {
    var incoming = region.querySelector("[data-groups-directory]");
    if (region.getAttribute("aria-busy") === "true" || incoming !== directory) {
      restore(false);
      directory = incoming;
      decorate();
    }
  }).observe(region,{childList:true,attributes:true,attributeFilter:["aria-busy"]});
  function resize() {
    if (editing) return;
    if (current) {
      var selected = current;
      restore(false);
      if (selected.row.isConnected) open(selected.row,selected.opener);
    }
    decorate();
  }
  // Suspend the desktop reader while editing, so only one feature dialog is
  // visible. Keep its panel in place for Cancel and defer breakpoint transfer.
  root.addEventListener("aelms:group-editor-open", function () {
    editing = true;
    if (dialog.open) dialog.close();
  });
  root.addEventListener("aelms:group-editor-close", function () {
    editing = false;
    if (!current) return;
    if (current.host !== (phone.matches ? "phone" : "dialog")) resize();
    else if (current.host === "dialog" && !dialog.open) dialog.showModal();
  });
  if (phone.addEventListener) phone.addEventListener("change",resize);
  else if (phone.addListener) phone.addListener(resize);
  window.addEventListener("pagehide",function () { restore(false); });
  decorate();
})();
