/* Present genuine server progress once; never record or infer completion. */
window.aelmsModules.register(document.currentScript, function (scope) {
  'use strict';
  const bars = [...document.querySelectorAll('[data-learning-progress]')];
  if (!bars.length || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (!window.IntersectionObserver) return;
  const observer = scope.createObserver(IntersectionObserver, entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.dataset.progressMotion = 'playing';
      observer.unobserve(entry.target);
    });
  }, {threshold:0.3});
  bars.forEach(bar => { bar.dataset.progressMotion = 'waiting'; observer.observe(bar); });
});
