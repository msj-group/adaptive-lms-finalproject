/* Atlas presentation only. Native links, lesson POSTs and media endpoints own data. */
(function () {
  'use strict';
  if (!document.body.classList.contains('student-atlas')) return;

  function openUnit(hash) {
    if (!hash || !hash.startsWith('#unit-')) return;
    var target;
    try { target = document.getElementById(decodeURIComponent(hash.slice(1))); }
    catch (_) { return; }
    if (target && target.matches('details.atlas-unit')) target.open = true;
  }
  openUnit(window.location.hash);
  window.addEventListener('hashchange', function () { openUnit(window.location.hash); });
  document.querySelectorAll('.atlas-unit-index a').forEach(function (link) {
    link.addEventListener('click', function () { openUnit(link.hash); });
  });

  var reader = document.querySelector('[data-atlas-reader]');
  if (!reader) return;
  var outline = reader.querySelector('[data-atlas-outline]');
  if (outline) {
    var previousWide;
    function fitOutline() {
      var wide = reader.getBoundingClientRect().width >= 760;
      if (wide === previousWide) return;
      previousWide = wide;
      if (!outline.contains(document.activeElement)) outline.open = wide;
    }
    fitOutline();
    if (window.ResizeObserver) new ResizeObserver(fitOutline).observe(reader);
    else window.addEventListener('resize', fitOutline);
  }

  var toolbar = reader.querySelector('[data-atlas-material-toolbar]');
  var materials = Array.from(reader.querySelectorAll('[data-atlas-material]'));
  if (!toolbar || !materials.length) return;
  var buttons = Array.from(toolbar.querySelectorAll('[data-atlas-filter]'));
  var status = toolbar.querySelector('[data-atlas-material-status]');
  var kinds = new Set(materials.map(function (item) { return item.dataset.atlasMaterial; }));
  if (kinds.size < 2) return;
  buttons.forEach(function (button) {
    button.hidden = button.dataset.atlasFilter !== 'all' && !kinds.has(button.dataset.atlasFilter);
  });
  function showKind(kind) {
    var shown = 0;
    materials.forEach(function (item) {
      item.hidden = kind !== 'all' && item.dataset.atlasMaterial !== kind;
      if (item.hidden) item.querySelectorAll('audio,video').forEach(function (media) { media.pause(); });
      else shown += 1;
    });
    buttons.forEach(function (button) { button.setAttribute('aria-pressed', String(button.dataset.atlasFilter === kind)); });
    if (status) status.textContent = shown === 1 ? status.dataset.single : status.dataset.plural.replace('{count}', String(shown));
  }
  toolbar.addEventListener('click', function (event) {
    var button = event.target.closest('button[data-atlas-filter]');
    if (button && toolbar.contains(button)) showKind(button.dataset.atlasFilter);
  });
  function revealMaterialHash() {
    var target;
    try { target = document.getElementById(decodeURIComponent(window.location.hash.slice(1))); }
    catch (_) { return; }
    if (target && materials.some(function (item) { return item === target || item.contains(target); })) {
      if (target.closest('[data-atlas-material]').hidden) showKind('all');
    }
  }
  window.addEventListener('hashchange', revealMaterialHash);
  toolbar.hidden = false;
  showKind('all');
}());
