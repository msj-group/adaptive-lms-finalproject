/* Progressive group selection. Native GET links/forms and reply POST own navigation. */
(() => {
  'use strict';
  document.querySelectorAll('[data-discussion-group-form]').forEach((form) => {
    const select = form.querySelector('[data-discussion-group]');
    const apply = form.querySelector('[data-discussion-group-apply]');
    if (!select || !apply || typeof form.requestSubmit !== 'function') return;
    select.addEventListener('change', () => form.requestSubmit());
    apply.hidden = true;
  });
})();
