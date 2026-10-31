/* Presentation helpers only. Native forms and the server own every mutation. */
(function () {
  'use strict';
  if (!document.body.classList.contains('rs-suite')) return;
  var searchTrigger = document.querySelector('[data-workspace-search-open]');
  var searchDialog = document.querySelector('[data-workspace-search]');
  if (searchTrigger && searchDialog && typeof searchDialog.showModal === 'function') {
    document.addEventListener('keydown', function (event) {
      if (event.defaultPrevented || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.key.toLowerCase() !== 'k') return;
      if (document.querySelector('dialog[open]')) return;
      event.preventDefault();
      searchTrigger.click();
    });
  }
  var filters = document.querySelector('[data-rs-filters]');
  if (filters) {
    var compact = window.matchMedia('(max-width:599px)');
    var syncFilters = function () { filters.open = !compact.matches || filters.dataset.active === 'true'; };
    compact.addEventListener('change', syncFilters);
    syncFilters();
  }
  document.querySelectorAll('[data-rs-dialog]').forEach(function (trigger) {
    var dialog = document.getElementById(trigger.dataset.rsDialog);
    if (!dialog || typeof dialog.showModal !== 'function') return;
    trigger.hidden = false;
    trigger.addEventListener('click', function () {
      if (dialog.open) return;
      dialog.showModal();
      dialog.addEventListener('close', function restore() {
        if (trigger.isConnected) trigger.focus({preventScroll: true});
        dialog.removeEventListener('close', restore);
      });
    });
  });
  document.querySelectorAll('dialog.rs-dialog').forEach(function (dialog) {
    dialog.querySelectorAll('[data-rs-close]').forEach(function (button) {
      button.addEventListener('click', function () { dialog.close(); });
    });
    dialog.addEventListener('click', function (event) {
      if (event.target !== dialog) return;
      var bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
  });
  // A reviewed export scope must be reviewed again after an edit. This is a
  // convenience guard; creation still validates and snapshots on the server.
  var exportForm = document.querySelector('[data-rs-export-form]');
  if (exportForm) {
    function markSelectionChanged(event) {
      if (!event.target.matches('select,input:not([type=hidden])')) return;
      var review = exportForm.querySelector('[data-rs-export-review]');
      var create = exportForm.querySelector('[data-rs-export-create]');
      if (review) review.hidden = true;
      if (create) create.hidden = true;
      var hint = exportForm.querySelector('[data-rs-review-changed]');
      if (hint) hint.hidden = false;
    }
    exportForm.addEventListener('input', markSelectionChanged);
    exportForm.addEventListener('change', markSelectionChanged);
  }
})();
