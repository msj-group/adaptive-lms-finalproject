/* Page resource scope. */
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
/* Progressive group selection. Native GET links/forms and reply POST own navigation. */
(() => {
  'use strict';
  document.querySelectorAll('[data-discussion-group-form]').forEach((form) => {
    const select = form.querySelector('[data-discussion-group]');
    const apply = form.querySelector('[data-discussion-group-apply]');
    if (!select || !apply || typeof form.requestSubmit !== 'function') return;
    pageScope.listen(select, 'change', () => form.requestSubmit());
    apply.hidden = true;
  });
})();

});
