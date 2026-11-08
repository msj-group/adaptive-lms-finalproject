/* The Student reader owns zoom/fullscreen; this adds only local page jumps. */
window.aelmsModules.register(document.currentScript, function(scope) {
  'use strict';
  const reader = document.querySelector('[data-teacher-reader]');
  if (!reader) return;
  const outline = reader.querySelector('[data-focus-outline]');
  const form = reader.querySelector('[data-book-jump]');
  function jump(number) {
    const page = reader.querySelector(`.ce-sheet[data-sheet="${number}"]`);
    if (!page) return;
    outline.open = false;
    page.scrollIntoView({block:'start',behavior:'instant'});
    reader.querySelector('.sf-scroll').focus({preventScroll:true});
  }
  scope.listen(form,'submit',event => { event.preventDefault(); if (form.reportValidity()) jump(Number(form.querySelector('input').value)); });
  scope.listen(reader,'click',event => { const link = event.target.closest('[data-book-page-jump]'); if (!link || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); jump(Number(link.dataset.bookPageJump)); });
});
